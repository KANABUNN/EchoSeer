"""Bounded unique-Oracle search: accepted agreement, two-pass support, score sum."""
from dataclasses import replace
import math
from audio.operations import check_cancel
from encounter.verification import ReconstructionResult, VerificationPolicy, VerificationStatus
from encounter.vog_oracles import OracleId


class SequenceReconstructor:
    def __init__(self, sequence=None, recognition=None):
        self.policy = VerificationPolicy.from_settings(sequence, recognition)

    def resolve(self, comparison, cancel=None):
        check_cancel(cancel)
        if comparison.status == VerificationStatus.CONFIRMED:
            return comparison
        reconstruction = self.reconstruct(comparison, cancel)
        if reconstruction.inferred:
            return replace(comparison, status=VerificationStatus.INFERRED, reason="RECONSTRUCTED",
                           sequence=reconstruction.sequence, correction_indices=reconstruction.correction_indices,
                           reconstruction=reconstruction)
        status = comparison.status
        if status == VerificationStatus.INFERRED:
            status = VerificationStatus.MISMATCH if comparison.mismatch_indices else VerificationStatus.CHECK
        return replace(comparison, status=status, reconstruction=reconstruction, reason=reconstruction.reason)

    def reconstruct(self, comparison, cancel=None):
        check_cancel(cancel)
        a, b = comparison.pass1, comparison.pass2
        if not (a.complete and b.complete):
            return ReconstructionResult(reason="INCOMPLETE_PASSES")
        if a.unsafe_indices or b.unsafe_indices:
            return ReconstructionResult(reason="UNSAFE_EVENT")
        if self.policy.max_corrections == 0:
            return ReconstructionResult(reason="CORRECTION_LIMIT")
        duplicate_ids = {item.oracle for validation in (a,b) for item in validation.duplicates}
        domains = []
        canonical = tuple(OracleId)
        for left, right in zip(comparison.history.pass1, comparison.history.pass2):
            scores_a = {row.oracle: row.score for row in left.candidates}
            scores_b = {row.oracle: row.score for row in right.candidates}
            fixed = left.accepted if left.accepted is not None and left.accepted == right.accepted and left.accepted not in duplicate_ids else None
            options = []
            for oracle in canonical:
                x, y = scores_a.get(oracle), scores_b.get(oracle)
                support_a = x is not None and x >= self.policy.candidate_floor and x > 1e-6
                support_b = y is not None and y >= self.policy.candidate_floor and y > 1e-6
                if fixed is not None and oracle != fixed:
                    continue
                if not (support_a or support_b):
                    continue
                votes = int(left.accepted == oracle) + int(right.accepted == oracle)
                strong = votes > 0 or (support_a and support_b and max(x,y) >= self.policy.confidence_threshold)
                correction = not (left.accepted == right.accepted == oracle)
                options.append((oracle, votes, int(support_a and support_b), ((x or 0)+(y or 0))/2, strong, correction))
            domains.append(options)
        best = []
        visited = 0
        def search(index, used, path, votes, support, score, strong, corrections):
            nonlocal visited
            visited += 1
            check_cancel(cancel)
            if len(corrections) > self.policy.max_corrections:
                return
            if index == len(domains):
                best.append(((votes,support,score),tuple(path),strong,tuple(corrections)))
                best.sort(key=lambda item:item[0],reverse=True)
                del best[2:]
                return
            for oracle, v, s, value, reliable, correction in domains[index]:
                if oracle not in used:
                    search(index+1,used|{oracle},path+[oracle],votes+v,support+s,score+value,
                           strong and reliable,corrections+[index+1] if correction else corrections)
        search(0,set(),[],0,0,0.0,True,[])
        if not best:
            reason = "CORRECTION_LIMIT" if sum(1 for x,y in zip(comparison.history.pass1,comparison.history.pass2)
                                               if x.accepted is None or y.accepted is None or x.accepted != y.accepted) > self.policy.max_corrections else "NO_VALID_SEQUENCE"
            return ReconstructionResult(reason=reason,visited_nodes=visited)
        key, sequence, strong, corrections = best[0]
        runner = best[1] if len(best)>1 else None
        equally_supported = runner is not None and key[:2] == runner[0][:2]
        margin = key[2]-runner[0][2] if equally_supported else None
        separated = not equally_supported or (margin > 1e-6 and
                    (margin >= self.policy.reconstruction_margin
                     or math.isclose(margin,self.policy.reconstruction_margin,rel_tol=0,abs_tol=1e-12)))
        strong_conflict = any(left.accepted is not None and right.accepted is not None
                              and left.accepted != right.accepted
                              and left.level.value == right.level.value == "HIGH"
                              and left.accepted not in duplicate_ids and right.accepted not in duplicate_ids
                              and abs(left.confidence-right.confidence) < self.policy.inference_margin
                              for left,right in zip(comparison.history.pass1,comparison.history.pass2))
        inferred = strong and separated and bool(corrections) and not strong_conflict
        reason = ("RECONSTRUCTED" if inferred else "WEAK_RECONSTRUCTION" if not strong
                  else "STRONG_PASS_CONFLICT" if strong_conflict else "AMBIGUOUS_RECONSTRUCTION" if not separated else "NO_CORRECTION")
        return ReconstructionResult(sequence,runner[1] if runner else None,key[2],runner[0][2] if runner else None,
                                    key[0],key[1],margin,corrections,inferred,reason,visited)
