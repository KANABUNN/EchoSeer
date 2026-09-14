"""Uniqueness, consensus, score optimum, ambiguity, safety and bounded cancellation."""
from dataclasses import replace
from threading import Event
import pytest
from audio.operations import OperationCancelled
from config.schema import SequenceSettings
from encounter.pass_comparator import PassComparator
from encounter.reconstruction import SequenceReconstructor
from encounter.sequence import SequenceEngine
from encounter.vog_oracles import OracleId
from tests.unit.test_pass_comparator import event,passes


def resolve(first,second,settings=None):
    comparison=PassComparator(settings).compare(first,second,len(first))
    return SequenceReconstructor(settings).resolve(comparison)


def test_low_error_uses_second_candidate_and_occupied_oracle_rule():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    first=list(first);first[2]=event(OracleId.L2,.83,{OracleId.L3:.82},start=first[2].timestamp)
    result=resolve(first,second)
    assert result.status=="INFERRED" and result.sequence==(OracleId.L1,OracleId.L2,OracleId.L3)
    assert result.correction_indices==(3,)
    assert result.history.pass1[2].best_candidate==OracleId.L2 and first[2].oracle is None
    assert len(set(result.sequence))==3 and result.reconstruction.inferred


def test_both_passes_duplicate_low_top_one_reconstruct_from_top_two():
    first,second=passes([OracleId.L1,OracleId.MID,OracleId.L3])
    first=list(first);second=list(second)
    for rows in (first,second):
        rows[2]=event(OracleId.L1,.86,{OracleId.L3:.85},start=rows[2].timestamp)
    result=resolve(first,second)
    assert result.status=="INFERRED" and result.sequence==(OracleId.L1,OracleId.MID,OracleId.L3)
    assert result.reconstruction.accepted_matches==4 and result.reconstruction.two_pass_support==3
    assert result.pass1.duplicates[0].indices==(1,3)
    assert result.history.pass1[2].candidates[0].oracle==OracleId.L1


def test_weak_unique_second_candidate_is_check_with_diagnostic_proposal():
    first,second=passes([OracleId.L1,OracleId.MID,OracleId.L3])
    first=list(first);second=list(second)
    for rows in (first,second):rows[2]=event(OracleId.L1,.61,{OracleId.L3:.60},start=rows[2].timestamp)
    result=resolve(first,second)
    assert result.status=="CHECK" and result.reason=="WEAK_RECONSTRUCTION"
    assert result.suggested_sequence is None
    assert result.reconstruction.sequence==(OracleId.L1,OracleId.MID,OracleId.L3)


def test_equal_valid_paths_are_not_auto_inferred():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    first=list(first);second=list(second)
    for rows in (first,second):
        rows[2]=event(OracleId.L3,.9,{OracleId.R3:.9},start=rows[2].timestamp)
    result=resolve(first,second)
    assert result.status=="CHECK" and result.reason=="AMBIGUOUS_RECONSTRUCTION"
    assert result.reconstruction.margin==0 and result.reconstruction.runner_up is not None
    assert not result.suggested_sequence


@pytest.mark.parametrize("gap,inferred",[(.099,False),(.1,True),(.101,True)])
def test_reconstruction_runner_up_score_margin_boundary(gap,inferred):
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    first=list(first);second=list(second)
    # All three near candidates prevent single-event acceptance; only one position is uncertain.
    for rows in (first,second):
        rows[2]=event(OracleId.L3,.95,{OracleId.L1:.94,OracleId.R3:.95-gap},start=rows[2].timestamp)
    result=resolve(first,second)
    assert (result.status=="INFERRED")==inferred
    assert result.reconstruction.margin==pytest.approx(gap)


def test_strong_conflicting_unique_passes_remain_mismatch():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    second=list(second);second[2]=event(OracleId.R3,.99,start=second[2].timestamp)
    result=resolve(first,second)
    assert result.status=="MISMATCH" and result.mismatch_indices==(3,)
    assert result.suggested_sequence is None and not result.reconstruction.inferred


def test_vog_duplicate_resolves_even_high_conflict_when_alternative_is_occupied():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    second=list(second);second[2]=event(OracleId.L1,.99,start=second[2].timestamp)
    result=resolve(first,second)
    assert result.status=="INFERRED" and result.sequence==(OracleId.L1,OracleId.L2,OracleId.L3)
    assert result.reconstruction.runner_up is None


def test_two_uncertain_positions_exceed_default_correction_limit():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    first=list(first)
    first[1]=event(OracleId.L1,.83,{OracleId.L2:.82},start=first[1].timestamp)
    first[2]=event(OracleId.L1,.83,{OracleId.L3:.82},start=first[2].timestamp)
    result=resolve(first,second)
    assert result.status!="INFERRED" and result.reason=="CORRECTION_LIMIT"
    assert result.suggested_sequence is None


