"""Manual round policy preserves confirmed evidence until an explicit command."""
from dataclasses import replace
import pytest

from tests.unit.test_sequence import presentations
from encounter.sequence import SequenceState


@pytest.mark.parametrize("round_index", [1, 5])
def test_manual_policy_keeps_confirmed_sequence_after_long_silence(round_index):
    engine, _ = presentations(round_index)
    engine.settings = replace(engine.settings, auto_advance=False)
    now = engine.snapshot().timestamp
    confirmed = engine.confirm(now)
    engine.advance(now, silent=True)
    result = engine.advance(now + 60, silent=True)
    assert result.state == SequenceState.LOCKOUT
    assert result.round_index == round_index
    assert result.final_sequence == confirmed.final_sequence
    assert result.pass1 == confirmed.pass1 and result.pass2 == confirmed.pass2
    explicit = engine.next_round(now + 60)
    assert explicit.state == (SequenceState.IDLE if round_index == 5 else SequenceState.ARMED)
    assert explicit.round_index == (5 if round_index == 5 else 2)
