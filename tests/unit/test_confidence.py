"""Acceptance, uncertain ranking, time provenance and duplicate boundaries."""
from dataclasses import replace
import math
import pytest
from config.schema import RecognitionSettings, ConfigValidationError
from detector.classifier import ClassificationResult, OracleScore, SampleScore
from detector.confidence import ConfidenceEngine, ConfidenceLevel, EventContext
from dsp.correlation import CorrelationMatch
from encounter.vog_oracles import OracleId


def ranking(best=.95, second=.51, oracle=OracleId.L2, status="RANKED", missing=()):
    others=[item for item in OracleId if item != oracle and item not in missing]
    ordered=[(oracle,best),*[(item,second if i==0 else second/(i+2)) for i,item in enumerate(others)]]
    def item(oracle,score):
        sample=SampleScore(oracle.value,CorrelationMatch(score,0,1000),score,score)
        return OracleScore(oracle,score,(sample,),score,score)
    return ClassificationResult(status,tuple(item(o,s) for o,s in ordered if o not in missing))


@pytest.mark.parametrize("best,second,level",[
    (.95,.51,"HIGH"),(.87,.85,"LOW"),(.83,.82,"LOW"),(1,.99,"LOW"),
    (.92,.74,"HIGH"),(.92,.75,"MEDIUM"),(.91,.1,"MEDIUM"),(.82,.72,"MEDIUM"),
    (.819,.60,"LOW"),(.55,.1,"LOW"),(.5499,.1,"REJECTED"),(.95,.851,"LOW"),
])
def test_score_and_margin_must_both_pass(best,second,level):
    decision=ConfidenceEngine().evaluate(ranking(best,second))
    assert decision.status.value==level
    assert decision.accepted==(level in ("HIGH","MEDIUM"))
    assert decision.oracle==(OracleId.L2 if decision.accepted else None)
    assert decision.best_candidate==OracleId.L2 and decision.confidence==best
    assert decision.margin==pytest.approx(best-second)


def test_instruction_ambiguous_l2_r1_never_forced_even_with_high_best():
    raw=ranking(.83,.82)
    assert raw.second_candidate==OracleId.L1
    rows=list(raw.ranking)
    # Place R1 second with L1 occupying R1's old row.
    rows[1],rows[4]=replace(rows[1],oracle=OracleId.R1),replace(rows[4],oracle=OracleId.L1)
    decision=ConfidenceEngine().evaluate(replace(raw,ranking=tuple(rows)))
    assert decision.best_candidate==OracleId.L2 and decision.second_candidate==OracleId.R1
    assert decision.oracle is None and decision.status==ConfidenceLevel.LOW


@pytest.mark.parametrize("status",["SILENT","NO_TEMPLATES","NO_MATCH","TOO_SHORT","TOO_LONG","LIMIT"])
def test_no_candidates_are_rejected(status):
    decision=ConfidenceEngine().evaluate(ClassificationResult(status))
    assert decision.status==ConfidenceLevel.REJECTED and decision.oracle is None
    assert decision.confidence is None


@pytest.mark.parametrize("missing",[(OracleId.R3,),tuple(o for o in OracleId if o!=OracleId.L2)])
def test_missing_templates_cannot_establish_confident_seven_position_decision(missing):
    decision=ConfidenceEngine().evaluate(ranking(missing=missing))
    assert decision.status==ConfidenceLevel.LOW and decision.reason=="INCOMPLETE_BANK"
    assert decision.oracle is None


def test_tie_never_accepted_even_when_margin_setting_is_zero():
    settings=RecognitionSettings(margin_threshold=0,high_margin_threshold=0)
    decision=ConfidenceEngine(settings).evaluate(ranking(1,1,status="TIED"))
    assert decision.status==ConfidenceLevel.LOW and decision.oracle is None


@pytest.mark.parametrize("value",[math.nan,math.inf,-.1,1.1,True])
def test_invalid_scores_are_rejected(value):
    raw=ranking()
    raw=replace(raw,ranking=(replace(raw.ranking[0],score=value),*raw.ranking[1:]))
    decision=ConfidenceEngine().evaluate(raw)
    assert decision.reason=="INVALID_RANKING" and decision.oracle is None
    assert decision.confidence is None


