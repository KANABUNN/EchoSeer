"""Worker-owned continuous VoG recognition, independent of Qt and capture callbacks."""
from dataclasses import dataclass, replace
from hashlib import sha256
from threading import Event
from collections.abc import Callable

from audio.event import EventContext
from audio.operations import check_cancel
from audio.sources import ClipSource
from config.schema import RecognitionSettings, SequenceSettings
from detector.confidence import (
    ConfidenceEngine, ConfidenceLevel, can_fill_matched_position,
    can_start_presentation,
)
from detector.onset_matcher import (
    OnsetMatcherResourceLimit, build_optional_onset_matcher,
)
from detector.streaming import StreamingRmsDetector
from encounter.sequence import SequenceEngine, SequenceEntry, SequenceSnapshot, SequenceState
from replay.analyzer import Analyzer
from logging_ext.evidence import CueEvidence,CueEvidenceCache


@dataclass(frozen=True, slots=True)
class LiveResult:
    snapshot: SequenceSnapshot
    detection: object | None = None
    classification: object | None = None
    notices: tuple[str, ...] = ()


class LiveSequenceSession:
    def __init__(self, classifier, sample_rate: int, channels: int, stream_id: str,
                 start_frame: int = 0, time_origin: float = 0.0, round_index: int = 1,
                 recognition: RecognitionSettings | None = None,
                 sequence: SequenceSettings | None = None, recorder=None,
                 cancel: Event | None = None) -> None:
        self.classifier, self.rate, self.stream_id = classifier, sample_rate, stream_id
        self.origin, self.start_frame, self.end_frame = time_origin, start_frame, start_frame
        self.recognition = replace(recognition or RecognitionSettings())
        self.engine = SequenceEngine(sequence)
        self.engine.arm(self._time(start_frame), round_index)
        self.confidence = ConfidenceEngine(self.recognition)
        onset_matcher = build_optional_onset_matcher(
            classifier, sample_rate, channels, start_frame=start_frame,
            cancel=cancel,
        )
        try:
            detector = StreamingRmsDetector(
                sample_rate, channels, self.recognition.detection_threshold,
                start_frame, onset_matcher,
            )
        except OnsetMatcherResourceLimit:
            if onset_matcher is None:
                raise
            onset_matcher = None
            detector = StreamingRmsDetector(
                sample_rate, channels, self.recognition.detection_threshold,
                start_frame,
            )
        self._matcher_active = onset_matcher is not None
        self.detector = detector
        self.analyzer, self.recorder = Analyzer(classifier.sample_rate), recorder
        self._processed_frame = start_frame
        self._checksums = []
        self._blocked = False
        self.evidence = CueEvidenceCache()

    def _time(self, frame: int) -> float:
        return self.origin + frame / self.rate

    def invalidate(self, reason: str, cancel: Event | None = None) -> LiveResult:
        self.engine.invalidate(max(self.engine.snapshot().timestamp, self._time(self.end_frame)), reason)
        snapshot = self.engine.verify(self.recognition, cancel)
        self._blocked = True
        notices=self.evidence.save_problem(snapshot,self.recorder,cancel)
        notices=(*notices,*self._summary(snapshot,cancel))
        return LiveResult(snapshot,notices=tuple(notices))

    def _summary(self, snapshot, cancel):
        if self.recorder is None:
            return ()
        checksum = sha256("".join(self._checksums).encode("ascii")).hexdigest()
        saved = self.recorder.record_sequence(snapshot, checksum, cancel, source={
            "source": "live", "stream_id": self.stream_id, "sample_rate": self.rate,
            "start_frame": self.start_frame, "end_frame": self._processed_frame,
            "checksum_scope": "ordered_cue_checksums",
        })
        return saved.notices

    def feed(self, samples, cancel: Event | None = None,
             progress: Callable[[LiveResult], None] | None = None) -> tuple[LiveResult, ...]:
        check_cancel(cancel)
        self.end_frame += len(samples)
        if self._blocked:
            return ()
        results = []
        def publish(snapshot, detection=None, classification=None, notices=()):
            result = LiveResult(snapshot, detection, classification, tuple(notices))
            results.append(result)
            if progress:
                progress(result)

        for step in self.detector.feed(samples, cancel):
            self._processed_frame = step.end_frame
            previous = self.engine.snapshot()
            if step.onset_frame is not None:
                self.engine.advance(self._time(step.onset_frame), silent=False)
            window = step.window
            if window is not None and self.engine.state in (
                SequenceState.ARMED, SequenceState.PASS_1, SequenceState.WAIT_PASS_2, SequenceState.PASS_2
            ):
                onset = self._time(window.onset_frame)
                end = self._time(window.signal_end_frame)
                before = self.engine.snapshot()
                analysis = self.analyzer.analyze(ClipSource(window.clip), cancel)
                raw = self.classifier.classify_preprocessed(analysis.processed, cancel)
                context = EventContext(
                    "live", onset, self.stream_id,
                    window.start_frame, window.end_frame,
                )

                # A single false start chime must not anchor PASS1 forever.
                # Probe with fresh confidence so a long duplicate cooldown from
                # that discarded cue cannot prevent a safe restart.
                if (
                    self._matcher_active
                    and window.matched
                    and before.state == SequenceState.PASS_1
                    and self.engine.can_restart_incomplete_pass(onset)
                    and not window.reason
                ):
                    probe = ConfidenceEngine(self.recognition).evaluate(raw, context)
                    if can_fill_matched_position(probe, True):
                        before = self.engine.restart_incomplete_pass(onset)
                        self.confidence.reset()
                        self.evidence.clear()
                        self._checksums.clear()

                evaluation = (
                    ConfidenceEngine(self.recognition)
                    if window.reason else self.confidence
                )
                detection = evaluation.evaluate(raw, context)
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
                    not self._matcher_active
                    or can_fill_matched_position(detection, window.matched)
                )
                candidate = matcher_eligible and (
                    in_progress
                    or can_start_presentation(
                        detection, self.recognition.low_score_threshold
                    )
                )
                if candidate:
                    snapshot = self.engine.add(SequenceEntry(detection, raw, end))
                    if before.state != SequenceState.WAIT_PASS_2 and snapshot.state == SequenceState.WAIT_PASS_2:
                        self.confidence.reset()
                    second = before.state in (SequenceState.WAIT_PASS_2, SequenceState.PASS_2)
                    self.evidence.add(CueEvidence(
                        window.clip, analysis.checksum, detection, raw, before.round_index,
                        2 if second else 1, len(before.pass2 if second else before.pass1) + 1,
                        window.onset_detection,
                    ))
                    self._checksums.append(analysis.checksum)
                    self._checksums = self._checksums[-14:]
                else:
                    snapshot = self.engine.ignore(end)
                notices = []
                if self.recorder:
                    sequence_context = None
                    if candidate:
                        second = before.state in (SequenceState.WAIT_PASS_2, SequenceState.PASS_2)
                        sequence_context = {
                            "round": before.round_index,
                            "pass": 2 if second else 1,
                            "index": len(before.pass2 if second else before.pass1) + 1,
                            "state": snapshot.state.value,
                            "reason": snapshot.reason,
                        }
                    saved = self.recorder.record(
                        detection, raw, window.clip, analysis.checksum, cancel,
                        sequence=sequence_context,
                        onset_detection=window.onset_detection,
                    )
                    notices.extend(saved.notices)
                if snapshot.state in (SequenceState.VERIFY, SequenceState.UNCERTAIN):
                    snapshot = self.engine.verify(self.recognition, cancel)
                    notices.extend(self.evidence.save_problem(snapshot, self.recorder, cancel))
                    notices.extend(self._summary(snapshot, cancel))
                    self._blocked = snapshot.state == SequenceState.UNCERTAIN
                publish(snapshot, detection, raw, notices)
            if not step.active:
                snapshot = self.engine.advance(max(self.engine.snapshot().timestamp, self._time(step.end_frame)), silent=step.silent)
                if snapshot.state == SequenceState.UNCERTAIN and snapshot.verification is None:
                    snapshot = self.engine.verify(self.recognition, cancel)
                    self._blocked = True
                    publish(snapshot, notices=(*self.evidence.save_problem(snapshot,self.recorder,cancel),*self._summary(snapshot,cancel)))
                elif snapshot.state != previous.state or snapshot.round_index != previous.round_index:
                    if snapshot.round_index != previous.round_index:
                        self.confidence.reset()
                        self._checksums.clear()
                        self.evidence.clear()
                    publish(snapshot)
            if self._blocked:
                break
        return tuple(results)