@pytest.mark.parametrize("reason",["CLIPPED_EVENT","SHORT_EVENT","EVENT_LIMIT"])
def test_reconstruction_does_not_fill_unsafe_event_from_good_pass(reason):
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    first=list(first);first[2]=event(OracleId.L3,start=first[2].timestamp,reason=reason)
    result=resolve(first,second)
    assert result.status=="CHECK" and result.reason=="UNSAFE_EVENT"
    assert result.reconstruction.sequence is None


def test_top_one_history_cannot_invent_missing_second_candidate():
    first,second=passes([OracleId.L1,OracleId.MID,OracleId.L3])
    first=list(first);second=list(second)
    for rows in (first,second):rows[2]=event(OracleId.L1,.86,{OracleId.L3:.85},start=rows[2].timestamp)
    result=resolve(first,second,SequenceSettings(candidate_top_n=1))
    assert result.status=="CHECK" and result.reason=="NO_VALID_SEQUENCE"
    assert len(result.history.pass1[2].candidates)==1 and result.reconstruction.sequence is None


def test_full_seven_oracle_search_is_bounded_and_ties_are_check():
    first,second=passes(list(OracleId))
    first=tuple(event(o,.9,{item:.9 for item in OracleId},start=e.timestamp) for o,e in zip(OracleId,first))
    second=tuple(event(o,.9,{item:.9 for item in OracleId},start=e.timestamp) for o,e in zip(OracleId,second))
    settings=SequenceSettings(candidate_top_n=7,max_corrections=7)
    result=resolve(first,second,settings)
    assert result.status=="CHECK" and result.reconstruction.reason=="AMBIGUOUS_RECONSTRUCTION"
    assert result.reconstruction.visited_nodes<=14000 and len(result.reconstruction.sequence)==7
    assert result.reconstruction.margin==0


def test_reconstruction_cancelled_before_or_during_search(monkeypatch):
    import encounter.reconstruction as module
    first,second=passes(list(OracleId))
    first=tuple(event(o,.9,{item:.9 for item in OracleId},start=e.timestamp) for o,e in zip(OracleId,first))
    second=tuple(event(o,.9,{item:.9 for item in OracleId},start=e.timestamp) for o,e in zip(OracleId,second))
    settings=SequenceSettings(candidate_top_n=7,max_corrections=7)
    comparison=PassComparator(settings).compare(first,second,7)
    cancel=Event();cancel.set()
    with pytest.raises(OperationCancelled):SequenceReconstructor(settings).resolve(comparison,cancel)
    cancel.clear();real=module.check_cancel;calls=0
    def check(token):
        nonlocal calls
        calls+=1
        if calls==100:cancel.set()
        real(token)
    monkeypatch.setattr(module,"check_cancel",check)
    with pytest.raises(OperationCancelled):SequenceReconstructor(settings).resolve(comparison,cancel)
    assert calls==100


def ingest(first,second):
    engine=SequenceEngine();engine.arm()
    for rows in (first,second):
        for entry in rows:engine.add(entry)
    return engine


def test_fsm_applies_exact_comparison_and_retains_verified_data_in_lockout_history():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    engine=ingest(first,second)
    result=engine.verify()
    assert result.state=="CONFIRMED" and result.verification.status=="CONFIRMED"
    assert result.final_sequence==(OracleId.L1,OracleId.L2,OracleId.L3)
    assert engine.advance(result.timestamp).state=="LOCKOUT"
    next_round=engine.next_round(result.timestamp+1)
    assert next_round.verification is None and engine.round_history[-1].verification.status=="CONFIRMED"
    assert engine.reset().verification is None


def test_fsm_inferred_result_stays_uncertain_without_confirmed_sequence():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    first=list(first);first[2]=event(OracleId.L2,.83,{OracleId.L3:.82},start=first[2].timestamp)
    engine=ingest(first,second)
    result=engine.verify()
    assert result.state=="UNCERTAIN" and result.verification.status=="INFERRED"
    assert result.final_sequence is None and not result.confirmed
    assert result.suggested_sequence==(OracleId.L1,OracleId.L2,OracleId.L3)
    with pytest.raises(ValueError):engine.confirm(result.timestamp)


def test_fsm_structural_failure_does_not_reconstruct_or_shift_missing_events():
    engine=SequenceEngine();engine.arm()
    engine.add(event(OracleId.L1,start=1));engine.add(event(OracleId.L2,start=2))
    engine.finish(3)
    result=engine.verify()
    assert result.state=="UNCERTAIN" and result.verification.status=="CHECK"
    assert result.verification.reason=="INCOMPLETE_INPUT" and result.suggested_sequence is None
