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
from detector.confidence import (
    ConfidenceEngine, ConfidenceLevel, DetectionResult,
    can_fill_matched_position, can_start_presentation,
)
from detector.events import RmsEventDetector
from detector.onset_matcher import build_optional_onset_matcher
from encounter.sequence import SequenceEngine, SequenceSnapshot, SequenceEntry, SequenceState
from logging_ext.recognition_logger import RecognitionRecorder
from replay.analyzer import AnalysisResult, Analyzer
from logging_ext.evidence import CueEvidence,CueEvidenceCache

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
    onset_matched: bool = False
    onset_match_oracle: str | None = None
    onset_match_score: float | None = None
    onset_match_margin: float | None = None


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
        onset_matcher = build_optional_onset_matcher(
            self.classifier, original.sample_rate, original.channels, cancel=cancel,
        )
        engine.arm(base, round_index)
        if progress:
            progress(engine.snapshot())
        traces, notices = [], []
        evidence = CueEvidenceCache()
        for window in detector.detect(original, cancel, onset_matcher):
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
            if (
                onset_matcher is not None
                and window.matched
                and before.state == SequenceState.PASS_1
                and engine.can_restart_incomplete_pass(onset)
                and not window.reason
            ):
                probe = ConfidenceEngine(self.recognition).evaluate(
                    classification, context,
                )
                if can_fill_matched_position(probe, True):
                    before = engine.restart_incomplete_pass(onset)
                    confidence.reset()
                    evidence.clear()

            evaluation = (
                ConfidenceEngine(self.recognition)
                if window.reason else confidence
            )
            detection = evaluation.evaluate(classification, context)
            if window.reason:
                detection = replace(
                    detection, oracle=None,
                    status=ConfidenceLevel.REJECTED,
                    reason=window.reason,
                )
            in_progress = before.state in (
                SequenceState.PASS_1, SequenceState.PASS_2,
            )
            matcher_eligible = (
                onset_matcher is None
                or can_fill_matched_position(detection, window.matched)
            )
            candidate = matcher_eligible and (
                in_progress
                or can_start_presentation(
                    detection, self.recognition.low_score_threshold
                )
            )
            sequence_context = None
            if candidate:
                snapshot = engine.add(SequenceEntry(detection, classification, signal_end))
                if before.state != SequenceState.WAIT_PASS_2 and snapshot.state == SequenceState.WAIT_PASS_2:
                    # PASS2 repeats PASS1; temporal duplicate history belongs to one presentation.
                    confidence.reset()
                pass_number = (
                    2 if before.state in (SequenceState.WAIT_PASS_2, SequenceState.PASS_2) else 1
                )
                index = len(before.pass2 if pass_number == 2 else before.pass1) + 1
                sequence_context = {
                    "round": round_index,
                    "pass": pass_number,
                    "index": index,
                    "state": snapshot.state.value,
                    "reason": snapshot.reason,
                }
                if self.recorder:
                    evidence.add(CueEvidence(
                        window.clip, cue_analysis.checksum, detection, classification,
                        round_index, pass_number, index,
                        window.onset_detection,
                    ))
            else:
                snapshot = engine.ignore(signal_end)
            traces.append(CueTrace(
                window.start_frame, window.end_frame, window.onset_frame,
                window.signal_end_frame, detection, classification,
                window.matched, window.match_oracle,
                window.match_score, window.match_margin,
            ))
            if self.recorder:
                persisted = self.recorder.record(
                    detection, classification, window.clip, cue_analysis.checksum, cancel,
                    sequence=sequence_context,
                    onset_detection=window.onset_detection,
                )
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
            notices.extend(evidence.save_problem(snapshot,self.recorder,cancel))
            saved = self.recorder.record_sequence(snapshot, analysis.checksum, cancel)
            notices.extend(saved.notices)
        if progress:
            progress(snapshot)
        return SequenceAnalysisResult(snapshot, tuple(traces), tuple(dict.fromkeys(notices)))
