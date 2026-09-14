"""Exact agreement, one-based mismatches, inference and immutable original evidence."""
from dataclasses import replace
import math
import pytest
from config.schema import SequenceSettings, RecognitionSettings, AppConfig, ConfigValidationError
from detector.classifier import ClassificationResult, OracleScore, SampleScore
from detector.confidence import ConfidenceEngine, ConfidenceLevel, EventContext
from dsp.correlation import CorrelationMatch
from encounter.pass_comparator import PassComparator
from encounter.sequence import SequenceEntry
from encounter.vog_oracles import OracleId


def event(oracle=OracleId.L1, score=.96, alternatives=None, start=1., reason=None):
    scores={item:.1 for item in OracleId}
    scores[oracle]=score
    scores.update(alternatives or {})
    rows=[]
    for item,value in sorted(scores.items(),key=lambda item:-item[1]):
        sample=SampleScore(item.value,CorrelationMatch(value,0,1000),value,value)
        rows.append(OracleScore(item,value,(sample,),value,value))
    raw=ClassificationResult("RANKED",tuple(rows))
    detection=ConfidenceEngine().evaluate(raw,EventContext(timestamp=start))
    if reason:detection=replace(detection,oracle=None,status=ConfidenceLevel.REJECTED,reason=reason)
    return SequenceEntry(detection,raw,start+.2)


def passes(order=None):
    order=order or [OracleId.L2,OracleId.R1,OracleId.MID,OracleId.L3,OracleId.R2]
    return (tuple(event(oracle,start=1+index*.7) for index,oracle in enumerate(order)),
            tuple(event(oracle,start=8+index*.7) for index,oracle in enumerate(order)))


@pytest.mark.parametrize("count",range(3,8))
def test_matching_complete_unique_passes_are_confirmed(count):
    first,second=passes(list(OracleId)[:count])
    result=PassComparator().compare(first,second,count)
    assert result.status=="CONFIRMED" and result.sequence==tuple(e.oracle for e in first)
    assert not result.differences and result.pass1.valid and result.pass2.valid


@pytest.mark.parametrize("index",range(1,6))
def test_high_confidence_disagreement_reports_exact_one_based_index(index):
    first,second=passes()
    second=list(second);second[index-1]=event(OracleId.R3,start=second[index-1].timestamp)
    result=PassComparator().compare(first,second,5)
    assert result.status=="MISMATCH" and result.mismatch_indices==(index,)
    assert result.differences[0].pass1_candidate==first[index-1].oracle
    assert result.differences[0].pass2_candidate==OracleId.R3
    assert result.sequence[index-1] is None


def test_instruction_low_confidence_error_at_index_four_is_inferred():
    first,second=passes()
    second=list(second)
    second[3]=event(OracleId.L2,.61,{OracleId.L3:.60},start=second[3].timestamp)
    raw=second[3]
    result=PassComparator().compare(first,second,5)
    assert result.status=="INFERRED" and result.mismatch_indices==(4,)
    assert result.suggested_sequence==(OracleId.L2,OracleId.R1,OracleId.MID,OracleId.L3,OracleId.R2)
    assert result.correction_indices==(4,) and result.pass2.duplicates[0].indices==(1,4)
    assert second[3]==raw and raw.oracle is None and raw.classification.best_candidate==OracleId.L2
    assert result.history.pass2[3].candidates[0].oracle==OracleId.L2
    assert result.history.pass2[3].candidates[1].oracle==OracleId.L3


@pytest.mark.parametrize("gap,inferred",[(.179,False),(.18,True),(.181,True)])
def test_individual_inference_margin_boundary(gap,inferred):
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    second=list(second)
    second[2]=event(OracleId.R3,.96-gap,start=second[2].timestamp)
    result=PassComparator().compare(first,second,3)
    assert (result.status=="INFERRED")==inferred
    assert result.status!="CONFIRMED"


