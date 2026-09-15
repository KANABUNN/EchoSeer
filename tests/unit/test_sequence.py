"""All FSM states, count boundaries, quiet lockout and safe verification hooks."""
from dataclasses import replace
import math
import pytest
from config.schema import SequenceSettings
from detector.confidence import ConfidenceEngine,EventContext
from encounter.definition import VOG_ORACLES,EncounterDefinition
from encounter.sequence import SequenceEngine,SequenceEntry,SequenceState
from encounter.vog_oracles import OracleId
from tests.unit.test_confidence import ranking


def entry(oracle=OracleId.L1,start=1.,duration=.2,unknown=False,duplicate=False,stream=None):
    raw=ranking(.83,.82,oracle) if unknown else ranking(oracle=oracle)
    context=EventContext("live" if stream else "replay",start,stream)
    detection=ConfidenceEngine().evaluate(raw,context)
    if duplicate:detection=replace(detection,oracle=None,duplicate=True,reason="DUPLICATE")
    return SequenceEntry(detection,raw,start+duration)


def presentations(round_index=1,unknown_index=None,second_order=None):
    engine=SequenceEngine()
    engine.arm(0,round_index)
    count=VOG_ORACLES.expected_count(round_index)
    order=list(OracleId)[:count]
    now=1.
    for index,oracle in enumerate(order):
        engine.add(entry(oracle,now,unknown=index==unknown_index))
        now=engine.snapshot().pass1[-1].end_time+.5
    first=engine.snapshot()
    now=first.pass1[-1].end_time+2.
    for oracle in second_order or order:
        engine.add(entry(oracle,now))
        now=engine.snapshot().pass2[-1].end_time+.5
    return engine,first


@pytest.mark.parametrize("round_index,count",[(1,3),(2,4),(3,5),(4,6),(5,7)])
def test_expected_counts_and_independent_passes_stop_at_verify(round_index,count):
    engine,first=presentations(round_index)
    result=engine.snapshot()
    assert first.state=="WAIT_PASS_2" and len(first.pass1)==count and not first.pass2
    assert result.state=="VERIFY" and len(result.pass1)==len(result.pass2)==count
    assert result.pass1==first.pass1 and result.pass2 is not result.pass1
    assert not result.confirmed and result.final_sequence is None
    assert [t.after.value for t in result.transitions]==["ARMED","PASS_1","WAIT_PASS_2","PASS_2","VERIFY"]


def test_idle_and_terminal_candidates_are_ignored_even_when_stale():
    engine=SequenceEngine()
    assert engine.add(entry(start=1)).state=="IDLE"
    engine.reset()
    engine,first=presentations()
    result=engine.add(entry(start=0))
    assert result.state=="VERIFY" and result.ignored_events==1
    assert result.pass1==first.pass1


def test_unknown_keeps_position_and_complete_second_pass():
    engine,first=presentations(unknown_index=1)
    result=engine.snapshot()
    assert result.state=="UNCERTAIN" and result.reason=="UNKNOWN_EVENT"
    assert len(result.pass1)==len(result.pass2)==3
    assert result.pass1[1].oracle is None and result.pass1[1].detection.best_candidate is not None
    assert result.pass2[1].oracle==OracleId.L2
    assert result.final_sequence is None
    with pytest.raises(ValueError):engine.confirm(result.timestamp)


def test_duplicate_does_not_consume_position():
    engine=SequenceEngine();engine.arm()
    engine.add(entry(start=1))
    duplicate=engine.add(entry(start=1.25,duplicate=True))
    assert len(duplicate.pass1)==1 and duplicate.ignored_events==1
    engine.add(entry(OracleId.L2,1.7))
    assert len(engine.snapshot().pass1)==2


def test_first_pass_gap_before_expected_count_is_uncertain():
    engine=SequenceEngine();engine.arm()
    engine.add(entry(start=1))
    result=engine.add(entry(OracleId.L2,3.2))
    assert result.state=="UNCERTAIN" and result.reason=="INCOMPLETE_PASS"
    assert len(result.pass1)==1 and not result.pass2


