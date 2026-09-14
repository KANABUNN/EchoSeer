"""One finite WAV/live snapshot -> event windows -> confidence -> independent VoG passes."""
from dataclasses import dataclass, replace
import logging
from threading import Event
from collections.abc import Callable
from audio.data import AudioDataError
from audio.event import EventContext
from audio.operations import check_cancel
from audio.sources import ClipSource
from config.schema import RecognitionSettings, SequenceSettings
from detector.classifier import ClassificationResult, OracleClassifier
from detector.confidence import ConfidenceEngine, ConfidenceLevel, DetectionResult
from detector.events import RmsEventDetector
from encounter.sequence import SequenceEngine, SequenceSnapshot, SequenceEntry, SequenceState
from logging_ext.recognition_logger import RecognitionRecorder
from replay.analyzer import AnalysisResult, Analyzer

logger = logging.getLogger("oracle_assistant.sequence")
MAX_SEQUENCE_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class CueTrace:
    start_frame: int
    end_frame: int
    onset_frame: int
    signal_end_frame: int
    detection: DetectionResult
    classification: ClassificationResult


@dataclass(frozen=True, slots=True)
class SequenceAnalysisResult:
    snapshot: SequenceSnapshot
    traces: tuple[CueTrace, ...]
    notices: tuple[str, ...] = ()


class ReplaySequenceAnalyzer:
    def __init__(self, classifier: OracleClassifier, recognition: RecognitionSettings | None = None,
                 sequence: SequenceSettings | None = None, recorder: RecognitionRecorder | None = None,
                 analyzer: Analyzer | None = None) -> None:
        self.classifier = classifier
        self.recognition = recognition or RecognitionSettings()
        self.sequence_settings = sequence
        self.recorder = recorder
        self.analyzer = analyzer or Analyzer(classifier.sample_rate)

    def analyze(self, analysis: AnalysisResult, round_index: int = 1, cancel: Event | None = None,
                progress: Callable[[SequenceSnapshot], None] | None = None,
                source_context: EventContext | None = None) -> SequenceAnalysisResult:
        original = analysis.original
        if original.duration_seconds > MAX_SEQUENCE_SECONDS:
            raise AudioDataError("順序解析は 120 秒以内の 1 ラウンドを選んでください。")
        live = source_context is not None and source_context.source == "live"
        base = (source_context.timestamp - original.duration_seconds) if live else 0.0
        offset = source_context.start_frame if live else 0
        engine = SequenceEngine(self.sequence_settings)
        confidence = ConfidenceEngine(self.recognition)
        detector = RmsEventDetector(self.recognition.detection_threshold)
        engine.arm(base, round_index)
        if progress:
            progress(engine.snapshot())
        traces, notices = [], []
        for window in detector.detect(original, cancel):
            check_cancel(cancel)
            onset = base + window.onset_frame / original.sample_rate
            signal_end = base + window.signal_end_frame / original.sample_rate
            engine.advance(onset, silent=True)
            before = engine.snapshot()
            if before.state not in (SequenceState.ARMED, SequenceState.PASS_1, SequenceState.WAIT_PASS_2, SequenceState.PASS_2):
                break
            cue_analysis = self.analyzer.analyze(ClipSource(window.clip), cancel)
            classification = self.classifier.classify_preprocessed(cue_analysis.processed, cancel)
            context = EventContext("live" if live else "replay", onset,
                                   source_context.stream_id if live else None,
                                   offset + window.start_frame, offset + window.end_frame)
            detection = confidence.evaluate(classification, context)
            if window.reason:
                detection = replace(detection, oracle=None, status=ConfidenceLevel.REJECTED, reason=window.reason)
            snapshot = engine.add(SequenceEntry(detection, classification, signal_end))
            if before.state != SequenceState.WAIT_PASS_2 and snapshot.state == SequenceState.WAIT_PASS_2:
                # PASS2 repeats PASS1; temporal duplicate history belongs to one presentation.
                confidence.reset()
            traces.append(CueTrace(window.start_frame, window.end_frame, window.onset_frame,
                                   window.signal_end_frame, detection, classification))
            if self.recorder:
                pass_number = 2 if before.state in (SequenceState.WAIT_PASS_2, SequenceState.PASS_2) else 1
                index = len(before.pass2 if pass_number == 2 else before.pass1) + 1
                persisted = self.recorder.record(detection, classification, window.clip, cue_analysis.checksum, cancel,
                                                sequence={"round": round_index, "pass": pass_number, "index": index,
                                                          "state": snapshot.state.value, "reason": snapshot.reason})
                notices.extend(persisted.notices)
            if progress:
                progress(snapshot)
            engine.advance(signal_end, silent=True)
            if snapshot.state in (SequenceState.VERIFY, SequenceState.UNCERTAIN):
                break
        check_cancel(cancel)
        engine.finish(base + original.duration_seconds)
        snapshot = engine.verify(self.recognition, cancel)
        logger.debug("Sequence: round=%s state=%s reason=%s pass1=%s pass2=%s",
                     snapshot.round_index, snapshot.state, snapshot.reason, len(snapshot.pass1), len(snapshot.pass2))
        if self.recorder:
            saved = self.recorder.record_sequence(snapshot, analysis.checksum, cancel)
            notices.extend(saved.notices)
        if progress:
            progress(snapshot)
        return SequenceAnalysisResult(snapshot, tuple(traces), tuple(dict.fromkeys(notices)))
