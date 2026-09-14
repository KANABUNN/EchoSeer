"""Single file/analysis worker, isolated from the capture worker and Qt widgets."""

from dataclasses import dataclass, replace
import logging
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QObject, Qt, Signal, Slot

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled, check_cancel
from audio.sources import AudioSource
from audio.playback import AudioPlayer
from evaluation.dataset import load_dataset,read_json
from review.cases import ReviewCaseStore
from review.logs import LogCatalog,load_logs,audio_path
from evaluation.runner import evaluate,compare_reports,export_report,MAX_REPORT_BYTES
from replay.timeline import ReplayTimelineAnalyzer, TimelineResult
from audio.waveio import write_wav
from replay.analyzer import AnalysisResult, Analyzer
from replay.sequence_analyzer import ReplaySequenceAnalyzer, SequenceAnalysisResult
from config.schema import SequenceSettings
from detector.classifier import ClassificationResult, OracleClassifier
from detector.confidence import ConfidenceEngine, DetectionResult, EventContext
from logging_ext.recognition_logger import RecognitionRecorder, PersistenceResult

logger = logging.getLogger("oracle_assistant.replay")


@dataclass(frozen=True, slots=True)
class OperationTask:
    kind: str
    source: AudioSource | None = None
    path: Path | None = None
    clip: AudioClip | None = None
    encoding: str = "float32"
    round_index: int = 1
    volume: float = .2
    baseline: Path | None = None
    report: dict | None = None
    annotation: dict | None = None
    observed: dict | None = None
    bounds: tuple | None = None
    relative_audio: str | None = None
    expected_audio_checksum: str | None = None


@dataclass(frozen=True, slots=True)
class OperationResult:
    kind: str
    analysis: AnalysisResult | None = None
    path: Path | None = None
    error: str = ""
    cancelled: bool = False
    classification: ClassificationResult | None = None
    detection: DetectionResult | None = None
    persistence: PersistenceResult | None = None
    sequence: SequenceAnalysisResult | None = None
    timeline: TimelineResult | None = None
    report: dict | None = None
    log_catalog: LogCatalog | None = None