@pytest.mark.parametrize("gap,allowed", [
    (1.999, False), (2.0, True), (4.999, True), (5.0, False),
])
def test_singleton_pass_restart_has_closed_gap_boundaries(gap, allowed):
    engine = SequenceEngine()
    engine.arm()
    first = engine.add(entry(start=1, stream="A"))
    last = first.pass1[-1].end_time
    now = last + gap

    assert engine.can_restart_incomplete_pass(now) is allowed
    if not allowed:
        with pytest.raises(ValueError):
            engine.restart_incomplete_pass(now)
        assert engine.snapshot() == first
        return

    restarted = engine.restart_incomplete_pass(now)
    assert restarted.state == SequenceState.ARMED
    assert not restarted.pass1 and not restarted.pass2
    assert restarted.ignored_events == 1
    transition = restarted.transitions[-1]
    assert (
        transition.before, transition.after, transition.reason,
    ) == (
        SequenceState.PASS_1, SequenceState.ARMED, "PASS_1_RESTARTED",
    )


def test_restart_refuses_two_positions_and_preserves_source_identity():
    engine = SequenceEngine()
    engine.arm()
    engine.add(entry(start=1, stream="A"))
    second = engine.add(entry(OracleId.L2, start=1.7, stream="A"))
    now = second.pass1[-1].end_time + 2

    assert not engine.can_restart_incomplete_pass(now)
    with pytest.raises(ValueError):
        engine.restart_incomplete_pass(now)
    assert engine.snapshot() == second
    incomplete = engine.add(entry(OracleId.L3, start=now, stream="A"))
    assert incomplete.state == SequenceState.UNCERTAIN
    assert incomplete.reason == "INCOMPLETE_PASS"
    assert len(incomplete.pass1) == 2

    engine = SequenceEngine()
    engine.arm()
    first = engine.add(entry(start=1, stream="A"))
    now = first.pass1[-1].end_time + 2
    engine.restart_incomplete_pass(now)
    changed = engine.add(entry(OracleId.L2, start=now, stream="B"))
    assert changed.state == SequenceState.UNCERTAIN
    assert changed.reason == "SOURCE_CHANGED"
    assert not changed.pass1 and changed.ignored_events == 2


@pytest.mark.parametrize("gap,expected",[(1.999,"UNCERTAIN"),(2.,"PASS_2")])
def test_pass_gap_boundary(gap,expected):
    engine,_=presentations()
    engine=SequenceEngine();engine.arm()
    for index,oracle in enumerate(list(OracleId)[:3]):engine.add(entry(oracle,1+index*.7))
    last=engine.snapshot().pass1[-1].end_time
    result=engine.add(entry(start=last+gap))
    assert result.state==expected
    assert len(result.pass2)==(1 if expected=="PASS_2" else 0)


def test_gap_alone_does_not_start_second_pass():
    engine=SequenceEngine();engine.arm()
    for index,oracle in enumerate(list(OracleId)[:3]):engine.add(entry(oracle,1+index*.7))
    last=engine.snapshot().pass1[-1].end_time
    assert engine.advance(last+2,silent=True).state=="WAIT_PASS_2"


@pytest.mark.parametrize("waiting",["PASS_1","WAIT_PASS_2","PASS_2"])
def test_event_timeout_boundaries(waiting):
    engine=SequenceEngine();engine.arm()
    engine.add(entry())
    if waiting!="PASS_1":
        engine.add(entry(OracleId.L2,1.7));engine.add(entry(OracleId.L3,2.4))
    if waiting=="PASS_2":engine.add(entry(start=4.6))
    snapshot=engine.snapshot()
    last=(snapshot.pass2 or snapshot.pass1)[-1].end_time
    deadline=7 if waiting=="WAIT_PASS_2" else 5
    assert engine.advance(last+deadline-.001,silent=True).state==waiting
    assert engine.advance(last+deadline,silent=True).reason=="EVENT_TIMEOUT"


def test_duration_is_excluded_from_between_event_timeout():
    engine=SequenceEngine();engine.arm()
    engine.add(entry(start=1,duration=4))
    assert engine.add(entry(OracleId.L2,5.5)).state=="PASS_1"


@pytest.mark.parametrize("count,reason",[(0,"NO_EVENTS"),(1,"INCOMPLETE_INPUT"),(3,"INCOMPLETE_INPUT")])
def test_end_of_input_does_not_invent_missing_positions(count,reason):
    engine=SequenceEngine();engine.arm()
    for index,oracle in enumerate(list(OracleId)[:count]):engine.add(entry(oracle,1+index*.7))
    result=engine.finish(engine.snapshot().timestamp+.2)
    assert result.state=="UNCERTAIN" and result.reason==reason
    assert len(result.pass1)==count and not result.pass2


