"""Continuous recognition must match finite windows and never join interrupted streams."""
from threading import Event

import numpy as np
import pytest

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio.ring_buffer import RingBuffer
from config.schema import RecognitionSettings, SequenceSettings
from detector.events import RmsEventDetector
from detector.live_sequence import LiveSequenceSession
from detector.streaming import StreamingRmsDetector
from encounter.sequence import SequenceState
from tests.verification_helpers import RowsClassifier, rows_for

RATE = 48000


def tone(seconds=.18, channels=2):
    values = (.15 * np.sin(2 * np.pi * 880 * np.arange(round(RATE * seconds)) / RATE)).astype(np.float32)
    return np.repeat(values[:, None], channels, axis=1)


def quiet(seconds, channels=2):
    return np.zeros((round(RATE * seconds), channels), dtype=np.float32)


def round_audio():
    return np.concatenate([quiet(.5), tone(), quiet(.5), tone(), quiet(.5), tone(), quiet(2.6),
                           tone(), quiet(.5), tone(), quiet(.5), tone(), quiet(.2)])


def chunks(values, size):
    for start in range(0, len(values), size):
        yield values[start:start + size]


@pytest.mark.parametrize("size", [17, 960, 4093, 24000])
def test_stream_chunk_boundaries_match_finite_detector(size):
    values = round_audio()
    finite = list(RmsEventDetector().detect(AudioClip(values, RATE)))
    stream = StreamingRmsDetector(RATE, 2)
    actual = [step.window for part in chunks(values, size) for step in stream.feed(part) if step.window]
    assert len(actual) == len(finite) == 6
    for first, second in zip(actual, finite):
        assert (first.start_frame, first.end_frame, first.onset_frame, first.signal_end_frame, first.reason) == (
            second.start_frame, second.end_frame, second.onset_frame, second.signal_end_frame, second.reason)
        np.testing.assert_array_equal(first.clip.samples, second.clip.samples)


def test_long_signal_emits_one_rejection_and_retains_bounded_audio():
    detector = StreamingRmsDetector(RATE, 2)
    windows = []
    for part in chunks(np.concatenate([quiet(.2), tone(12)]), 4093):
        windows.extend(step.window for step in detector.feed(part) if step.window)
        assert detector.retained_frames <= detector.limit + detector.pre_frames + detector.block
    assert len(windows) == 1 and windows[0].reason == "EVENT_LIMIT"
    assert windows[0].clip.frame_count <= RATE * 3
    windows = [step.window for step in detector.feed(np.concatenate([quiet(.2), tone(), quiet(.2)])) if step.window]
    assert len(windows) == 1 and not windows[0].reason


@pytest.mark.parametrize("values", [np.full((20, 2), np.nan), np.zeros((20, 1))])
def test_bad_stream_format_is_rejected(values):
    with pytest.raises(AudioDataError):
        list(StreamingRmsDetector(RATE, 2).feed(values))


def test_streaming_cancellation():
    cancel = Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        list(StreamingRmsDetector(RATE, 2).feed(tone(), cancel))


def test_partial_tail_waits_for_more_audio_without_eof_rejection():
    detector = StreamingRmsDetector(RATE, 2)
    assert not list(detector.feed(quiet(.001)))
    assert detector.retained_frames == 48
    windows = [step.window for step in detector.feed(np.concatenate([quiet(.199), tone(), quiet(.2)])) if step.window]
    assert len(windows) == 1 and not windows[0].reason


def test_read_since_wraps_in_order_and_reports_loss_and_reset():
    ring = RingBuffer(8000, 1, .001)
    ring.write(np.arange(6, dtype=np.float32)[:, None])
    initial = ring.read_since(0, max_frames=3)
    assert initial.latest_frame == 6 and initial.end_frame == 3 and not initial.overrun
    ring.write(np.arange(6, 12, dtype=np.float32)[:, None])
    second = ring.read_since(3, initial.stream_id)
    assert second.overrun and second.start_frame == 4
    np.testing.assert_array_equal(second.samples[:, 0], np.arange(4, 12))
    second.samples[:] = -1
    np.testing.assert_array_equal(ring.snapshot()[:, 0], np.arange(4, 12))
    assert len(ring.read_since(12, initial.stream_id).samples) == 0
    ring.clear()
    ring.write(np.ones((2, 1), dtype=np.float32))
    assert ring.read_since(12, initial.stream_id).overrun


