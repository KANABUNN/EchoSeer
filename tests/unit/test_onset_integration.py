"""Registered onset signatures split continuous finite and live RMS events."""

from threading import Event

import numpy as np
import pytest

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled, check_cancel
from detector.classifier import OracleClassifier, TemplateWaveform
from detector.events import RmsEventDetector
from detector.live_sequence import LiveSequenceSession
from detector.onset_matcher import (
    OnsetMatch, TemplateOnsetMatcher, build_optional_onset_matcher,
)
from detector.streaming import StreamingRmsDetector
from encounter.vog_oracles import OracleId


RATE = 8000
CHANNELS = 2
BLOCK_FRAMES = round(RATE * 0.02)
PRE_ROLL_FRAMES = round(RATE * 0.06)
BGM_START = 8000
BGM_STOP = 43520
CHUNK_SIZES = (17, 333, 1601, 4093)


def _pattern(seed: int, seconds: float = 0.64) -> AudioClip:
    rng = np.random.default_rng(seed)
    source = rng.normal(0, 1, round(RATE * seconds) + 3)
    wave = np.convolve(
        source, np.array([0.15, 0.35, 0.35, 0.15]), mode="valid",
    )
    wave = (wave / np.max(np.abs(wave)) * 0.2).astype(np.float32)
    return AudioClip(
        np.stack((wave, np.roll(wave, seed % 3 + 1)), axis=1), RATE,
    )


def _registered() -> tuple[OracleClassifier, dict[OracleId, AudioClip]]:
    clips = {
        OracleId.L1: _pattern(11),
        OracleId.MID: _pattern(23),
        OracleId.R2: _pattern(37),
    }
    templates = tuple(
        TemplateWaveform(oracle, oracle.value, clip)
        for oracle, clip in clips.items()
    )
    return OracleClassifier(templates=templates), clips