@pytest.mark.parametrize("reason",["CLIPPED_EVENT","SHORT_EVENT","EVENT_LIMIT","STALE_EVENT"])
def test_unsafe_window_is_not_inferred_from_other_pass(reason):
    first,second=passes()
    second=list(second);second[3]=event(OracleId.L3,start=second[3].timestamp,reason=reason)
    result=PassComparator().compare(first,second,5)
    assert result.status=="CHECK" and result.reason=="UNSAFE_EVENT"
    assert result.suggested_sequence is None and result.pass2.unsafe_indices==(4,)


def test_duplicate_sequences_never_confirm_and_originals_are_retained():
    first,second=passes([OracleId.L1,OracleId.MID,OracleId.L1])
    result=PassComparator().compare(first,second,3)
    assert result.status=="CHECK" and result.reason=="DUPLICATE_ORACLE"
    assert result.pass1.duplicates[0].indices==result.pass2.duplicates[0].indices==(1,3)
    assert result.history.pass1[2].accepted==OracleId.L1
    assert all(e.oracle==OracleId.L1 for e in (first[0],first[2]))
    assert result.sequence==(None,OracleId.MID,None)


@pytest.mark.parametrize("length", [2,4])
def test_missing_or_extra_entries_cannot_be_confirmed_or_inferred(length):
    first,second=passes(list(OracleId)[:length])
    result=PassComparator().compare(first,second,3)
    assert result.status!="CONFIRMED" and result.status!="INFERRED"
    assert result.reason=="INCOMPLETE_PASSES"
    assert result.pass1.actual_count==length and len(result.sequence)==3


def test_invalid_or_inconsistent_ranking_cannot_be_confirmed():
    first,second=passes([OracleId.L1,OracleId.L2,OracleId.L3])
    bad=replace(first[0],classification=replace(first[0].classification,ranking=(replace(first[0].classification.ranking[0],score=math.nan),)))
    result=PassComparator().compare([bad,*first[1:]],second,3)
    assert result.status=="CHECK" and not result.history.pass1[0].candidates
    bad=replace(first[0],detection=replace(first[0].detection,oracle=OracleId.R3))
    result=PassComparator().compare([bad,*first[1:]],second,3)
    assert result.status!="CONFIRMED" and result.pass1.unsafe_indices==(1,)


def test_candidate_top_n_is_separate_from_template_aggregation_and_settings_are_copied():
    settings=SequenceSettings(candidate_top_n=2)
    classifier_settings=RecognitionSettings(top_n=19)
    comparator=PassComparator(settings,classifier_settings)
    settings.candidate_top_n=1
    first,second=passes()
    result=comparator.compare(first,second,5)
    assert all(len(e.candidates)==2 for e in result.history.pass1)
    assert classifier_settings.top_n==19


@pytest.mark.parametrize("name,value",[
    ("candidate_top_n",0),("candidate_top_n",8),("candidate_top_n",True),
    ("max_corrections",-1),("max_corrections",8),("max_corrections",True),
    ("inference_margin",-1),("inference_margin",math.nan),
    ("reconstruction_margin",1.01),("reconstruction_margin",math.inf),
])
def test_invalid_verification_settings_rejected_by_core_and_config(name,value):
    config=AppConfig()
    setattr(config.sequence,name,value)
    with pytest.raises(ConfigValidationError):config.validate()
    with pytest.raises(ValueError):PassComparator(config.sequence)


def test_legacy_phase7_settings_load_new_defaults_without_mutating_input():
    raw={"schema_version":1,"sequence":{"pass_gap":2.3,"event_timeout":6}}
    before=repr(raw)
    restored=AppConfig.from_dict(raw)
    assert restored.sequence.candidate_top_n==3 and restored.sequence.max_corrections==1
    assert restored.sequence.pass_gap==2.3 and repr(raw)==before


def test_zero_corrections_disables_inference():
    first,second=passes()
    second=list(second);second[3]=event(OracleId.L2,.61,{OracleId.L3:.6},start=second[3].timestamp)
    result=PassComparator(SequenceSettings(max_corrections=0)).compare(first,second,5)
    assert result.status=="MISMATCH" and result.suggested_sequence is None
