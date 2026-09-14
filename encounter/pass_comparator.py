"""Compare independent presentations and identify one-based disagreement positions."""
from dataclasses import replace
import math
from encounter.rules import candidate_history, validate_pass
from encounter.verification import (
    PassComparison, PositionDifference, VerificationPolicy, VerificationStatus,
)


class PassComparator:
    def __init__(self, sequence=None, recognition=None):
        self.policy = VerificationPolicy.from_settings(sequence, recognition)

    def compare(self, first, second, expected_count: int) -> PassComparison:
        if type(expected_count) is not int or not 1 <= expected_count <= 7:
            raise ValueError("Expected sequence length must be 1..7")
        first, second = tuple(first), tuple(second)
        if len(first) > 7 or len(second) > 7:
            raise ValueError("A VoG pass cannot exceed seven entries")
        history = candidate_history(first, second, self.policy)
        a, b = validate_pass(history.pass1, expected_count), validate_pass(history.pass2, expected_count)
        differences, sequence, corrections = [], [], []
        complete = a.complete and b.complete
        for index in range(expected_count):
            left = history.pass1[index] if index < len(first) else None
            right = history.pass2[index] if index < len(second) else None
            accepted_equal = left is not None and right is not None and left.accepted is not None and left.accepted == right.accepted
            choice = left.accepted if accepted_equal else None
            if not accepted_equal:
                candidates = (left.best_candidate if left else None, right.best_candidate if right else None)
                reason = ("MISSING_EVENT" if left is None or right is None else
                          "DISAGREEMENT" if candidates[0] != candidates[1] else "UNKNOWN_EVENT")
                differences.append(PositionDifference(index+1, *candidates,
                                                      left.confidence if left else None,
                                                      right.confidence if right else None, reason))
                if complete and left.eligible and right.eligible:
                    for stronger, weaker in ((left, right), (right, left)):
                        gap = stronger.confidence - weaker.confidence
                        if (stronger.accepted is not None and gap > 1e-6
                                and (gap >= self.policy.inference_margin
                                     or math.isclose(gap, self.policy.inference_margin, abs_tol=1e-12, rel_tol=0))):
                            choice = stronger.accepted
                            corrections.append(index+1)
                            break
            sequence.append(choice)
        if a.valid and b.valid and not differences:
            return PassComparison(VerificationStatus.CONFIRMED, "MATCHING_PASSES", tuple(sequence),
                                  (), a, b, history)
        inferred = (complete and not (a.unsafe_indices or b.unsafe_indices)
                    and None not in sequence and len(set(sequence)) == expected_count
                    and 0 < len(corrections) <= self.policy.max_corrections)
        if inferred:
            status, reason = VerificationStatus.INFERRED, "CONFIDENCE_INFERENCE"
        else:
            status = (VerificationStatus.CHECK if not complete or a.unsafe_indices or b.unsafe_indices else
                      VerificationStatus.MISMATCH if any(d.reason == "DISAGREEMENT" for d in differences) else VerificationStatus.CHECK)
            reason = ("INCOMPLETE_PASSES" if not complete else
                      "UNSAFE_EVENT" if a.unsafe_indices or b.unsafe_indices else
                      "DUPLICATE_ORACLE" if a.duplicates or b.duplicates else "UNRESOLVED_PASSES")
            # An unresolved row contains only uncontested, unique accepted positions.
            sequence = [left.accepted if left.accepted is not None and left.accepted == right.accepted else None
                        for left, right in zip(history.pass1[:expected_count], history.pass2[:expected_count])]
            sequence += [None] * (expected_count-len(sequence))
            duplicates = {item.oracle for validation in (a,b) for item in validation.duplicates}
            sequence = [oracle if oracle not in duplicates else None for oracle in sequence]
            corrections = []
        return PassComparison(status, reason, tuple(sequence), tuple(differences), a, b, history, tuple(corrections))
