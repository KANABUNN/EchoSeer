"""Continuous recognition must match finite windows and never join interrupted streams."""
import json
from threading import Event

import numpy as np
import pytest

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio.ring_buffer import RingBuffer
from config.schema import LoggingSettings, RecognitionSettings, SequenceSettings
from detector.events import EventWindow, RmsEventDetector
from detector.live_sequence import LiveSequenceSession
from detector.onset_matcher import TemplateOnsetMatcher
from detector.streaming import StreamStep, StreamingRmsDetector
from encounter.sequence import SequenceState
from encounter.vog_oracles import OracleId
from logging_ext.recognition_logger import RecognitionRecorder
from tests.verification_helpers import RowsClassifier, rows_for
from tests.unit.test_confidence import ranking
from tests.unit.test_onset_matcher import classifier as onset_classifier

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


class ActiveMatcher:
    sample_rate = RATE
    channels = 2
    origin_frame = 0
    lookback_frames = 1
    retained_frames = 0
    pristine = True

    def feed(self, samples, cancel=None):
        return ()

    def finish(self, cancel=None):
        return ()


class ScheduledLiveDetector:
    def __init__(self, windows):
        self.windows = tuple(windows)

    def feed(self, samples, cancel=None):
        for window in self.windows:
            yield StreamStep(
                window.onset_frame, False, True,
                onset_frame=window.onset_frame,
            )
            yield StreamStep(
                window.end_frame, True, False, window=window,
            )


def scheduled_live_windows(specs):
    cue = AudioClip(tone(), RATE)
    result = []
    for spec in specs:
        seconds, matched, *remainder = spec
        reason = remainder[0] if remainder else ""
        onset = round(seconds * RATE)
        result.append(EventWindow(
            cue, onset, onset + cue.frame_count,
            onset, onset + cue.frame_count,
            reason=reason, matched=matched,
        ))
    return tuple(result)


def run_scheduled_live(
    monkeypatch, raws, specs, recognition=None, recorder=None,
):
    import detector.live_sequence as module

    matcher = ActiveMatcher()
    monkeypatch.setattr(
        module, "build_optional_onset_matcher",
        lambda *args, **kwargs: matcher,
    )
    classifier = RowsClassifier(rows_for("CONFIRMED"))
    classifier.rows = tuple(raws)
    classifier.cursor = 0
    session = LiveSequenceSession(
        classifier, RATE, 2, "scheduled", recognition=recognition,
        recorder=recorder,
    )
    windows = scheduled_live_windows(specs)
    session.detector = ScheduledLiveDetector(windows)
    duration = windows[-1].end_frame / RATE + .5
    updates = session.feed(quiet(duration))
    return session, updates


def test_live_event_log_records_matcher_provenance(tmp_path, monkeypatch):
    recorder = RecognitionRecorder(
        tmp_path/"logs", LoggingSettings(uncertain_audio=False),
    )
    run_scheduled_live(
        monkeypatch,
        (ranking(oracle=OracleId.L1),),
        ((.4, True),),
        recorder=recorder,
    )
    payload = json.loads(
        recorder.events.path.read_text(encoding="utf-8").splitlines()[0]
    )

    assert payload["onset_detection"] == {
        "matched": True, "candidate": None, "score": None, "margin": None,
    }


def test_live_onset_matcher_is_optional_for_stubs_and_active_for_templates():
    legacy = LiveSequenceSession(
        RowsClassifier(rows_for("CONFIRMED")), RATE, 2, "legacy",
    )
    assert legacy.detector.onset_matcher is None

    source, _ = onset_classifier(RATE)
    current = LiveSequenceSession(source, RATE, 2, "templates")
    assert isinstance(current.detector.onset_matcher, TemplateOnsetMatcher)
    current.feed(quiet(.1))
    assert current.detector.onset_matcher.retained_frames > 0


def test_live_matcher_mode_ignores_rms_low_but_keeps_matched_low_position(
        monkeypatch):
    session, _ = run_scheduled_live(
        monkeypatch,
        (
            ranking(oracle=OracleId.L1),
            ranking(.83, .82, OracleId.R3),
            ranking(.83, .82, OracleId.R3),
            ranking(oracle=OracleId.L2),
        ),
        ((.4, False), (.9, False), (1.4, True), (1.9, False)),
    )
    snapshot = session.engine.snapshot()

    assert snapshot.ignored_events == 1
    assert [item.oracle for item in snapshot.pass1] == [
        OracleId.L1, None, OracleId.L2,
    ]
    assert snapshot.pass1[1].detection.status == "LOW"