class OperationController(QObject):
    finished = Signal(object)
    busy_changed = Signal(bool)
    sequence_progress = Signal(object)
    evaluation_progress = Signal(int,str)

    def __init__(
        self, sample_rate: int = 48000, parent: QObject | None = None,
        analyzer: Analyzer | None = None, classifier: OracleClassifier | None = None,
        confidence: ConfidenceEngine | None = None, recorder: RecognitionRecorder | None = None,
        sequence_settings: SequenceSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.analyzer = analyzer or Analyzer(sample_rate)
        self.classifier = classifier
        self.confidence = confidence or ConfidenceEngine()
        self.recorder = recorder
        self.sequence_settings = replace(sequence_settings or SequenceSettings())
        self._queue: Queue[OperationTask] = Queue()
        self._closing = Event()
        self._thread: Thread | None = None
        self._busy = False
        self.player = AudioPlayer()
        self._play_stop = Event()
        self._task_cancel = Event()
        self.playing = False
        self.finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def busy(self) -> bool:
        return self._busy

    def analyze(self, source: AudioSource, live: bool = False) -> bool:
        return self._submit(OperationTask("live" if live else "wave", source=source))

    def analyze_sequence(self, source: AudioSource, round_index: int = 1, live: bool = False) -> bool:
        return self._submit(OperationTask("live_sequence" if live else "sequence", source=source, round_index=round_index))

    def save_review_case(self,source,path,annotation,observed=None,bounds=None):
        return self._submit(OperationTask("case_save",source=source,path=path,annotation=annotation,observed=observed,bounds=bounds))

    def snapshot_review_cases(self,path):
        return self._submit(OperationTask("case_dataset",path=path))

    def read_logs(self,path):
        return self._submit(OperationTask("logs",path=path))

    def analyze_log_audio(self,root,relative,expected_checksum=None):
        return self._submit(OperationTask("log_audio",path=root,relative_audio=relative,expected_audio_checksum=expected_checksum))

    def evaluate_dataset(self,path,baseline=None):
        return self._submit(OperationTask("dataset",path=path,baseline=baseline))

    def export_evaluation(self,report,path):
        return self._submit(OperationTask("report_export",path=path,report=report))

    def cancel_current(self):
        self._task_cancel.set()
        self._play_stop.set()

    def analyze_timeline(self, source):
        return self._submit(OperationTask("timeline", source=source))

    def listen(self, clip, volume=.2):
        if self.busy:
            return False
        self._play_stop.clear()
        self.playing = True
        return self._submit(OperationTask("listen", clip=clip, volume=volume))

    def stop_playback(self):
        self._play_stop.set()

    def dump(self, source: AudioSource, path: Path) -> bool:
        return self._submit(OperationTask("dump", source=source, path=path))

    def save(self, clip: AudioClip, path: Path, encoding: str = "float32") -> bool:
        return self._submit(OperationTask("save", clip=clip, path=path, encoding=encoding))

    def _submit(self, task: OperationTask) -> bool:
        if self._closing.is_set() or self._busy:
            return False
        self._task_cancel.clear()
        self._busy = True
        self.busy_changed.emit(True)
        self._queue.put(task)
        if self._thread is None:
            self._thread = Thread(target=self._run, name="OracleReplayWorker", daemon=False)
            self._thread.start()
        return True

    @Slot(object)
    def _finish(self, event: OperationResult) -> None:
        self.playing = False
        self._busy = False
        self.busy_changed.emit(False)

    def shutdown(self, timeout: float = 0) -> bool:
        self._closing.set()
        self._task_cancel.set()
        self._play_stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.is_alive

    def _run(self) -> None:
        while not self._closing.is_set():
            try:
                task = self._queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                check_cancel(self._task_cancel)
                if task.kind == "case_save":
                    path=ReviewCaseStore(task.path).save(task.source,task.annotation,task.observed,self._task_cancel,task.bounds)
                    event=OperationResult(task.kind,path=path)
                elif task.kind == "case_dataset":
                    event=OperationResult(task.kind,path=ReviewCaseStore(task.path).snapshot_dataset(self._task_cancel))
                elif task.kind == "logs":
                    event=OperationResult(task.kind,log_catalog=load_logs(task.path,self._task_cancel))
                elif task.kind == "dataset":
                    report=evaluate(load_dataset(task.path),self.classifier or OracleClassifier(sample_rate=self.analyzer.sample_rate),
                        self.confidence.settings,self._task_cancel,self.evaluation_progress.emit,self.sequence_settings)
                    if task.baseline is not None:
                        report["comparison"]=compare_reports(report,read_json(task.baseline,MAX_REPORT_BYTES))
                    event=OperationResult(task.kind,report=report)
                elif task.kind == "report_export":
                    event=OperationResult(task.kind,path=export_report(task.report,task.path,self._task_cancel))
                elif task.kind in ("wave", "live", "sequence", "live_sequence", "timeline","log_audio"):
                    source=task.source
                    if task.kind=="log_audio":
                        from audio.sources import WaveFileSource
                        source=WaveFileSource(audio_path(task.path,task.relative_audio))
                    analysis = self.analyzer.analyze(source,self._task_cancel)
                    if task.kind=="log_audio" and task.expected_audio_checksum is not None:
                        from templates.manager import audio_checksum
                        if audio_checksum(analysis.original)!=task.expected_audio_checksum:
                            raise AudioDataError("保存ログ音声のchecksumが異なります。元の音声を確認してください。")
                    if task.kind == "timeline":
                        pipeline = ReplayTimelineAnalyzer(self.classifier or OracleClassifier(sample_rate=self.analyzer.sample_rate),
                            self.confidence.settings, self.analyzer, self.recorder)
                        timeline = pipeline.analyze(analysis, self._task_cancel)
                        last = timeline.traces[0] if timeline.traces else None
                        classification, detection = (last.classification, last.detection) if last else (None, None)
                        event = OperationResult(task.kind, analysis=analysis, classification=classification,
                            detection=detection, timeline=timeline, persistence=PersistenceResult(notices=timeline.notices))
                    elif task.kind in ("sequence", "live_sequence"):
                        pipeline = ReplaySequenceAnalyzer(
                            self.classifier or OracleClassifier(sample_rate=self.analyzer.sample_rate),
                            self.confidence.settings, self.sequence_settings, self.recorder, self.analyzer)
                        sequence = pipeline.analyze(analysis, task.round_index, self._task_cancel,
                                                    self.sequence_progress.emit,
                                                    getattr(task.source, "event_context", None)
                                                    if task.kind == "live_sequence" else None)
                        last = sequence.traces[-1] if sequence.traces else None
                        classification = last.classification if last else None
                        detection = last.detection if last else None
                        persistence = PersistenceResult(notices=sequence.notices)
                        event = OperationResult(task.kind, analysis=analysis, classification=classification,
                                                detection=detection, persistence=persistence, sequence=sequence)
                    else:
                        classification = (self.classifier.classify_preprocessed(analysis.processed, self._task_cancel)
                                          if self.classifier is not None else None)
                        detection = persistence = None
                        if classification is not None:
                            context = (getattr(task.source, "event_context", None) if task.kind == "live"
                                       else EventContext(timestamp=analysis.original.duration_seconds,
                                                         start_frame=0, end_frame=analysis.original.frame_count))
                            if task.kind == "live" and context is None:
                                raise ValueError("Live confidence requires timestamped audio")
                            detection = self.confidence.evaluate(classification, context)
                            if self.recorder is not None:
                                persistence = self.recorder.record(detection, classification, analysis.original,
                                                                   analysis.checksum, self._task_cancel)
                            logger.debug("Confidence: level=%s oracle=%s reason=%s duplicate=%s",
                                         detection.status, detection.oracle, detection.reason, detection.duplicate)
                        event = OperationResult(task.kind, analysis=analysis, classification=classification,
                                                detection=detection, persistence=persistence)
                    if classification is not None:
                        logger.debug("Combined ranking: status=%s oracle=%s best=%s second=%s score=%s samples=%s",
                                    classification.status, classification.oracle, classification.best_score,
                                    classification.second_candidate, classification.second_score, classification.sample_count)
                    if classification is not None and classification.ranking:
                        first = classification.ranking[0]
                        logger.debug("Score components: waveform=%s spectrum=%s combined=%s weights=%s/%s",
                                     first.waveform_score, first.spectrum_score, first.combined_score,
                                     classification.waveform_weight, classification.spectrum_weight)
                    logger.info(
                        "Audio analyzed: %s Hz / %s ch -> %s Hz / 1 ch, %s frames, sha256=%s",
                        analysis.original.sample_rate, analysis.original.channels,
                        analysis.processed.sample_rate, analysis.processed.frame_count, analysis.checksum,
                    )
                elif task.kind == "listen":
                    classification = None
                    self.player.play(task.clip, task.volume, self._play_stop)
                    event = OperationResult(task.kind)
                else:
                    clip = task.source.read(self._task_cancel) if task.kind == "dump" else task.clip
                    path = write_wav(task.path, clip, task.encoding, self._task_cancel)
                    event = OperationResult(task.kind, path=path)
                    logger.info("Audio saved: %s (%s)", path, task.encoding)
            except OperationCancelled:
                event = OperationResult(task.kind, cancelled=True)
            except Exception as error:
                logger.exception("Replay/file operation failed")
                if isinstance(error, AudioDataError):
                    message = str(error)
                elif isinstance(error, FileNotFoundError):
                    message = "ファイルまたは保存先が見つかりません。再選択してください。"
                elif isinstance(error, OSError):
                    message = "ファイルの読み込み・保存に失敗しました。保存先・アクセス権・空き容量を確認してください。"
                else:
                    message = "音声の処理に失敗しました。診断ログを確認してください。"
                event = OperationResult(task.kind, error=message)
            self.finished.emit(event)
