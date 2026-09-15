"""Conservative score/margin decisions and stream-local duplicate suppression, without Qt."""
from dataclasses import dataclass, replace
from enum import StrEnum
import math

from audio.event import EventContext
from config.schema import ConfigValidationError, RecognitionSettings
from detector.classifier import ClassificationResult, TIE_EPSILON
from encounter.vog_oracles import OracleId


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class DetectionResult:
    context: EventContext
    oracle: OracleId | None
    best_candidate: OracleId | None
    confidence: float | None
    waveform_score: float | None
    spectrum_score: float | None
    second_candidate: OracleId | None
    second_score: float | None
    margin: float | None
    status: ConfidenceLevel
    reason: str
    duplicate: bool = False

    @property
    def timestamp(self) -> float:
        return self.context.timestamp

    @property
    def accepted(self) -> bool:
        return self.oracle is not None


class ConfidenceEngine:
    """One worker owns an engine. Replay decisions do not touch live history.

    Missing Oracle templates cannot establish a reliable runner-up across all seven
    positions. LOW and REJECTED retain diagnostic candidates but never an Oracle.
    Duplicate times are anchored to the last accepted event, not suppressed events.
    """
    def __init__(self, settings: RecognitionSettings | None = None) -> None:
        self.settings = replace(settings or RecognitionSettings())
        score_fields = ("low_score_threshold", "confidence_threshold", "high_confidence_threshold",
                        "margin_threshold", "high_margin_threshold")
        for name in (*score_fields, "duplicate_cooldown"):
            value = getattr(self.settings, name)
            upper = 10 if name == "duplicate_cooldown" else 1
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
                raise ConfigValidationError(f"recognition.{name}: invalid confidence setting")
        if not (self.settings.low_score_threshold <= self.settings.confidence_threshold
                <= self.settings.high_confidence_threshold):
            raise ConfigValidationError("recognition score thresholds must be ordered")
        if self.settings.margin_threshold > self.settings.high_margin_threshold:
            raise ConfigValidationError("recognition margin thresholds must be ordered")
        self.reset()

    def reset(self) -> None:
        self._stream_id: str | None = None
        self._latest_time: float | None = None
        self._accepted: dict[OracleId, float] = {}

    @staticmethod
    def _valid(result: ClassificationResult) -> bool:
        if not result.ranking or len(result.ranking) > len(OracleId):
            return False
        ids = [item.oracle for item in result.ranking]
        if any(not isinstance(oracle, OracleId) for oracle in ids) or len(set(ids)) != len(ids):
            return False
        values = [value for item in result.ranking
                  for value in (item.score, item.waveform_score, item.spectrum_score)]
        return (all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 for value in values)
                and all(a.score >= b.score for a, b in zip(result.ranking, result.ranking[1:])))

    def evaluate(self, result: ClassificationResult, context: EventContext | None = None) -> DetectionResult:
        context = context or EventContext()
        stale = False
        if context.source == "live":
            if context.stream_id != self._stream_id:
                self.reset()
                self._stream_id = context.stream_id
            stale = self._latest_time is not None and context.timestamp < self._latest_time
            if not stale:
                self._latest_time = context.timestamp
        if not self._valid(result):
            return DetectionResult(context, None, None, None, None, None, None, None, None,
                                   ConfidenceLevel.REJECTED, "INVALID_RANKING" if result.ranking else result.status)
        best, second = result.ranking[0], result.ranking[1] if len(result.ranking) > 1 else None
        margin = best.score - second.score if second else None
        status, reason, oracle = ConfidenceLevel.REJECTED, "BELOW_LOW_THRESHOLD", None
        policy = self.settings
        if stale:
            reason = "STALE_EVENT"
        elif result.status not in ("RANKED", "TIED"):
            reason = result.status
        elif best.score >= policy.low_score_threshold:
            status = ConfidenceLevel.LOW
            if result.missing_oracles:
                reason = "INCOMPLETE_BANK"
            elif result.status == "TIED" or margin <= TIE_EPSILON or not _at_least(margin, policy.margin_threshold):
                reason = "AMBIGUOUS_MARGIN"
            elif best.score < policy.confidence_threshold:
                reason = "BELOW_CONFIDENCE_THRESHOLD"
            else:
                status = (ConfidenceLevel.HIGH
                          if best.score >= policy.high_confidence_threshold and _at_least(margin, policy.high_margin_threshold)
                          else ConfidenceLevel.MEDIUM)
                reason, oracle = "ACCEPTED", best.oracle
        duplicate = False
        if oracle is not None and context.source == "live":
            previous = self._accepted.get(oracle)
            if previous is not None and not _at_least(context.timestamp - previous, policy.duplicate_cooldown):
                duplicate, oracle, reason = True, None, "DUPLICATE"
            else:
                self._accepted[oracle] = context.timestamp
        return DetectionResult(context, oracle, best.oracle, best.score, best.waveform_score,
                               best.spectrum_score, second.oracle if second else None,
                               second.score if second else None, margin, status, reason, duplicate)


def can_start_presentation(result: DetectionResult, low_score_threshold: float) -> bool:
    """Only a plausible Oracle match may start a presentation."""
    return (
        result.status is not ConfidenceLevel.REJECTED
        and result.confidence is not None
        and _at_least(result.confidence, low_score_threshold)
    )

_MATCHED_UNSAFE_WINDOWS = frozenset({
    "CLIPPED_EVENT", "SHORT_EVENT", "EVENT_LIMIT",
})


def can_fill_matched_position(
    result: DetectionResult, matched_onset: bool,
) -> bool:
    """Keep accepted cues and matcher-backed uncertain windows in PASS order."""
    return result.accepted or (
        matched_onset
        and (
            result.status is ConfidenceLevel.LOW
            or result.reason in _MATCHED_UNSAFE_WINDOWS
        )
    )

def _at_least(value: float, threshold: float) -> bool:
    # Decimal boundaries may lose an ulp when subtracting scores or timestamps.
    return value >= threshold or math.isclose(value, threshold, rel_tol=0, abs_tol=1e-12)