def test_live_matcher_mode_armed_ignores_unmatched_low_but_starts_matched_low(
        monkeypatch):
    session, updates = run_scheduled_live(
        monkeypatch,
        (
            ranking(.83, .82, OracleId.R3),
            ranking(.83, .82, OracleId.R3),
        ),
        ((.4, False), (.9, True)),
    )
    detections = [item.detection for item in updates if item.detection is not None]
    snapshot = session.engine.snapshot()

    assert [item.status for item in detections] == ["LOW", "LOW"]
    assert snapshot.ignored_events == 1
    assert [item.oracle for item in snapshot.pass1] == [None]
    assert not snapshot.pass2


def test_live_restarts_false_singleton_without_duplicate_poisoning(monkeypatch):
    order = (
        OracleId.L3,
        OracleId.L1, OracleId.L2, OracleId.L3,
        OracleId.L1, OracleId.L2, OracleId.L3,
    )
    session, updates = run_scheduled_live(
        monkeypatch,
        tuple(ranking(oracle=item) for item in order),
        (
            (.4, False),
            (2.8, True), (3.4, True), (4.0, True),
            (6.2, True), (6.8, True), (7.4, True),
        ),
        RecognitionSettings(duplicate_cooldown=10),
    )
    confirmed = next(result.snapshot for result in updates if result.snapshot.confirmed)
    detections = [result.detection for result in updates if result.detection is not None]

    assert confirmed.ignored_events == 1
    assert [item.oracle for item in confirmed.pass1] == [
        OracleId.L1, OracleId.L2, OracleId.L3,
    ]
    assert [item.oracle for item in confirmed.pass2] == [
        OracleId.L1, OracleId.L2, OracleId.L3,
    ]
    assert len(detections) == 7
    assert all(not detection.duplicate for detection in detections[1:])
    assert len(session._checksums) == 6
    assert any(
        transition.reason == "PASS_1_RESTARTED"
        for transition in confirmed.transitions
    )


@pytest.mark.parametrize(
    "reason", ["CLIPPED_EVENT", "SHORT_EVENT", "EVENT_LIMIT"],
)
def test_live_structural_window_does_not_poison_same_oracle_duplicate_history(
        monkeypatch, reason):
    session, updates = run_scheduled_live(
        monkeypatch,
        (
            ranking(oracle=OracleId.L2),
            ranking(oracle=OracleId.L1),
            ranking(oracle=OracleId.L1),
        ),
        ((.4, True), (.9, True, reason), (1.4, True)),
        RecognitionSettings(duplicate_cooldown=10),
    )
    detections = [item.detection for item in updates if item.detection is not None]
    structural, following = detections[1:]

    assert structural.reason == reason
    assert structural.status == "REJECTED"
    assert structural.oracle is None
    assert following.oracle == OracleId.L1
    assert not following.duplicate
    assert [item.oracle for item in session.engine.snapshot().pass1] == [
        OracleId.L2, None, OracleId.L1,
    ]


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


@pytest.mark.parametrize("size", [17, 960, 4093, 24000])
def test_pre_warmup_event_releases_at_fixed_quiet_level_in_finite_and_live(size):
    quiet_frames = round(RATE * .9)
    phase = np.arange(quiet_frames)
    low_background = (
        .0071 * np.sin(2 * np.pi * 330 * phase / RATE)
    ).astype(np.float32)
    low_background = np.repeat(low_background[:, None], 2, axis=1)
    values = np.concatenate((tone(.1), low_background))

    finite = list(RmsEventDetector().detect(AudioClip(values, RATE)))
    detector = StreamingRmsDetector(RATE, 2)
    live = [
        step.window
        for part in chunks(values, size)
        for step in detector.feed(part)
        if step.window is not None
    ]
    live.extend(
        step.window for step in detector.finish()
        if step.window is not None
    )

    expected_signal_end = round(RATE * .1)
    expected_end = expected_signal_end + round(
        RATE * RmsEventDetector().release_seconds
    )
    assert len(finite) == len(live) == 1
    assert finite[0].signal_end_frame == live[0].signal_end_frame == expected_signal_end
    assert finite[0].end_frame == live[0].end_frame == expected_end
    assert finite[0].reason == live[0].reason == "CLIPPED_EVENT"
    np.testing.assert_array_equal(finite[0].clip.samples, live[0].clip.samples)

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