@pytest.mark.parametrize("frame,limit", [(-1, None), (0, 0), (True, 2)])
def test_bad_read_cursor_is_rejected(frame, limit):
    with pytest.raises(ValueError):
        RingBuffer(8000, 1).read_since(frame, max_frames=limit)


@pytest.mark.parametrize("size", [17, 4093, 24000])
def test_live_cooldown_is_scoped_to_each_pass_and_correctly_confirms(size):
    settings = RecognitionSettings(duplicate_cooldown=10)
    session = LiveSequenceSession(RowsClassifier(rows_for("CONFIRMED")), RATE, 2, "stream",
                                  start_frame=1000, time_origin=50, recognition=settings)
    results = [result for part in chunks(round_audio(), size) for result in session.feed(part)]
    confirmed = next(result.snapshot for result in results if result.snapshot.state == SequenceState.CONFIRMED)
    assert [item.value for item in confirmed.final_sequence] == ["L1", "L2", "L3"]
    assert len(confirmed.pass1) == len(confirmed.pass2) == 3
    assert all(not item.detection.duplicate for item in (*confirmed.pass1, *confirmed.pass2))
    assert confirmed.pass1[0].detection.context.start_frame >= 1000


@pytest.mark.parametrize("status", ["INFERRED", "MISMATCH", "CHECK"])
def test_live_uncertain_results_stop_and_preserve_raw_rows(status):
    session = LiveSequenceSession(RowsClassifier(rows_for(status)), RATE, 2, "stream")
    for part in chunks(round_audio(), 4093):
        session.feed(part)
    snapshot = session.engine.snapshot()
    assert snapshot.state == SequenceState.UNCERTAIN and not snapshot.confirmed
    assert snapshot.verification.status.value == status and snapshot.final_sequence is None
    original = snapshot.pass1
    session.feed(np.concatenate([quiet(5), tone(), quiet(.2)]))
    assert session.engine.snapshot().pass1 == original


def test_live_incomplete_pass_timeout_is_check():
    session = LiveSequenceSession(RowsClassifier(rows_for("CONFIRMED")), RATE, 2, "stream")
    session.feed(np.concatenate([quiet(.5), tone(), quiet(6)]))
    snapshot = session.engine.snapshot()
    assert snapshot.state == SequenceState.UNCERTAIN
    assert snapshot.verification.status.value == "CHECK" and snapshot.reason == "EVENT_TIMEOUT"


@pytest.mark.parametrize("reason", ["AUDIO_GAP", "SOURCE_CHANGED", "DEVICE_LOST"])
def test_interruption_removes_confirmation_and_stops_candidate_input(reason):
    session = LiveSequenceSession(RowsClassifier(rows_for("CONFIRMED")), RATE, 2, "stream")
    session.feed(round_audio())
    assert session.engine.snapshot().confirmed
    result = session.invalidate(reason)
    assert not result.snapshot.confirmed and result.snapshot.final_sequence is None
    assert result.snapshot.verification.status.value == "CHECK" and result.snapshot.reason == reason
    assert not session.feed(round_audio())


def test_lockout_ignores_combat_sound_and_advances_after_continuous_quiet():
    session = LiveSequenceSession(RowsClassifier(rows_for("CONFIRMED")), RATE, 2, "stream",
                                  sequence=SequenceSettings(lockout_duration=1, silence_duration=.5))
    session.feed(round_audio())
    assert session.engine.state == SequenceState.LOCKOUT
    session.feed(tone(.8))
    assert session.engine.state == SequenceState.LOCKOUT
    session.feed(quiet(.8))
    snapshot = session.engine.snapshot()
    assert snapshot.state == SequenceState.ARMED and snapshot.round_index == 2
    assert not snapshot.pass1 and not snapshot.pass2 and snapshot.verification is None
    assert len(session.engine.round_history) == 1