@pytest.mark.parametrize("kind",["unsorted","repeated","component"])
def test_inconsistent_ranking_is_rejected(kind):
    raw=ranking()
    rows=list(raw.ranking)
    if kind=="unsorted":rows[0],rows[1]=rows[1],rows[0]
    elif kind=="repeated":rows[1]=replace(rows[1],oracle=rows[0].oracle)
    else:rows[0]=replace(rows[0],spectrum_score=math.nan)
    assert ConfidenceEngine().evaluate(replace(raw,ranking=tuple(rows))).reason=="INVALID_RANKING"


def live(timestamp,stream="capture-A"):
    return EventContext("live",timestamp,stream)


def test_duplicate_cooldown_is_per_oracle_and_anchored_to_accepted_event():
    engine=ConfidenceEngine()
    first=engine.evaluate(ranking(),live(10))
    duplicate=engine.evaluate(ranking(),live(10.2))
    other=engine.evaluate(ranking(oracle=OracleId.R1),live(10.3))
    again=engine.evaluate(ranking(),live(10.45))
    assert first.oracle==OracleId.L2 and other.oracle==OracleId.R1
    assert duplicate.duplicate and duplicate.oracle is None and duplicate.status=="HIGH"
    assert again.oracle==OracleId.L2 and not again.duplicate


def test_low_and_rejected_do_not_block_later_confident_event():
    engine=ConfidenceEngine()
    assert not engine.evaluate(ranking(.83,.82),live(1)).accepted
    assert not engine.evaluate(ranking(.1,.09),live(1.1)).accepted
    assert engine.evaluate(ranking(),live(1.2)).accepted


def test_frozen_live_audio_stays_duplicate_regardless_of_analysis_delay():
    engine=ConfidenceEngine()
    assert engine.evaluate(ranking(),live(10)).accepted
    for _ in range(5):
        assert engine.evaluate(ranking(),live(10)).duplicate


def test_replay_is_repeatable_and_does_not_reset_live_history():
    engine=ConfidenceEngine()
    engine.evaluate(ranking(),live(10))
    first=engine.evaluate(ranking())
    assert first.accepted and not first.duplicate
    assert all(engine.evaluate(ranking())==first for _ in range(5))
    assert engine.evaluate(ranking(),live(10.2)).duplicate


def test_zero_cooldown_and_new_stream_and_reset():
    engine=ConfidenceEngine(RecognitionSettings(duplicate_cooldown=0))
    assert engine.evaluate(ranking(),live(10)).accepted
    assert engine.evaluate(ranking(),live(10)).accepted
    engine=ConfidenceEngine()
    engine.evaluate(ranking(),live(10))
    assert engine.evaluate(ranking(),live(.1,"capture-B")).accepted
    engine.reset()
    assert engine.evaluate(ranking(),live(.1,"capture-B")).accepted


def test_out_of_order_event_does_not_overwrite_cooldown_history():
    engine=ConfidenceEngine()
    engine.evaluate(ranking(),live(10))
    stale=engine.evaluate(ranking(),live(9))
    assert stale.reason=="STALE_EVENT" and stale.oracle is None and stale.status=="REJECTED"
    assert engine.evaluate(ranking(),live(10.2)).duplicate


def test_configuration_is_copied_and_thresholds_can_be_changed():
    settings=RecognitionSettings(confidence_threshold=.70,high_confidence_threshold=.80,
                                 margin_threshold=.05,high_margin_threshold=.10)
    engine=ConfidenceEngine(settings)
    settings.high_confidence_threshold=1
    assert engine.evaluate(ranking(.83,.72)).status=="HIGH"


@pytest.mark.parametrize("field,value",[("duplicate_cooldown",-1),("margin_threshold",math.nan),
                                      ("confidence_threshold",.1)])
def test_invalid_configuration_fails_before_analysis(field,value):
    settings=RecognitionSettings()
    setattr(settings,field,value)
    with pytest.raises(ConfigValidationError):ConfidenceEngine(settings)


@pytest.mark.parametrize("kwargs",[{"source":"live"},{"timestamp":math.nan},{"timestamp":-1},
                                 {"start_frame":1},{"start_frame":2,"end_frame":1}])
def test_invalid_event_context_is_not_used(kwargs):
    with pytest.raises(ValueError):EventContext(**kwargs)

def test_confidence_policy_is_independent_of_valid_native_dsp_configuration():
    from config.schema import AppConfig
    config=AppConfig()
    config.audio.internal_sample_rate=192000
    config.recognition.bandpass_enabled=True
    config.recognition.bandpass_high_hz=50000
    config.validate()
    assert ConfidenceEngine(config.recognition).evaluate(ranking()).status=="HIGH"