@pytest.mark.parametrize("size", [17, 960, 4093, 24000])
def test_streaming_retrigger_matches_overlapping_finite_events(size):
    residual = (.025 * np.sin(
        2 * np.pi * 880 * np.arange(round(RATE * .1)) / RATE
    )).astype(np.float32)
    residual = np.repeat(residual[:, None], 2, axis=1)
    values = np.concatenate([quiet(.2), tone(.16), residual, tone(.16), quiet(.2)])
    finite = list(RmsEventDetector().detect(AudioClip(values, RATE)))
    detector = StreamingRmsDetector(RATE, 2)
    steps = [
        step
        for part in chunks(values, size)
        for step in detector.feed(part)
    ]
    actual = [step.window for step in steps if step.window]
    assert len(actual) == len(finite) == 2
    for first, second in zip(actual, finite):
        assert (
            first.start_frame, first.end_frame, first.onset_frame,
            first.signal_end_frame, first.reason
        ) == (
            second.start_frame, second.end_frame, second.onset_frame,
            second.signal_end_frame, second.reason
        )
        np.testing.assert_array_equal(first.clip.samples, second.clip.samples)
    split_index = next(index for index, step in enumerate(steps) if step.window is actual[0])
    assert steps[split_index].onset_frame is None
    assert steps[split_index + 1].onset_frame == actual[1].onset_frame

def test_live_sequence_accepts_retriggered_cues_in_chronological_order():
    residual = (.025 * np.sin(
        2 * np.pi * 880 * np.arange(round(RATE * .1)) / RATE
    )).astype(np.float32)
    residual = np.repeat(residual[:, None], 2, axis=1)
    presentation = np.concatenate([
        tone(.16), residual, tone(.16), residual, tone(.16), quiet(.2),
    ])
    values = np.concatenate([quiet(.5), presentation, quiet(2.4), presentation])
    session = LiveSequenceSession(
        RowsClassifier(rows_for("CONFIRMED")), RATE, 2, "overlapping"
    )
    updates = [
        result
        for part in chunks(values, 4093)
        for result in session.feed(part)
    ]
    confirmed = next(
        result.snapshot
        for result in updates
        if result.snapshot.state == SequenceState.CONFIRMED
    )
    assert [item.value for item in confirmed.final_sequence] == ["L1", "L2", "L3"]
    assert [item.detection.context.timestamp for item in confirmed.pass1] == sorted(
        item.detection.context.timestamp for item in confirmed.pass1
    )


@pytest.mark.parametrize("size", [17, 960, 4093, 24000])
def test_streaming_adaptive_low_level_boundaries_match_finite_detector(size):
    floor = tone(.4) * (.001 / .15)
    cue = tone(.18) * (.02 / .15)
    tail = tone(.9) * (.006 / .15)
    ending = tone(.2) * (.001 / .15)
    values = np.concatenate([floor, cue, tail, cue, tail, cue, ending])
    finite = list(RmsEventDetector().detect(AudioClip(values, RATE)))
    detector = StreamingRmsDetector(RATE, 2)
    actual = [
        step.window
        for part in chunks(values, size)
        for step in detector.feed(part)
        if step.window
    ]
    assert len(actual) == len(finite) == 3
    for first, second in zip(actual, finite):
        assert (
            first.start_frame, first.end_frame, first.onset_frame,
            first.signal_end_frame, first.reason,
        ) == (
            second.start_frame, second.end_frame, second.onset_frame,
            second.signal_end_frame, second.reason,
        )
        np.testing.assert_array_equal(first.clip.samples, second.clip.samples)


def test_live_ignores_below_low_prelude_before_a_valid_presentation():
    classifier = RowsClassifier(rows_for("CONFIRMED"))
    classifier.rows = (ranking(.2, .1), *classifier.rows)
    values = np.concatenate([quiet(.5), tone(), quiet(.5), round_audio()])
    session = LiveSequenceSession(classifier, RATE, 2, "prelude")
    updates = [
        result
        for part in chunks(values, 4093)
        for result in session.feed(part)
    ]
    confirmed = next(result.snapshot for result in updates if result.snapshot.confirmed)
    assert [item.value for item in confirmed.final_sequence] == ["L1", "L2", "L3"]
    assert confirmed.ignored_events == 1