def test_confirmed_lockout_requires_duration_and_contiguous_quiet():
    engine,_=presentations()
    now=engine.snapshot().timestamp
    confirmed=engine.confirm(now)
    assert confirmed.state=="CONFIRMED" and confirmed.final_sequence==tuple(list(OracleId)[:3])
    assert engine.advance(now).state=="LOCKOUT"
    assert engine.add(entry(start=now+1)).state=="LOCKOUT"
    assert engine.advance(now+8,silent=True).state=="LOCKOUT"
    assert engine.advance(now+10,silent=True).state=="LOCKOUT"
    result=engine.advance(now+11,silent=True)
    assert result.state=="ARMED" and result.round_index==2 and result.expected_count==4
    assert not result.pass1 and not result.pass2 and result.final_sequence is None
    assert engine.round_history[-1].confirmed


def test_interrupted_quiet_does_not_unlock():
    engine,_=presentations()
    now=engine.snapshot().timestamp
    engine.confirm(now)
    engine.advance(now+1,silent=True)
    engine.advance(now+9,silent=False)
    assert engine.advance(now+10,silent=True).state=="LOCKOUT"
    assert engine.advance(now+12.999,silent=True).state=="LOCKOUT"
    assert engine.advance(now+13,silent=True).state=="ARMED"


def test_mismatch_cannot_be_confirmed_and_verification_can_fail():
    engine,_=presentations(second_order=[OracleId.L1,OracleId.L2,OracleId.R1])
    now=engine.snapshot().timestamp
    with pytest.raises(ValueError):engine.confirm(now)
    result=engine.reject_verification(now)
    assert result.state=="UNCERTAIN" and not result.confirmed


def test_repeated_oracle_cannot_be_confirmed_even_when_both_passes_match():
    engine,_=presentations(second_order=[OracleId.L1,OracleId.L2,OracleId.L3])
    # Build presentations with identical repeated IDs to exercise the safe hook.
    engine=SequenceEngine();engine.arm()
    for start in (1.,1.7,2.4,4.6,5.3,6.):engine.add(entry(OracleId.MID,start))
    assert engine.snapshot().state=="VERIFY"
    with pytest.raises(ValueError):engine.confirm(engine.snapshot().timestamp)


def test_next_round_is_manual_recovery_and_preserves_previous_pass():
    engine=SequenceEngine();engine.arm()
    original=engine.add(entry())
    next_round=engine.next_round(2)
    assert next_round.state=="ARMED" and next_round.round_index==2 and not next_round.pass1
    assert engine.round_history[0].pass1==original.pass1
    result=engine.reset()
    assert result.state=="IDLE" and result.round_index==1 and not engine.round_history


def test_last_round_completes_without_round_six():
    engine,_=presentations(5)
    result=engine.next_round(engine.snapshot().timestamp)
    assert result.state=="IDLE" and result.reason=="ENCOUNTER_COMPLETE" and result.round_index==5


def test_source_change_and_stale_input_do_not_mix_passes():
    engine=SequenceEngine();engine.arm()
    engine.add(entry(stream="A"))
    assert engine.add(entry(OracleId.L2,1.7,stream="B")).reason=="SOURCE_CHANGED"
    engine=SequenceEngine();engine.arm()
    engine.add(entry())
    result=engine.add(entry(OracleId.L2,.5))
    assert result.reason=="STALE_EVENT" and len(result.pass1)==1


def test_settings_are_copied_and_backwards_clock_is_refused():
    settings=SequenceSettings(event_timeout=1)
    engine=SequenceEngine(settings);settings.event_timeout=10
    engine.arm();engine.add(entry())
    assert engine.advance(2.2).reason=="EVENT_TIMEOUT"
    with pytest.raises(ValueError):engine.advance(1)


@pytest.mark.parametrize("field,value",[("pass_gap",0),("event_timeout",math.nan),("silence_duration",True),("lockout_duration",121)])
def test_invalid_settings(field,value):
    settings=SequenceSettings();setattr(settings,field,value)
    with pytest.raises(ValueError):SequenceEngine(settings)


@pytest.mark.parametrize("round_index",[0,6,True])
def test_invalid_round_is_rejected(round_index):
    with pytest.raises(ValueError):SequenceEngine().arm(round_index=round_index)


def test_explicitly_ignored_sound_does_not_start_a_pass():
    engine = SequenceEngine()
    engine.arm()
    result = engine.ignore(1)
    assert result.state == SequenceState.ARMED
    assert not result.pass1 and not result.pass2
    assert result.ignored_events == 1
