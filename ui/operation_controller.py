"""Single file/analysis worker, isolated from the capture worker and Qt widgets."""

from dataclasses import dataclass
import logging
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QObject, Qt, Signal, Slot

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled, check_cancel
from audio.sources import AudioSource
from audio.waveio import write_wav
from replay.analyzer import AnalysisResult, Analyzer
from detector.classifier import ClassificationResult, OracleClassifier

logger = logging.getLogger("oracle_assistant.replay")


@dataclass(frozen=True, slots=True)
class OperationTask:
    kind: str
    source: AudioSource | None = None
    path: Path | None = None
    clip: AudioClip | None = None
    encoding: str = "float32"


@dataclass(frozen=True, slots=True)
class OperationResult:
    kind: str
    analysis: AnalysisResult | None = None
    path: Path | None = None
    error: str = ""
    cancelled: bool = False
    classification: ClassificationResult | None = None


class OperationController(QObject):
    finished = Signal(object)
    busy_changed = Signal(bool)

    def __init__(
        self, sample_rate: int = 48000, parent: QObject | None = None,
        analyzer: Analyzer | None = None, classifier: OracleClassifier | None = None,
    ) -> None:
        super().__init__(parent)
        self.analyzer = analyzer or Analyzer(sample_rate)
        self.classifier = classifier
        self._queue: Queue[OperationTask] = Queue()
        self._closing = Event()
        self._thread: Thread | None = None
        self._busy = False
        self.finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def busy(self) -> bool:
        return self._busy

    def analyze(self, source: AudioSource, live: bool = False) -> bool:
        return self._submit(OperationTask("live" if live else "wave", source=source))

    def dump(self, source: AudioSource, path: Path) -> bool:
        return self._submit(OperationTask("dump", source=source, path=path))

    def save(self, clip: AudioClip, path: Path, encoding: str = "float32") -> bool:
        return self._submit(OperationTask("save", clip=clip, path=path, encoding=encoding))

    def _submit(self, task: OperationTask) -> bool:
        if self._closing.is_set() or self._busy:
            return False
        self._busy = True
        self.busy_changed.emit(True)
        self._queue.put(task)
        if self._thread is None:
            self._thread = Thread(target=self._run, name="OracleReplayWorker", daemon=False)
            self._thread.start()
        return True

    @Slot(object)
    def _finish(self, event: OperationResult) -> None:
        self._busy = False
        self.busy_changed.emit(False)

    def shutdown(self, timeout: float = 0) -> bool:
        self._closing.set()
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
                check_cancel(self._closing)
                if task.kind in ("wave", "live"):
                    analysis = self.analyzer.analyze(task.source, self._closing)
                    classification = (self.classifier.classify_preprocessed(analysis.processed, self._closing)
                                      if self.classifier is not None else None)
                    event = OperationResult(task.kind, analysis=analysis, classification=classification)
                    if classification is not None:
                        logger.info("Waveform ranking: status=%s oracle=%s best=%s second=%s score=%s samples=%s",
                                    classification.status, classification.oracle, classification.best_score,
                                    classification.second_candidate, classification.second_score, classification.sample_count)
                    logger.info(
                        "Audio analyzed: %s Hz / %s ch -> %s Hz / 1 ch, %s frames, sha256=%s",
                        analysis.original.sample_rate, analysis.original.channels,
                        analysis.processed.sample_rate, analysis.processed.frame_count, analysis.checksum,
                    )
                else:
                    clip = task.source.read(self._closing) if task.kind == "dump" else task.clip
                    path = write_wav(task.path, clip, task.encoding, self._closing)
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
