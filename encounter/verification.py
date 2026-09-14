"""Immutable verification, candidate history and reconstruction evidence; no Qt."""
from __future__ import annotations
from dataclasses import dataclass, replace
from enum import StrEnum
import math
from config.schema import RecognitionSettings, SequenceSettings
from detector.confidence import ConfidenceEngine, ConfidenceLevel
from encounter.vog_oracles import OracleId


class VerificationStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    MISMATCH = "MISMATCH"
    INFERRED = "INFERRED"
    CHECK = "CHECK"


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    candidate_top_n: int
    max_corrections: int
    inference_margin: float
    reconstruction_margin: float
    candidate_floor: float
    confidence_threshold: float

    @classmethod
    def from_settings(cls, sequence: SequenceSettings | None = None,
                      recognition: RecognitionSettings | None = None):
        sequence = replace(sequence or SequenceSettings())
        recognition = ConfidenceEngine(recognition).settings
        for name, lower, upper in (("candidate_top_n", 1, 7), ("max_corrections", 0, 7)):
            value = getattr(sequence, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"Invalid sequence setting: {name}")
        for name in ("inference_margin", "reconstruction_margin"):
            value = getattr(sequence, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"Invalid sequence setting: {name}")
        return cls(sequence.candidate_top_n, sequence.max_corrections,
                   sequence.inference_margin, sequence.reconstruction_margin,
                   recognition.low_score_threshold, recognition.confidence_threshold)


@dataclass(frozen=True, slots=True)
class CandidateScore:
    oracle: OracleId
    score: float
    waveform_score: float
    spectrum_score: float


@dataclass(frozen=True, slots=True)
class EventCandidates:
    index: int
    timestamp: float
    accepted: OracleId | None
    best_candidate: OracleId | None
    confidence: float | None
    level: ConfidenceLevel
    reason: str
    eligible: bool
    candidates: tuple[CandidateScore, ...]


@dataclass(frozen=True, slots=True)
class CandidateHistory:
    pass1: tuple[EventCandidates, ...]
    pass2: tuple[EventCandidates, ...]


@dataclass(frozen=True, slots=True)
class DuplicateOracle:
    oracle: OracleId
    indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PassValidation:
    expected_count: int
    actual_count: int
    duplicates: tuple[DuplicateOracle, ...]
    unknown_indices: tuple[int, ...]
    unsafe_indices: tuple[int, ...]

    @property
    def complete(self) -> bool:
        return self.actual_count == self.expected_count

    @property
    def valid(self) -> bool:
        return self.complete and not (self.duplicates or self.unknown_indices or self.unsafe_indices)


@dataclass(frozen=True, slots=True)
class PositionDifference:
    index: int
    pass1_candidate: OracleId | None
    pass2_candidate: OracleId | None
    pass1_confidence: float | None
    pass2_confidence: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class ReconstructionResult:
    sequence: tuple[OracleId, ...] | None = None
    runner_up: tuple[OracleId, ...] | None = None
    score: float | None = None
    runner_up_score: float | None = None
    accepted_matches: int = 0
    two_pass_support: int = 0
    margin: float | None = None
    correction_indices: tuple[int, ...] = ()
    inferred: bool = False
    reason: str = "NOT_ATTEMPTED"
    visited_nodes: int = 0


@dataclass(frozen=True, slots=True)
class PassComparison:
    status: VerificationStatus
    reason: str
    sequence: tuple[OracleId | None, ...]
    differences: tuple[PositionDifference, ...]
    pass1: PassValidation
    pass2: PassValidation
    history: CandidateHistory
    correction_indices: tuple[int, ...] = ()
    reconstruction: ReconstructionResult | None = None

    @property
    def mismatch_indices(self) -> tuple[int, ...]:
        return tuple(item.index for item in self.differences if item.reason == "DISAGREEMENT")

    @property
    def suggested_sequence(self) -> tuple[OracleId, ...] | None:
        return tuple(self.sequence) if self.status == VerificationStatus.INFERRED and None not in self.sequence else None
