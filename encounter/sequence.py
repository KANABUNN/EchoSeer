"""VoG presentation FSM, independent verification and conservative reconstruction."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from encounter.verification import PassComparison
from dataclasses import dataclass, replace
from enum import StrEnum
import math

from config.schema import SequenceSettings
from detector.classifier import ClassificationResult
from detector.confidence import ConfidenceLevel, DetectionResult
from encounter.definition import EncounterDefinition, VOG_ORACLES
from encounter.vog_oracles import OracleId


class SequenceState(StrEnum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    PASS_1 = "PASS_1"
    WAIT_PASS_2 = "WAIT_PASS_2"
    PASS_2 = "PASS_2"
    VERIFY = "VERIFY"
    CONFIRMED = "CONFIRMED"
    UNCERTAIN = "UNCERTAIN"
    LOCKOUT = "LOCKOUT"


@dataclass(frozen=True, slots=True)
class SequenceEntry:
    detection: DetectionResult
    classification: ClassificationResult
    end_time: float

    def __post_init__(self) -> None:
        _time(self.end_time)
        if self.end_time < self.detection.timestamp:
            raise ValueError("An event cannot end before its onset")

    @property
    def timestamp(self) -> float:
        return self.detection.timestamp

    @property
    def oracle(self) -> OracleId | None:
        event = self.detection
        return (event.oracle if isinstance(event.oracle, OracleId) and not event.duplicate
                and event.status in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM) else None)


@dataclass(frozen=True, slots=True)
class StateTransition:
    before: SequenceState
    after: SequenceState
    timestamp: float
    reason: str


@dataclass(frozen=True, slots=True)
class SequenceSnapshot:
    state: SequenceState
    round_index: int
    expected_count: int
    pass1: tuple[SequenceEntry, ...]
    pass2: tuple[SequenceEntry, ...]
    timestamp: float
    reason: str
    confirmed: bool
    ignored_events: int
    transitions: tuple[StateTransition, ...]
    verification: PassComparison | None = None

    @property
    def final_sequence(self) -> tuple[OracleId, ...] | None:
        return tuple(entry.oracle for entry in self.pass1) if self.confirmed else None

    @property
    def suggested_sequence(self) -> tuple[OracleId, ...] | None:
        return self.verification.suggested_sequence if self.verification else None


class SequenceEngine:
    """Worker-owned metadata only. All internal times use one audio/monotonic clock.

    Unknown sound events occupy a position; suppressed duplicates never do. Expected
    counts define PASS1's end. A quiet gap must precede the first event of PASS2.
    """
    def __init__(self, settings: SequenceSettings | None = None,
                 encounter: EncounterDefinition = VOG_ORACLES) -> None:
        self.settings = replace(settings or SequenceSettings())
        for name in ("lockout_duration", "silence_duration", "pass_gap", "event_timeout"):
            value = getattr(self.settings, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not .01 <= value <= 120:
                raise ValueError(f"Invalid sequence setting: {name}")
        from encounter.verification import VerificationPolicy
        VerificationPolicy.from_settings(self.settings)
        self.encounter = encounter
        self.reset(0.0)

    def reset(self, now: float = 0.0) -> SequenceSnapshot:
        _time(now)
        self.state, self.round_index = SequenceState.IDLE, 1
        self._pass1, self._pass2, self._transitions, self._round_history = [], [], [], []
        self._now, self._last_end = float(now), None
        self._silence_since = self._lockout_since = None
        self._source_key = None
        self._verification = None
        self._confirmed, self._ignored, self.reason = False, 0, "RESET"
        return self.snapshot()

    @property
    def round_history(self) -> tuple[SequenceSnapshot, ...]:
        return tuple(self._round_history)

    def snapshot(self) -> SequenceSnapshot:
        return SequenceSnapshot(self.state, self.round_index, self.encounter.expected_count(self.round_index),
                                tuple(self._pass1), tuple(self._pass2), self._now, self.reason,
                                self._confirmed, self._ignored, tuple(self._transitions), self._verification)

    def _clock(self, now: float) -> None:
        _time(now)
        if now < self._now and not math.isclose(now, self._now, rel_tol=0, abs_tol=1e-12):
            raise ValueError("Sequence time must not move backwards")
        self._now = max(float(now), self._now)

    def _set(self, state: SequenceState, reason: str) -> None:
        if state != self.state:
            self._transitions.append(StateTransition(self.state, state, self._now, reason))
            self._transitions = self._transitions[-64:]
        self.state, self.reason = state, reason

    def arm(self, now: float = 0.0, round_index: int = 1) -> SequenceSnapshot:
        self.encounter.expected_count(round_index)
        if self.state != SequenceState.IDLE:
            raise ValueError("Reset or Next Round before arming again")
        self._clock(now)
        self.round_index = round_index
        self._set(SequenceState.ARMED, "ARMED")
        return self.snapshot()

    def next_round(self, now: float) -> SequenceSnapshot:
        self._clock(now)
        if self.state == SequenceState.IDLE:
            raise ValueError("No active round")
        self._round_history.append(self.snapshot())
        self._round_history = self._round_history[-len(self.encounter.sequence_lengths):]
        if self.round_index == len(self.encounter.sequence_lengths):
            self._set(SequenceState.IDLE, "ENCOUNTER_COMPLETE")
            return self.snapshot()
        self.round_index += 1
        self._pass1, self._pass2 = [], []
        self._last_end = self._silence_since = self._lockout_since = self._source_key = None
        self._confirmed, self._ignored = False, 0
        self._verification = None
        self._set(SequenceState.ARMED, "NEXT_ROUND")
        return self.snapshot()

    def advance(self, now: float, silent: bool = False) -> SequenceSnapshot:
        self._clock(now)
        if silent:
            if self._silence_since is None:
                self._silence_since = self._now
        else:
            self._silence_since = None
        if self.state == SequenceState.CONFIRMED:
            self._set(SequenceState.LOCKOUT, "LOCKOUT")
        if self.state == SequenceState.LOCKOUT:
            quiet = self._silence_since is not None and _elapsed(self._now - self._silence_since, self.settings.silence_duration)
            if _elapsed(self._now - self._lockout_since, self.settings.lockout_duration) and quiet:
                return self.next_round(self._now)
        elif self._last_end is not None:
            gap = self._now - self._last_end
            deadline = self.settings.event_timeout
            if self.state == SequenceState.WAIT_PASS_2:
                deadline += self.settings.pass_gap
            if self.state in (SequenceState.PASS_1, SequenceState.PASS_2, SequenceState.WAIT_PASS_2) and _elapsed(gap, deadline):
                self._set(SequenceState.UNCERTAIN, "EVENT_TIMEOUT")
        return self.snapshot()

    def add(self, entry: SequenceEntry) -> SequenceSnapshot:
        if self.state not in (SequenceState.ARMED, SequenceState.PASS_1, SequenceState.WAIT_PASS_2, SequenceState.PASS_2):
            self.advance(max(self._now, entry.timestamp))
            self._ignored += 1
            return self.snapshot()
        if entry.timestamp < self._now and not math.isclose(entry.timestamp, self._now, rel_tol=0, abs_tol=1e-12):
            self._ignored += 1
            self._set(SequenceState.UNCERTAIN, "STALE_EVENT")
            return self.snapshot()
        self.advance(entry.timestamp)
        if self.state not in (SequenceState.ARMED, SequenceState.PASS_1, SequenceState.WAIT_PASS_2, SequenceState.PASS_2):
            self._ignored += 1
            return self.snapshot()
        if entry.detection.duplicate:
            self._ignored += 1
            return self.snapshot()
        context = entry.detection.context
        source_key = (context.source, context.stream_id)
        if self._source_key is not None and source_key != self._source_key:
            self._set(SequenceState.UNCERTAIN, "SOURCE_CHANGED")
            self._ignored += 1
            return self.snapshot()
        self._source_key = source_key
        gap = entry.timestamp - self._last_end if self._last_end is not None else None
        if self.state == SequenceState.WAIT_PASS_2:
            if not _elapsed(gap, self.settings.pass_gap):
                self._set(SequenceState.UNCERTAIN, "EARLY_PASS_2")
                self._ignored += 1
                return self.snapshot()
            self._set(SequenceState.PASS_2, "PASS_2_STARTED")
        elif self.state in (SequenceState.PASS_1, SequenceState.PASS_2) and _elapsed(gap, self.settings.pass_gap):
            self._set(SequenceState.UNCERTAIN, "INCOMPLETE_PASS")
            self._ignored += 1
            return self.snapshot()
        elif self.state == SequenceState.ARMED:
            self._set(SequenceState.PASS_1, "PASS_1_STARTED")
        target = self._pass1 if self.state == SequenceState.PASS_1 else self._pass2
        target.append(entry)
        self._last_end = entry.end_time
        self._clock(entry.end_time)
        if len(target) == self.encounter.expected_count(self.round_index):
            if self.state == SequenceState.PASS_1:
                self._set(SequenceState.WAIT_PASS_2, "PASS_1_COMPLETE")
            elif any(item.oracle is None for item in (*self._pass1, *self._pass2)):
                self._set(SequenceState.UNCERTAIN, "UNKNOWN_EVENT")
            else:
                self._set(SequenceState.VERIFY, "AWAITING_VERIFICATION")
        return self.snapshot()

    def finish(self, now: float) -> SequenceSnapshot:
        self.advance(now, silent=True)
        if self.state in (SequenceState.ARMED, SequenceState.PASS_1, SequenceState.WAIT_PASS_2, SequenceState.PASS_2):
            self._set(SequenceState.UNCERTAIN, "NO_EVENTS" if not self._pass1 else "INCOMPLETE_INPUT")
        return self.snapshot()

    def verify(self, recognition=None, cancel=None) -> SequenceSnapshot:
        from encounter.pass_comparator import PassComparator
        from encounter.reconstruction import SequenceReconstructor
        from encounter.verification import VerificationStatus
        if self.state not in (SequenceState.VERIFY, SequenceState.UNCERTAIN):
            raise ValueError("Finish presentations before verification")
        comparison = PassComparator(self.settings, recognition).compare(self._pass1, self._pass2,
                                                                        self.encounter.expected_count(self.round_index))
        eligible = self.state == SequenceState.VERIFY or self.reason == "UNKNOWN_EVENT"
        if eligible:
            comparison = SequenceReconstructor(self.settings, recognition).resolve(comparison, cancel)
        else:
            comparison = replace(comparison, status=VerificationStatus.CHECK, reason=self.reason,
                                 sequence=(None,)*self.encounter.expected_count(self.round_index),
                                 correction_indices=())
        self._verification = comparison
        if comparison.status == VerificationStatus.CONFIRMED:
            return self.confirm(self._now)
        self._confirmed = False
        self._set(SequenceState.UNCERTAIN, comparison.reason)
        return self.snapshot()

    def confirm(self, now: float) -> SequenceSnapshot:
        """Only complete, equal, accepted, unique presentations can be confirmed."""
        self._clock(now)
        first, second = tuple(item.oracle for item in self._pass1), tuple(item.oracle for item in self._pass2)
        if (self.state != SequenceState.VERIFY or len(first) != self.encounter.expected_count(self.round_index)
                or first != second or None in first or len(set(first)) != len(first)):
            raise ValueError("An incomplete, unknown, repeated or mismatched sequence cannot be confirmed")
        self._confirmed = True
        self._lockout_since = self._now
        self._set(SequenceState.CONFIRMED, "VERIFIED")
        return self.snapshot()

    def reject_verification(self, now: float) -> SequenceSnapshot:
        self._clock(now)
        if self.state != SequenceState.VERIFY:
            raise ValueError("No complete presentations to verify")
        self._set(SequenceState.UNCERTAIN, "VERIFICATION_FAILED")
        return self.snapshot()


def _time(value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Sequence times must be finite and nonnegative")


def _elapsed(value: float, threshold: float) -> bool:
    return value >= threshold or math.isclose(value, threshold, rel_tol=0, abs_tol=1e-12)
