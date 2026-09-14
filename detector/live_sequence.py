"""Worker-owned continuous VoG recognition, independent of Qt and capture callbacks."""
from dataclasses import dataclass, replace
from hashlib import sha256
from threading import Event
from collections.abc import Callable

from audio.event import EventContext
from audio.operations import check_cancel
from audio.sources import ClipSource
from config.schema import RecognitionSettings, SequenceSettings
from detector.confidence import ConfidenceEngine, ConfidenceLevel
from detector.streaming import StreamingRmsDetector
from encounter.sequence import SequenceEngine, SequenceEntry, SequenceSnapshot, SequenceState
from replay.analyzer import Analyzer


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
                 sequence: SequenceSettings | None = None, recorder=None) -> None:
        self.classifier, self.rate, self.stream_id = classifier, sample_rate, stream_id
        self.origin, self.start_frame, self.end_frame = time_origin, start_frame, start_frame
        self.recognition = replace(recognition or RecognitionSettings())
        self.engine = SequenceEngine(sequence)
        self.engine.arm(self._time(start_frame), round_index)
        self.confidence = ConfidenceEngine(self.recognition)
        self.detector = StreamingRmsDetector(sample_rate, channels, self.recognition.detection_threshold, start_frame)
        self.analyzer, self.recorder = Analyzer(classifier.sample_rate), recorder
        self._processed_frame = start_frame
        self._checksums = []
        self._blocked = False

    def _time(self, frame: int) -> float:
        return self.origin + frame / self.rate

    def invalidate(self, reason: str, cancel: Event | None = None) -> LiveResult:
        self.engine.invalidate(max(self.engine.snapshot().timestamp, self._time(self.end_frame)), reason)
        snapshot = self.engine.verify(self.recognition, cancel)
        self._blocked = True
        self._summary(snapshot, cancel)
        return LiveResult(snapshot)

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
                onset, end = self._time(window.onset_frame), self._time(window.signal_end_frame)
                before = self.engine.snapshot()
                analysis = self.analyzer.analyze(ClipSource(window.clip), cancel)
                raw = self.classifier.classify_preprocessed(analysis.processed, cancel)
                detection = self.confidence.evaluate(raw, EventContext(
                    "live", onset, self.stream_id, window.start_frame, window.end_frame))
                if window.reason:
                    detection = replace(detection, oracle=None, status=ConfidenceLevel.REJECTED, reason=window.reason)
                snapshot = self.engine.add(SequenceEntry(detection, raw, end))
                if before.state != SequenceState.WAIT_PASS_2 and snapshot.state == SequenceState.WAIT_PASS_2:
                    self.confidence.reset()
                self._checksums.append(analysis.checksum)
                self._checksums = self._checksums[-14:]
                notices = []
                if self.recorder:
                    second = before.state in (SequenceState.WAIT_PASS_2, SequenceState.PASS_2)
                    saved = self.recorder.record(detection, raw, window.clip, analysis.checksum, cancel,
                        sequence={"round": before.round_index, "pass": 2 if second else 1,
                                  "index": len(before.pass2 if second else before.pass1) + 1,
                                  "state": snapshot.state.value, "reason": snapshot.reason})
                    notices.extend(saved.notices)
                if snapshot.state in (SequenceState.VERIFY, SequenceState.UNCERTAIN):
                    snapshot = self.engine.verify(self.recognition, cancel)
                    notices.extend(self._summary(snapshot, cancel))
                    self._blocked = snapshot.state == SequenceState.UNCERTAIN
                publish(snapshot, detection, raw, notices)
            if not step.active:
                snapshot = self.engine.advance(max(self.engine.snapshot().timestamp, self._time(step.end_frame)), silent=step.silent)
                if snapshot.state == SequenceState.UNCERTAIN and snapshot.verification is None:
                    snapshot = self.engine.verify(self.recognition, cancel)
                    self._blocked = True
                    publish(snapshot, notices=self._summary(snapshot, cancel))
                elif snapshot.state != previous.state or snapshot.round_index != previous.round_index:
                    if snapshot.round_index != previous.round_index:
                        self.confidence.reset()
                        self._checksums.clear()
                    publish(snapshot)
            if self._blocked:
                break
        return tuple(results)