def _continuous_bgm(frame_count: int, start_frame: int) -> np.ndarray:
    """Keep every native RMS block above the fixed quiet threshold.

    Isolated low blocks preserve a stable adaptive floor without remaining low
    for the detector's 120 ms release duration.
    """
    result = np.empty((frame_count, CHANNELS), dtype=np.float32)
    for offset in range(0, frame_count, BLOCK_FRAMES):
        stop = min(frame_count, offset + BLOCK_FRAMES)
        amplitude = 0.08 if (offset // BLOCK_FRAMES) % 3 == 0 else 0.02
        frames = np.arange(start_frame + offset, start_frame + stop)
        result[offset:stop, 0] = amplitude * np.sin(
            2 * np.pi * 137 * frames / RATE,
        )
        result[offset:stop, 1] = amplitude * np.sin(
            2 * np.pi * 223 * frames / RATE + 0.4,
        )
    return result


def _audio_with_cues(
    cues: tuple[tuple[int, OracleId], ...], *, frame_count: int, bgm_stop: int,
) -> np.ndarray:
    _, clips = _registered()
    values = np.zeros((frame_count, CHANNELS), dtype=np.float32)
    values[BGM_START:bgm_stop] = _continuous_bgm(
        bgm_stop - BGM_START, BGM_START,
    )
    for onset, oracle in cues:
        clip = clips[oracle].samples
        values[onset:onset + len(clip)] += clip
    return values


def _matcher(classifier: OracleClassifier) -> TemplateOnsetMatcher:
    matcher = build_optional_onset_matcher(classifier, RATE, CHANNELS)
    assert matcher is not None
    return matcher


def _finite(values: np.ndarray, classifier: OracleClassifier):
    return list(RmsEventDetector().detect(
        AudioClip(values, RATE), onset_matcher=_matcher(classifier),
    ))


def _live(values: np.ndarray, classifier: OracleClassifier, chunk_size: int):
    detector = StreamingRmsDetector(
        RATE, CHANNELS, onset_matcher=_matcher(classifier),
    )
    windows = [
        step.window
        for start in range(0, len(values), chunk_size)
        for step in detector.feed(values[start:start + chunk_size])
        if step.window is not None
    ]
    windows.extend(step.window for step in detector.finish() if step.window is not None)
    return windows


def _identity(window):
    return (
        window.start_frame,
        window.end_frame,
        window.onset_frame,
        window.signal_end_frame,
        window.reason,
        window.matched,
        window.match_oracle,
        window.match_score,
        window.match_margin,
    )


def _assert_raw_slice(window, source: np.ndarray) -> None:
    np.testing.assert_array_equal(
        window.clip.samples, source[window.start_frame:window.end_frame],
    )


def test_registered_onsets_split_continuous_bgm_at_exact_samples():
    classifier, _ = _registered()
    cue_onsets = (9601, 19017, 28433, 37849)
    cues = tuple(zip(
        cue_onsets,
        (OracleId.L1, OracleId.MID, OracleId.R2, OracleId.L1),
        strict=True,
    ))
    values = _audio_with_cues(cues, frame_count=46000, bgm_stop=BGM_STOP)
    before = values.copy()

    fixed_quiet = RmsEventDetector().threshold * 0.4
    bgm_rms = []
    for start in range(BGM_START, BGM_STOP, BLOCK_FRAMES):
        block = values[start:start + BLOCK_FRAMES]
        centered = block - np.mean(block, axis=0, dtype=np.float64)
        bgm_rms.append(float(np.sqrt(np.mean(np.square(centered)))))
    assert min(bgm_rms) > fixed_quiet

    finite = _finite(values, classifier)
    assert [window.onset_frame for window in finite] == [BGM_START, *cue_onsets]
    assert [window.signal_end_frame for window in finite[:-1]] == list(cue_onsets)
    assert finite[0].onset_detection == {
        "matched": False, "candidate": None, "score": None, "margin": None,
    }
    assert [window.match_oracle for window in finite[1:]] == [
        oracle.value for _, oracle in cues
    ]
    assert all(
        window.matched
        and window.match_score >= .60
        and window.match_margin >= .10
        for window in finite[1:]
    )
    for cue, window in zip(cue_onsets, finite[1:], strict=True):
        assert window.start_frame == cue - PRE_ROLL_FRAMES
    for window in finite:
        _assert_raw_slice(window, values)

    for chunk_size in CHUNK_SIZES:
        live = _live(values, classifier, chunk_size)
        assert [_identity(window) for window in live] == [
            _identity(window) for window in finite
        ]
        for actual, expected in zip(live, finite, strict=True):
            np.testing.assert_array_equal(
                actual.clip.samples, expected.clip.samples,
            )
            _assert_raw_slice(actual, values)
    np.testing.assert_array_equal(values, before)


def test_finite_detector_borrows_matcher_without_consuming_it():
    classifier, _ = _registered()
    cue_onsets = (9601, 19017)
    values = _audio_with_cues(
        tuple(zip(
            cue_onsets, (OracleId.L1, OracleId.MID), strict=True,
        )),
        frame_count=29000,
        bgm_stop=27000,
    )
    matcher = _matcher(classifier)

    first = list(RmsEventDetector().detect(
        AudioClip(values, RATE), onset_matcher=matcher,
    ))
    assert matcher.retained_frames == 0
    second = list(RmsEventDetector().detect(
        AudioClip(values, RATE), onset_matcher=matcher,
    ))

    assert [_identity(window) for window in second] == [
        _identity(window) for window in first
    ]


def test_finite_matcher_cancellation_can_retry_the_same_instance(monkeypatch):
    classifier, _ = _registered()
    values = _audio_with_cues(
        ((9601, OracleId.L1),),
        frame_count=18037,
        bgm_stop=16500,
    )
    matcher = _matcher(classifier)
    cancel = Event()
    original_feed = matcher.feed
    calls = 0

    def cancel_after_first(samples, token=None):
        nonlocal calls
        result = original_feed(samples, token)
        calls += 1
        if calls == 1:
            cancel.set()
        return result

    monkeypatch.setattr(matcher, "feed", cancel_after_first)
    with pytest.raises(OperationCancelled):
        list(RmsEventDetector().detect(
            AudioClip(values, RATE), cancel, matcher,
        ))

    cancel.clear()
    monkeypatch.setattr(matcher, "feed", original_feed)
    actual = list(RmsEventDetector().detect(
        AudioClip(values, RATE), onset_matcher=matcher,
    ))
    expected = _finite(values, classifier)
    assert [_identity(window) for window in actual] == [
        _identity(window) for window in expected
    ]


def test_matcher_recovers_a_valid_event_during_event_limit_suppression():
    classifier, _ = _registered()
    first_onset = 9601
    recovery_onset = 38017
    values = _audio_with_cues(
        (
            (first_onset, OracleId.L1),
            (recovery_onset, OracleId.MID),
        ),
        frame_count=47000,
        bgm_stop=44500,
    )
    finite = _finite(values, classifier)

    assert len(finite) == 3
    limited = finite[1]
    assert (
        limited.onset_frame,
        limited.start_frame,
        limited.end_frame,
        limited.signal_end_frame,
        limited.reason,
    ) == (
        first_onset,
        first_onset - PRE_ROLL_FRAMES,
        first_onset - PRE_ROLL_FRAMES + 3 * RATE,
        first_onset - PRE_ROLL_FRAMES + 3 * RATE,
        "EVENT_LIMIT",
    )
    recovered = finite[2]
    assert recovered.onset_frame == recovery_onset
    assert recovered.start_frame == recovery_onset - PRE_ROLL_FRAMES
    assert recovered.reason == ""
    for window in finite:
        _assert_raw_slice(window, values)

    reference_live = None
    for chunk_size in CHUNK_SIZES:
        live = _live(values, classifier, chunk_size)
        assert len(live) == len(finite)
        if reference_live is None:
            reference_live = live
        else:
            assert [_identity(window) for window in live] == [
                _identity(window) for window in reference_live
            ]
        for actual, expected in zip(live, finite, strict=True):
            assert _identity(actual) == _identity(expected)
            np.testing.assert_array_equal(
                actual.clip.samples, expected.clip.samples,
            )
            _assert_raw_slice(actual, values)


def test_optional_matcher_absence_and_detector_format_mismatch():
    assert build_optional_onset_matcher(
        OracleClassifier(), RATE, CHANNELS,
    ) is None

    short = AudioClip(
        np.ones((round(RATE * 0.4) - 1, CHANNELS), dtype=np.float32), RATE,
    )
    short_only = OracleClassifier(templates=(
        TemplateWaveform(OracleId.L1, "short", short),
    ))
    assert build_optional_onset_matcher(short_only, RATE, CHANNELS) is None

    classifier, _ = _registered()
    nonzero_matcher = TemplateOnsetMatcher(
        classifier, RATE, CHANNELS, start_frame=1,
    )
    with pytest.raises(AudioDataError):
        list(RmsEventDetector().detect(
            AudioClip(np.zeros((RATE, CHANNELS), dtype=np.float32), RATE),
            onset_matcher=nonzero_matcher,
        ))

    dirty_matcher = _matcher(classifier)
    dirty_matcher.feed(np.zeros((17, CHANNELS), dtype=np.float32))
    assert not dirty_matcher.pristine
    with pytest.raises(AudioDataError):
        list(RmsEventDetector().detect(
            AudioClip(np.zeros((RATE, CHANNELS), dtype=np.float32), RATE),
            onset_matcher=dirty_matcher,
        ))
    with pytest.raises(AudioDataError):
        StreamingRmsDetector(RATE, CHANNELS, onset_matcher=dirty_matcher)
    assert dirty_matcher.retained_frames == 17

    finished_matcher = _matcher(classifier)
    finished_matcher.finish()
    assert not finished_matcher.pristine
    with pytest.raises(AudioDataError):
        list(RmsEventDetector().detect(
            AudioClip(np.zeros((RATE, CHANNELS), dtype=np.float32), RATE),
            onset_matcher=finished_matcher,
        ))
    with pytest.raises(AudioDataError):
        StreamingRmsDetector(RATE, CHANNELS, onset_matcher=finished_matcher)

    wrong_formats = ((RATE * 2, CHANNELS), (RATE, 1))
    for sample_rate, channels in wrong_formats:
        samples = np.zeros((sample_rate, channels), dtype=np.float32)
        with pytest.raises(AudioDataError):
            list(RmsEventDetector().detect(
                AudioClip(samples, sample_rate),
                onset_matcher=_matcher(classifier),
            ))
        with pytest.raises(AudioDataError):
            StreamingRmsDetector(
                sample_rate,
                channels,
                onset_matcher=_matcher(classifier),
            )



class _ScheduledMatcher:
    sample_rate = RATE
    channels = CHANNELS
    origin_frame = 0
    lookback_frames = round(RATE * 0.62)
    retained_frames = 0

    def __init__(self, onset_frame: int, discovery_frame: int) -> None:
        self.onset_frame = onset_frame
        self.discovery_frame = discovery_frame
        self.position = 0
        self.sent = False

    @property
    def pristine(self):
        return self.position == 0 and not self.sent

    def feed(self, samples, cancel=None):
        self.position += len(samples)
        if not self.sent and self.position >= self.discovery_frame:
            self.sent = True
            return (OnsetMatch(self.onset_frame, OracleId.L1, 0.95, 0.50),)
        return ()

    def finish(self, cancel=None):
        return ()

    def _checkpoint_stream(self):
        return self.position, self.sent

    def _restore_stream(self, checkpoint):
        self.position, self.sent = checkpoint


class _ScheduledMatches:
    sample_rate = RATE
    channels = CHANNELS
    origin_frame = 0
    lookback_frames = round(RATE * 0.62)
    retained_frames = 0

    def __init__(self, schedule) -> None:
        self.schedule = tuple(schedule)
        self.position = 0
        self.index = 0

    @property
    def pristine(self):
        return self.position == 0 and self.index == 0

    def feed(self, samples, cancel=None):
        check_cancel(cancel)
        self.position += len(samples)
        result = []
        while (
            self.index < len(self.schedule)
            and self.position >= self.schedule[self.index][1]
        ):
            onset, _, oracle = self.schedule[self.index]
            result.append(OnsetMatch(onset, oracle, 0.95, 0.50))
            self.index += 1
        return tuple(result)

    def finish(self, cancel=None):
        check_cancel(cancel)
        return ()

    def _checkpoint_stream(self):
        return self.position, self.index

    def _restore_stream(self, checkpoint):
        self.position, self.index = checkpoint


def _sine_into(values: np.ndarray, start: int, end: int, amplitude: float) -> None:
    frames = np.arange(start, end)
    wave = amplitude * np.sin(2 * np.pi * 440 * frames / RATE)
    values[start:end] = np.repeat(wave[:, None], CHANNELS, axis=1)


def test_same_block_match_closes_event_at_limit_before_starting_matched_window():
    exact_onset = round(RATE * 3.0) + BLOCK_FRAMES // 2
    discovery = exact_onset + _ScheduledMatcher.lookback_frames
    frame_count = (
        discovery + round(RATE * 0.2) + 2 * BLOCK_FRAMES
    )
    frames = np.arange(frame_count)
    mono = (0.20 * np.sin(2 * np.pi * 440 * frames / RATE)).astype(np.float32)
    values = np.repeat(mono[:, None], CHANNELS, axis=1)
    detector = StreamingRmsDetector(
        RATE,
        CHANNELS,
        onset_matcher=_ScheduledMatcher(exact_onset, discovery),
    )

    steps = [*detector.feed(values), *detector.finish()]
    limit_index, limit_step = next(
        (index, step)
        for index, step in enumerate(steps)
        if step.window is not None and step.window.reason == "EVENT_LIMIT"
    )
    match_index, _ = next(
        (index, step)
        for index, step in enumerate(steps)
        if step.onset_frame == exact_onset
    )
    limit_end = detector.limit

    assert limit_index < match_index
    assert limit_step.end_frame == limit_end
    assert limit_step.window.end_frame == limit_end
    assert limit_step.window.signal_end_frame == limit_end
    np.testing.assert_array_equal(
        limit_step.window.clip.samples, values[:limit_end],
    )

    matched = next(
        step.window for step in steps
        if step.window is not None and step.window.matched
    )
    assert matched.onset_frame == exact_onset
    assert matched.start_frame == exact_onset - PRE_ROLL_FRAMES
    _assert_raw_slice(matched, values)


def test_matcher_replaces_a_recent_rough_rms_retrigger_in_finite_and_streaming():
    rough_onset = round(RATE * 0.64)
    exact_onset = round(RATE * 0.80)
    discovery = exact_onset + _ScheduledMatcher.lookback_frames
    values = np.zeros((round(RATE * 2.7), CHANNELS), dtype=np.float32)
    _sine_into(values, round(RATE * 0.40), round(RATE * 0.56), 0.20)
    _sine_into(values, round(RATE * 0.56), rough_onset, 0.02)
    _sine_into(values, rough_onset, round(RATE * 1.80), 0.20)

    finite = list(RmsEventDetector().detect(
        AudioClip(values, RATE),
        onset_matcher=_ScheduledMatcher(exact_onset, discovery),
    ))
    streaming_detector = StreamingRmsDetector(
        RATE, CHANNELS,
        onset_matcher=_ScheduledMatcher(exact_onset, discovery),
    )
    streaming = [
        step.window
        for start in range(0, len(values), 333)
        for step in streaming_detector.feed(values[start:start + 333])
        if step.window is not None
    ]
    streaming.extend(
        step.window for step in streaming_detector.finish()
        if step.window is not None
    )

    assert [window.onset_frame for window in finite] == [round(RATE * 0.40), exact_onset]
    assert rough_onset not in [window.onset_frame for window in finite]
    assert finite[0].end_frame == finite[0].signal_end_frame == exact_onset
    assert finite[1].start_frame == exact_onset - PRE_ROLL_FRAMES
    assert [_identity(window) for window in streaming] == [
        _identity(window) for window in finite
    ]
    for window in (*finite, *streaming):
        _assert_raw_slice(window, values)


def test_match_in_limit_crossing_block_closes_both_detectors_at_limit_end():
    first_onset = 9601
    limit_end = first_onset - PRE_ROLL_FRAMES + 3 * RATE
    next_onset = limit_end + 4
    schedule = (
        (
            first_onset,
            first_onset + _ScheduledMatches.lookback_frames,
            OracleId.L2,
        ),
        (
            next_onset,
            next_onset + _ScheduledMatches.lookback_frames,
            OracleId.R1,
        ),
    )
    values = np.zeros((42000, CHANNELS), dtype=np.float32)
    _sine_into(values, first_onset, 36000, .20)

    finite = list(RmsEventDetector().detect(
        AudioClip(values, RATE), onset_matcher=_ScheduledMatches(schedule),
    ))
    streaming_detector = StreamingRmsDetector(
        RATE, CHANNELS, onset_matcher=_ScheduledMatches(schedule),
    )
    streaming = _window_results([
        *streaming_detector.feed(values), *streaming_detector.finish(),
    ])

    assert len(finite) == len(streaming) == 2
    assert [_identity(window) for window in streaming] == [
        _identity(window) for window in finite
    ]
    limited = finite[0]
    assert limited.reason == "EVENT_LIMIT"
    assert limited.end_frame == limited.signal_end_frame == limit_end
    assert finite[1].onset_frame == next_onset
    for window in (*finite, *streaming):
        _assert_raw_slice(window, values)

def test_event_limit_uses_the_same_pass_clock_in_finite_and_streaming(
        monkeypatch):
    import detector.live_sequence as live_module
    import replay.sequence_analyzer as replay_module
    from audio.sources import ClipSource
    from encounter.sequence import SequenceState
    from replay.analyzer import Analyzer
    from replay.sequence_analyzer import ReplaySequenceAnalyzer
    from tests.unit.test_confidence import ranking
    from tests.unit.test_sequence_analyzer import QueueClassifier

    long_onset = round(RATE * 1.20)
    next_onset = round(RATE * 6.40)
    schedule = (
        (
            long_onset,
            long_onset + _ScheduledMatches.lookback_frames,
            OracleId.L2,
        ),
        (
            next_onset,
            next_onset + _ScheduledMatches.lookback_frames,
            OracleId.R1,
        ),
    )

    def build(*args, **kwargs):
        return _ScheduledMatches(schedule)

    monkeypatch.setattr(replay_module, "build_optional_onset_matcher", build)
    monkeypatch.setattr(live_module, "build_optional_onset_matcher", build)

    values = np.zeros((round(RATE * 8.50), CHANNELS), dtype=np.float32)
    _sine_into(values, round(RATE * .30), round(RATE * .60), .20)
    _sine_into(values, long_onset, round(RATE * 7.20), .20)
    rows = (
        ranking(oracle=OracleId.L1),
        ranking(oracle=OracleId.L2),
        ranking(oracle=OracleId.R1),
    )

    analyzer = Analyzer(RATE)
    replay = ReplaySequenceAnalyzer(
        QueueClassifier(rows), analyzer=analyzer,
    ).analyze(analyzer.analyze(ClipSource(AudioClip(values, RATE))))

    live_classifier = QueueClassifier(rows)
    live = LiveSequenceSession(
        live_classifier, RATE, CHANNELS, "event-limit-parity",
    )
    updates = []
    for start in range(0, len(values), 333):
        live.feed(values[start:start + 333], progress=updates.append)

    live_detections = [
        update.detection for update in updates
        if update.detection is not None
    ]
    assert len(replay.traces) == len(live_detections) == 3
    assert [
        (
            trace.detection.timestamp,
            trace.detection.context.start_frame,
            trace.detection.context.end_frame,
            trace.detection.reason,
        )
        for trace in replay.traces
    ] == [
        (
            detection.timestamp,
            detection.context.start_frame,
            detection.context.end_frame,
            detection.reason,
        )
        for detection in live_detections
    ]

    replay_limit = replay.traces[1]
    live_limit = live_detections[1]
    expected_limit_end = long_onset - PRE_ROLL_FRAMES + 3 * RATE
    assert replay_limit.detection.reason == live_limit.reason == "EVENT_LIMIT"
    assert (
        replay_limit.end_frame,
        replay_limit.signal_end_frame,
        live_limit.context.end_frame,
    ) == (expected_limit_end,) * 3

    replay_snapshot = replay.snapshot
    live_snapshot = live.engine.snapshot()
    assert replay_snapshot.state == live_snapshot.state == SequenceState.UNCERTAIN
    assert replay_snapshot.reason == live_snapshot.reason == "INCOMPLETE_PASS"
    assert replay_snapshot.ignored_events == live_snapshot.ignored_events == 1
    assert [
        (entry.timestamp, entry.end_time, entry.oracle)
        for entry in replay_snapshot.pass1
    ] == [
        (entry.timestamp, entry.end_time, entry.oracle)
        for entry in live_snapshot.pass1
    ]
    assert replay_snapshot.pass1[1].end_time == expected_limit_end / RATE


def test_live_matcher_lookback_does_not_move_the_fsm_clock_backwards(monkeypatch):
    from tests.unit.test_sequence_analyzer import StubClassifier
    import detector.live_sequence as module

    onset = round(RATE * 2.50)
    discovery = onset + _ScheduledMatcher.lookback_frames
    matcher = _ScheduledMatcher(onset, discovery)
    monkeypatch.setattr(
        module, "build_optional_onset_matcher", lambda *args, **kwargs: matcher,
    )
    session = LiveSequenceSession(StubClassifier(), RATE, CHANNELS, "lookback")
    values = np.zeros((round(RATE * 4.5), CHANNELS), dtype=np.float32)
    _sine_into(values, 0, round(RATE * 3.3), 0.20)
    updates = []

    for start in range(0, len(values), 333):
        session.feed(
            values[start:start + 333], progress=updates.append,
        )

    timestamps = [result.snapshot.timestamp for result in updates]
    exact_updates = [
        result for result in updates
        if result.detection is not None
        and result.detection.context.timestamp == onset / RATE
    ]
    assert matcher.sent
    assert timestamps == sorted(timestamps)
    assert len(exact_updates) == 1
    exact = exact_updates[0].detection
    assert exact.reason != "SHORT_EVENT"
    assert exact.context.end_frame - exact.context.start_frame >= round(RATE * 0.4)
    assert session.engine.snapshot().timestamp >= onset / RATE


def test_matcher_starts_a_cue_below_the_rms_candidate_floor():
    classifier, clips = _registered()
    onset = round(RATE * 0.50) + 13
    cue = clips[OracleId.L1].samples * 0.02
    values = np.zeros((onset + len(cue) + round(RATE * 1.2), CHANNELS), dtype=np.float32)
    values[onset:onset + len(cue)] = cue
    block_rms = []
    for start in range(onset, onset + len(cue), BLOCK_FRAMES):
        block = cue[start - onset:start - onset + BLOCK_FRAMES]
        centered = block - np.mean(block, axis=0, dtype=np.float64)
        block_rms.append(float(np.sqrt(np.mean(np.square(centered)))))
    assert max(block_rms) < RmsEventDetector().threshold
    assert max(block_rms) < 0.002

    finite = _finite(values, classifier)
    live = _live(values, classifier, 333)

    assert len(finite) == len(live) == 1
    assert finite[0].onset_frame == live[0].onset_frame == onset
    assert _identity(live[0]) == _identity(finite[0])
    _assert_raw_slice(finite[0], values)
    _assert_raw_slice(live[0], values)

def _window_results(steps):
    return [step.window for step in steps if step.window is not None]


def _assert_stream_transaction_unchanged(detector, matcher, snapshot) -> None:
    input_position, position, tail, queued, matches, matcher_state = snapshot
    assert detector._input_position == input_position
    assert detector._position == position
    np.testing.assert_array_equal(detector._tail, tail)
    assert len(detector._queued) == len(queued)
    for actual, expected in zip(detector._queued, queued, strict=True):
        assert actual[:2] == expected[:2]
        np.testing.assert_array_equal(actual[2], expected[2])
    assert tuple(detector._matches) == matches
    current = matcher._checkpoint_stream()
    assert current.buffer_start == matcher_state.buffer_start
    assert current.stream_end == matcher_state.stream_end
    assert current.next_start == matcher_state.next_start
    assert current.older == matcher_state.older
    assert current.previous == matcher_state.previous
    assert current.last_match == matcher_state.last_match
    assert current.finished == matcher_state.finished
    np.testing.assert_array_equal(current.buffer, matcher_state.buffer)


def _stream_transaction_snapshot(detector, matcher):
    return (
        detector._input_position,
        detector._position,
        detector._tail.copy(),
        tuple((start, end, block.copy()) for start, end, block in detector._queued),
        tuple(detector._matches),
        matcher._checkpoint_stream(),
    )


def _assert_full_stream_checkpoint(actual, expected) -> None:
    scalar_fields = (
        "position", "input_position", "matches", "onset", "start", "matched",
        "match_evidence", "stored", "suppress", "finished", "gate",
    )
    for field in scalar_fields:
        assert getattr(actual, field) == getattr(expected, field)
    np.testing.assert_array_equal(actual.tail, expected.tail)
    assert len(actual.queued) == len(expected.queued)
    for current, saved in zip(actual.queued, expected.queued, strict=True):
        assert current[:2] == saved[:2]
        np.testing.assert_array_equal(current[2], saved[2])
    assert len(actual.pre) == len(expected.pre)
    for current, saved in zip(actual.pre, expected.pre, strict=True):
        np.testing.assert_array_equal(current, saved)
    assert len(actual.parts) == len(expected.parts)
    for current, saved in zip(actual.parts, expected.parts, strict=True):
        np.testing.assert_array_equal(current, saved)
    if expected.matcher is None:
        assert actual.matcher is None
    else:
        matcher_fields = (
            "buffer_start", "stream_end", "next_start", "older", "previous",
            "last_match", "finished",
        )
        for field in matcher_fields:
            assert getattr(actual.matcher, field) == getattr(expected.matcher, field)
        np.testing.assert_array_equal(actual.matcher.buffer, expected.matcher.buffer)


def test_streaming_post_matcher_feed_cancellation_restores_every_state_and_retries(
        monkeypatch):
    classifier, _ = _registered()
    values = _audio_with_cues(
        ((9601, OracleId.L1),), frame_count=18037, bgm_stop=16500,
    )
    split = 17000
    matcher = _matcher(classifier)
    detector = StreamingRmsDetector(
        RATE, CHANNELS, onset_matcher=matcher,
    )
    prefix_steps = list(detector.feed(values[:split]))
    before = detector._checkpoint_transaction()
    assert before.queued and before.parts and before.gate.onset_frame is not None
    cancel = Event()
    original = detector._match_transaction

    def cancel_after_matcher(*args, **kwargs):
        result = original(*args, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(detector, "_match_transaction", cancel_after_matcher)
    with pytest.raises(OperationCancelled):
        list(detector.feed(values[split:], cancel))
    _assert_full_stream_checkpoint(detector._checkpoint_transaction(), before)

    cancel.clear()
    monkeypatch.setattr(detector, "_match_transaction", original)
    actual = _window_results([
        *prefix_steps, *detector.feed(values[split:]), *detector.finish(),
    ])
    expected = _live(values, classifier, len(values))
    assert [_identity(item) for item in actual] == [
        _identity(item) for item in expected
    ]
    assert detector._input_position == len(values)
    assert matcher._stream_end == len(values)


def test_streaming_post_matcher_finish_cancellation_restores_every_state_and_retries(
        monkeypatch):
    classifier, clips = _registered()
    onset = round(RATE * .25) + 13
    values = np.zeros(
        (onset + len(clips[OracleId.L1].samples) + 37, CHANNELS),
        dtype=np.float32,
    )
    values[onset:onset + len(clips[OracleId.L1].samples)] = clips[OracleId.L1].samples
    matcher = _matcher(classifier)
    detector = StreamingRmsDetector(
        RATE, CHANNELS, onset_matcher=matcher,
    )
    prefix_steps = list(detector.feed(values))
    before = detector._checkpoint_transaction()
    assert before.queued and not before.finished and not before.matcher.finished
    cancel = Event()
    original = detector._match_transaction

    def cancel_after_matcher(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("finish"):
            cancel.set()
        return result

    monkeypatch.setattr(detector, "_match_transaction", cancel_after_matcher)
    with pytest.raises(OperationCancelled):
        list(detector.finish(cancel))
    _assert_full_stream_checkpoint(detector._checkpoint_transaction(), before)

    cancel.clear()
    monkeypatch.setattr(detector, "_match_transaction", original)
    actual = _window_results([*prefix_steps, *detector.finish()])
    expected = _live(values, classifier, len(values))
    assert [_identity(item) for item in actual] == [
        _identity(item) for item in expected
    ]
    assert detector._finished and matcher._finished
    assert not list(detector.finish())


def test_streaming_feed_cancellation_after_last_step_rolls_back_and_retries(
        monkeypatch):
    frame_count = 3 * BLOCK_FRAMES
    frames = np.arange(frame_count)
    mono = (0.20 * np.sin(2 * np.pi * 440 * frames / RATE)).astype(np.float32)
    values = np.repeat(mono[:, None], CHANNELS, axis=1)
    detector = StreamingRmsDetector(RATE, CHANNELS)
    before = detector._checkpoint_transaction()
    cancel = Event()
    original_feed = detector._feed

    def cancel_after_last_step(samples, token=None):
        yield from original_feed(samples, token)
        cancel.set()

    monkeypatch.setattr(detector, "_feed", cancel_after_last_step)
    with pytest.raises(OperationCancelled):
        list(detector.feed(values, cancel))
    _assert_full_stream_checkpoint(detector._checkpoint_transaction(), before)

    cancel.clear()
    monkeypatch.setattr(detector, "_feed", original_feed)
    actual_steps = [*detector.feed(values), *detector.finish()]
    expected_detector = StreamingRmsDetector(RATE, CHANNELS)
    expected_steps = [
        *expected_detector.feed(values), *expected_detector.finish(),
    ]
    assert [
        (
            step.end_frame, step.silent, step.active, step.onset_frame,
            _identity(step.window) if step.window is not None else None,
        )
        for step in actual_steps
    ] == [
        (
            step.end_frame, step.silent, step.active, step.onset_frame,
            _identity(step.window) if step.window is not None else None,
        )
        for step in expected_steps
    ]
    actual_windows = _window_results(actual_steps)
    expected_windows = _window_results(expected_steps)
    assert len(actual_windows) == len(expected_windows) == 1
    np.testing.assert_array_equal(
        actual_windows[0].clip.samples, expected_windows[0].clip.samples,
    )


def test_streaming_finish_cancellation_after_final_window_rolls_back_and_retries(
        monkeypatch):
    frame_count = 3 * BLOCK_FRAMES + 17
    frames = np.arange(frame_count)
    mono = (0.20 * np.sin(2 * np.pi * 440 * frames / RATE)).astype(np.float32)
    values = np.repeat(mono[:, None], CHANNELS, axis=1)
    detector = StreamingRmsDetector(RATE, CHANNELS)
    prefix_steps = list(detector.feed(values))
    before = detector._checkpoint_transaction()
    assert before.onset is not None and not before.finished
    cancel = Event()
    original_finish = detector._finish

    def cancel_after_final_window(token=None):
        yield from original_finish(token)
        cancel.set()

    monkeypatch.setattr(detector, "_finish", cancel_after_final_window)
    with pytest.raises(OperationCancelled):
        list(detector.finish(cancel))
    _assert_full_stream_checkpoint(detector._checkpoint_transaction(), before)

    cancel.clear()
    monkeypatch.setattr(detector, "_finish", original_finish)
    actual_steps = [*prefix_steps, *detector.finish()]
    expected_detector = StreamingRmsDetector(RATE, CHANNELS)
    expected_steps = [
        *expected_detector.feed(values), *expected_detector.finish(),
    ]
    actual_windows = _window_results(actual_steps)
    expected_windows = _window_results(expected_steps)
    assert len(actual_windows) == len(expected_windows) == 1
    assert _identity(actual_windows[0]) == _identity(expected_windows[0])
    np.testing.assert_array_equal(
        actual_windows[0].clip.samples, expected_windows[0].clip.samples,
    )
    assert detector._finished
    assert not list(detector.finish())


def test_streaming_matcher_feed_cancellation_rolls_back_the_whole_call(monkeypatch):
    classifier, _ = _registered()
    values = _audio_with_cues(
        ((9601, OracleId.L1),), frame_count=18037, bgm_stop=16500,
    )
    matcher = _matcher(classifier)
    detector = StreamingRmsDetector(
        RATE, CHANNELS, onset_matcher=matcher,
    )
    assert not list(detector.feed(values[:17]))
    before = _stream_transaction_snapshot(detector, matcher)
    cancel = Event()
    original = matcher._score_batch

    def cancel_after_scoring(samples, start_frame, start_count):
        result = original(samples, start_frame, start_count)
        cancel.set()
        return result

    monkeypatch.setattr(matcher, "_score_batch", cancel_after_scoring)
    with pytest.raises(OperationCancelled):
        list(detector.feed(values[17:], cancel))
    _assert_stream_transaction_unchanged(detector, matcher, before)

    cancel.clear()
    monkeypatch.setattr(matcher, "_score_batch", original)
    actual_steps = [*detector.feed(values[17:]), *detector.finish()]
    expected = _live(values, classifier, len(values))
    actual = _window_results(actual_steps)
    assert [_identity(window) for window in actual] == [
        _identity(window) for window in expected
    ]
    for first, second in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(first.clip.samples, second.clip.samples)


def test_streaming_matcher_finish_cancellation_restores_tail_and_matcher(monkeypatch):
    classifier, clips = _registered()
    onset = round(RATE * .25) + 13
    values = np.zeros(
        (onset + len(clips[OracleId.L1].samples) + 37, CHANNELS),
        dtype=np.float32,
    )
    values[onset:onset + len(clips[OracleId.L1].samples)] = clips[OracleId.L1].samples
    matcher = _matcher(classifier)
    detector = StreamingRmsDetector(
        RATE, CHANNELS, onset_matcher=matcher,
    )
    prefix_steps = list(detector.feed(values))
    before = _stream_transaction_snapshot(detector, matcher)
    cancel = Event()
    original_finish = matcher.finish

    def cancel_at_finish(token=None):
        cancel.set()
        return original_finish(token)

    monkeypatch.setattr(matcher, "finish", cancel_at_finish)
    with pytest.raises(OperationCancelled):
        list(detector.finish(cancel))
    _assert_stream_transaction_unchanged(detector, matcher, before)

    cancel.clear()
    monkeypatch.setattr(matcher, "finish", original_finish)
    actual = _window_results([*prefix_steps, *detector.finish()])
    expected = _live(values, classifier, len(values))
    assert [_identity(window) for window in actual] == [
        _identity(window) for window in expected
    ]
    for first, second in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(first.clip.samples, second.clip.samples)
    assert not list(detector.finish())


@pytest.mark.parametrize("with_matcher", [False, True])
def test_streaming_rejects_oversized_feed_before_audio_copies(
        monkeypatch, with_matcher):
    classifier, _ = _registered()
    matcher = _matcher(classifier) if with_matcher else None
    detector = StreamingRmsDetector(
        RATE, CHANNELS, onset_matcher=matcher,
    )
    class OversizedArrayLike:
        def __len__(self):
            return detector.max_feed_frames + 1

        def __array__(self, *args, **kwargs):
            pytest.fail("oversized input reached np.asarray")

    class UnsizedArrayLike:
        def __array__(self, *args, **kwargs):
            pytest.fail("unsized input reached np.asarray")

    class OverflowArrayLike:
        def __len__(self):
            return 1

        def __array__(self, *args, **kwargs):
            raise OverflowError

    def unexpected_copy(*args, **kwargs):
        pytest.fail("invalid input reached concatenate")

    monkeypatch.setattr(np, "concatenate", unexpected_copy)
    with pytest.raises(AudioDataError, match="16 MiB"):
        list(detector.feed(OversizedArrayLike()))
    with pytest.raises(AudioDataError):
        list(detector.feed(UnsizedArrayLike()))
    with pytest.raises(AudioDataError):
        list(detector.feed(OverflowArrayLike()))

    assert detector._input_position == detector._position == 0
    assert not detector._queued and not detector._parts and not detector._pre
    assert matcher is None or matcher.pristine


@pytest.mark.parametrize("values", [
    np.zeros((20, CHANNELS), dtype=np.bool_),
    np.full((20, CHANNELS), 1 + 2j, dtype=np.complex64),
    np.full((20, CHANNELS), "0.25", dtype="U4"),
    np.full((20, CHANNELS), 1, dtype=object),
    np.full((20, CHANNELS), 1e300, dtype=np.float64),
    [[0, 0], [1]],
])
def test_streaming_rejects_non_real_numeric_and_unrepresentable_input_without_state_change(values):
    detector = StreamingRmsDetector(RATE, CHANNELS)
    assert not list(detector.feed(np.zeros((17, CHANNELS), dtype=np.float32)))
    before = detector._tail.copy()

    with pytest.raises(AudioDataError):
        list(detector.feed(values))

    assert detector._input_position == detector._position == 0
    np.testing.assert_array_equal(detector._tail, before)


@pytest.mark.parametrize("dtype", [np.int16, np.uint8, np.float64])
def test_streaming_accepts_real_numeric_dtypes_without_mutating_input(dtype):
    values = np.ones((BLOCK_FRAMES + 13, CHANNELS), dtype=dtype)
    before = values.copy()
    detector = StreamingRmsDetector(RATE, CHANNELS)

    list(detector.feed(values))

    np.testing.assert_array_equal(values, before)
    assert detector._input_position == BLOCK_FRAMES
    assert len(detector._tail) == 13


def test_streaming_requires_matcher_origin_and_preserves_nonzero_native_frames():
    classifier, clips = _registered()
    origin = 1234
    matcher = TemplateOnsetMatcher(
        classifier, RATE, CHANNELS, start_frame=origin,
    )
    assert matcher.origin_frame == origin
    with pytest.raises(AudioDataError):
        StreamingRmsDetector(RATE, CHANNELS, onset_matcher=matcher)
    with pytest.raises(AudioDataError):
        StreamingRmsDetector(
            RATE, CHANNELS, start_frame=origin,
            onset_matcher=TemplateOnsetMatcher(classifier, RATE, CHANNELS),
        )

    local_onset = round(RATE * .25) + 13
    values = np.zeros(
        (local_onset + len(clips[OracleId.L1].samples) + RATE, CHANNELS),
        dtype=np.float32,
    )
    values[local_onset:local_onset + len(clips[OracleId.L1].samples)] = (
        clips[OracleId.L1].samples
    )
    detector = StreamingRmsDetector(
        RATE, CHANNELS, start_frame=origin,
        onset_matcher=TemplateOnsetMatcher(
            classifier, RATE, CHANNELS, start_frame=origin,
        ),
    )
    windows = _window_results([*detector.feed(values), *detector.finish()])
    matched = [window for window in windows if window.matched]
    assert len(matched) == 1
    assert matched[0].onset_frame == origin + local_onset
    np.testing.assert_array_equal(
        matched[0].clip.samples,
        values[
            matched[0].start_frame - origin:
            matched[0].end_frame - origin
        ],
    )


def test_live_session_forwards_initialization_cancellation_to_matcher(monkeypatch):
    import detector.live_sequence as module
    from tests.unit.test_sequence_analyzer import StubClassifier

    token = Event()
    seen = []

    def build(*args, cancel=None, **kwargs):
        seen.append(cancel)
        check_cancel(cancel)
        return None

    monkeypatch.setattr(module, "build_optional_onset_matcher", build)
    LiveSequenceSession(StubClassifier(), RATE, CHANNELS, "cancel", cancel=token)
    assert seen == [token]

    token.set()
    with pytest.raises(OperationCancelled):
        LiveSequenceSession(StubClassifier(), RATE, CHANNELS, "cancel", cancel=token)
    assert seen == [token, token]
