"""Keep top-N evidence and validate a VoG pass without modifying classifier results."""
from __future__ import annotations
import math
from detector.confidence import ConfidenceEngine, ConfidenceLevel
from encounter.verification import (
    CandidateHistory, CandidateScore, EventCandidates, DuplicateOracle, PassValidation,
    VerificationPolicy,
)


def candidate_history(first, second, policy: VerificationPolicy) -> CandidateHistory:
    def events(entries):
        result = []
        for index, entry in enumerate(entries, 1):
            raw, event = entry.classification, entry.detection
            valid = ConfidenceEngine._valid(raw)
            scores = tuple(CandidateScore(row.oracle, row.score, row.waveform_score, row.spectrum_score)
                           for row in raw.ranking[:policy.candidate_top_n]) if valid else ()
            confidence = raw.best_score if valid else None
            consistent = (confidence is not None and type(event.confidence) in (int, float)
                          and math.isfinite(event.confidence)
                          and math.isclose(confidence, event.confidence, rel_tol=0, abs_tol=1e-12)
                          and event.best_candidate == raw.best_candidate)
            eligible = (valid and consistent and raw.status in ("RANKED", "TIED")
                        and not raw.missing_oracles and not event.duplicate
                        and event.status in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW)
                        and event.reason in ("ACCEPTED", "AMBIGUOUS_MARGIN", "BELOW_CONFIDENCE_THRESHOLD"))
            accepted = entry.oracle if eligible else None
            if event.oracle is not None and (accepted != raw.best_candidate or accepted is None):
                eligible, accepted = False, None
            result.append(EventCandidates(index, entry.timestamp, accepted,
                                          raw.best_candidate if valid else None, confidence,
                                          event.status, event.reason, eligible, scores))
        return tuple(result)
    return CandidateHistory(events(first), events(second))


def validate_pass(events: tuple[EventCandidates, ...], expected_count: int) -> PassValidation:
    seen = {}
    for event in events:
        if event.eligible and event.best_candidate is not None:
            seen.setdefault(event.best_candidate, []).append(event.index)
    duplicates = tuple(DuplicateOracle(oracle, tuple(indices))
                       for oracle, indices in seen.items() if len(indices) > 1)
    return PassValidation(expected_count, len(events), duplicates,
                          tuple(event.index for event in events if event.accepted is None),
                          tuple(event.index for event in events if not event.eligible))
