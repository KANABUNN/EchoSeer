"""Short template onsets remain deterministic across live callback chunks."""

import weakref
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from detector.classifier import OracleClassifier, TemplateWaveform
from detector.onset_matcher import (
    OnsetMatcherResourceLimit, TemplateOnsetMatcher, build_optional_onset_matcher,
)
from dsp.preprocess import preprocess_clip
from encounter.vog_oracles import OracleId
from templates.manager import TemplateManager


RATE = 8000


def pattern(seed: int, rate: int = RATE, seconds: float = 0.64) -> AudioClip:
    rng = np.random.default_rng(seed)
    source = rng.normal(0, 1, round(rate * seconds) + 3)
    wave = np.convolve(source, np.array([0.15, 0.35, 0.35, 0.15]), mode="valid")
    wave = (wave / np.max(np.abs(wave)) * 0.2).astype(np.float32)
    return AudioClip(np.stack((wave, np.roll(wave, seed % 3 + 1)), axis=1), rate)


def classifier(rate: int = RATE) -> tuple[OracleClassifier, dict[OracleId, AudioClip]]:
    clips = {
        OracleId.L1: pattern(11, rate),
        OracleId.MID: pattern(23, rate),
        OracleId.R2: pattern(37, rate),
    }
    templates = tuple(
        TemplateWaveform(oracle, oracle.value, clip) for oracle, clip in clips.items()
    )
    return OracleClassifier(templates=templates), clips


def chunks(values: np.ndarray, sizes: tuple[int, ...]):
    start = index = 0
    while start < len(values):
        size = sizes[index % len(sizes)]
        yield values[start:start + size]
        start += size
        index += 1


def stream(clips: dict[OracleId, AudioClip]) -> tuple[np.ndarray, tuple[int, ...]]:
    quiet = np.full((round(RATE * 0.25) + 37, 2), 0.03, dtype=np.float32)
    gap = np.full((round(RATE * 1.05), 2), 0.03, dtype=np.float32)
    first = clips[OracleId.L1].samples * 0.04 + 0.03
    second = clips[OracleId.R2].samples * 0.7 + 0.03
    values = np.concatenate((quiet, first, gap, second, quiet))
    starts = (len(quiet), len(quiet) + len(first) + len(gap))
    return values, starts


def test_in_memory_matches_are_scale_invariant_and_chunk_independent():
    source, clips = classifier()
    values, starts = stream(clips)
    before = values.copy()

    whole = TemplateOnsetMatcher(source, RATE, 2)
    expected = whole.feed(values)
    split = TemplateOnsetMatcher(source, RATE, 2)
    actual = tuple(
        match
        for part in chunks(values, (17, 333, 1601, 79))
        for match in split.feed(part)
    )

    assert [item.oracle for item in expected] == [OracleId.L1, OracleId.R2]
    assert actual == expected
    assert [item.onset_frame for item in actual] == list(starts)
    assert all(type(item.onset_frame) is int for item in actual)
    assert all(item.score > 0.99 and item.margin > 0.9 for item in actual)
    np.testing.assert_array_equal(values, before)
    assert split.lookback_frames == (
        split.window_frames + split.batch_frames + split.hop_frames
    )
    assert split.retained_frames < split.lookback_frames


def test_manager_templates_are_loaded_once_without_changes(tmp_path: Path):
    manager = TemplateManager(tmp_path / "templates")
    first = manager.add(OracleId.L2, pattern(41, 11025))
    second = manager.add(OracleId.R1, pattern(53, 11025))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    source = OracleClassifier(manager=manager)
    matcher = TemplateOnsetMatcher(source, RATE, 1)

    converted = preprocess_clip(pattern(41, 11025), RATE).samples
    values = np.concatenate((np.zeros((round(RATE * 0.24) + 29, 1), dtype=np.float32), converted,
                             np.zeros((RATE // 10, 1), dtype=np.float32)))
    result = matcher.feed(values)

    assert matcher.template_count == 2
    assert len(result) == 1 and result[0].oracle == OracleId.L2
    assert result[0].onset_frame == round(RATE * 0.24) + 29
    assert result[0].score > 0.99 and result[0].margin > 0.9
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert first.path.is_file() and second.path.is_file()


def test_margin_local_peak_and_refractory_reject_ambiguous_or_repeated_hits():
    cue = pattern(61)
    ambiguous = OracleClassifier(templates=(
        TemplateWaveform(OracleId.L1, "a", cue),
        TemplateWaveform(OracleId.R1, "b", cue),
    ))
    assert not TemplateOnsetMatcher(ambiguous, RATE, 2).feed(
        np.concatenate((cue.samples, np.zeros((RATE // 10, 2), dtype=np.float32)))
    )

    source, clips = classifier()
    pulse = clips[OracleId.MID].samples
    gap_short = np.zeros((round(RATE * 0.1), 2), dtype=np.float32)
    gap_long = np.zeros((round(RATE * 1.0), 2), dtype=np.float32)
    values = np.concatenate((pulse, gap_short, pulse, gap_long, pulse,
                             np.zeros((RATE // 10, 2), dtype=np.float32)))
    result = TemplateOnsetMatcher(source, RATE, 2).feed(values)
    assert [item.oracle for item in result] == [OracleId.MID, OracleId.MID]


def test_single_oracle_template_has_margin_against_no_competitor():
    cue = pattern(71)
    source = OracleClassifier(templates=(
        TemplateWaveform(OracleId.L1, 'only', cue),
    ))
    quiet = np.zeros((RATE // 4, 2), dtype=np.float32)
    matcher = TemplateOnsetMatcher(source, RATE, 2)

    result = matcher.feed(np.concatenate((quiet, cue.samples, quiet)))

    assert len(result) == 1
    assert result[0].oracle == OracleId.L1
    assert result[0].onset_frame == len(quiet)
    assert result[0].score > 0.99
    assert result[0].margin > 0.99


def test_invalid_input_limits_and_cancellation_do_not_return_partial_results():
    source, clips = classifier()
    cancel = Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        TemplateOnsetMatcher(source, RATE, 2, cancel=cancel)

    matcher = TemplateOnsetMatcher(source, RATE, 2)
    with pytest.raises(AudioDataError):
        matcher.feed(np.zeros((10, 1), dtype=np.float32))
    with pytest.raises(AudioDataError):
        matcher.feed(np.full((10, 2), np.nan, dtype=np.float32))
    with pytest.raises(AudioDataError):
        matcher.feed(np.zeros((matcher.max_feed_frames + 1, 2), dtype=np.float32))

    class OversizedArrayLike:
        array_called = False

        def __len__(self):
            return matcher.max_feed_frames + 1

        def __array__(self, *args, **kwargs):
            self.array_called = True
            raise AssertionError("oversized input must not be materialized")

    oversized = OversizedArrayLike()
    with pytest.raises(AudioDataError):
        matcher.feed(oversized)
    assert not oversized.array_called

    class NoLength:
        array_called = False

        def __array__(self, *args, **kwargs):
            self.array_called = True
            return np.zeros((1, 2), dtype=np.float32)

    no_length = NoLength()
    with pytest.raises(AudioDataError):
        matcher.feed(no_length)
    assert not no_length.array_called

    class InvalidArray:
        def __len__(self):
            return 1

        def __array__(self, *args, **kwargs):
            raise ValueError("invalid array protocol")

    with pytest.raises(AudioDataError):
        matcher.feed(InvalidArray())
    with pytest.raises(OperationCancelled):
        matcher.feed(clips[OracleId.L1].samples, cancel)
    assert matcher.retained_frames == 0


def test_pristine_reports_the_complete_stream_lifecycle():
    source, _ = classifier()
    matcher = TemplateOnsetMatcher(source, RATE, 2, start_frame=37)

    assert matcher.pristine
    assert matcher.feed(np.empty((0, 2), dtype=np.float32)) == ()
    assert matcher.pristine

    checkpoint = matcher._checkpoint_stream()
    assert matcher.feed(np.zeros((1, 2), dtype=np.float32)) == ()
    assert not matcher.pristine
    matcher._restore_stream(checkpoint)
    assert matcher.pristine

    assert matcher.finish() == ()
    assert not matcher.pristine


def test_public_feed_accepts_30_seconds_with_bounded_internal_chunks(monkeypatch):
    import detector.onset_matcher as module

    source, _ = classifier()
    matcher = TemplateOnsetMatcher(source, RATE, 2)
    combined_sizes = []
    original = matcher._process

    def tracked(combined, *args, **kwargs):
        combined_sizes.append(len(combined))
        return original(combined, *args, **kwargs)

    monkeypatch.setattr(matcher, "_process", tracked)
    result = matcher.feed(
        np.zeros((matcher.max_feed_frames, 2), dtype=np.float32),
    )

    assert result == ()
    assert len(combined_sizes) > 1
    assert max(combined_sizes) <= (
        matcher.lookback_frames + matcher._feed_chunk_frames
    )
    assert matcher.max_working_bytes <= module.MAX_MATCHER_BYTES


@pytest.mark.parametrize("seconds", [0.79, 1.01])
def test_refractory_range_is_bounded(seconds):
    source, _ = classifier()
    with pytest.raises(ValueError):
        TemplateOnsetMatcher(source, RATE, 2, refractory_seconds=seconds)


def test_finish_returns_pending_eof_peak_once():
    source, clips = classifier()
    matcher = TemplateOnsetMatcher(source, RATE, 2)
    quiet = np.zeros((round(RATE * 0.24) + 41, 2), dtype=np.float32)
    prefix = clips[OracleId.L1].samples[:matcher.window_frames]

    assert matcher.feed(np.concatenate((quiet, prefix))) == ()
    result = matcher.finish()

    assert len(result) == 1 and result[0].oracle == OracleId.L1
    assert result[0].onset_frame == len(quiet)
    assert result[0].score > 0.99
    assert matcher.finish() == ()
    with pytest.raises(AudioDataError):
        matcher.feed(np.zeros((1, 2), dtype=np.float32))

def test_mid_feed_cancellation_rolls_back_stream_state(monkeypatch):
    source, clips = classifier()
    values, _ = stream(clips)
    matcher = TemplateOnsetMatcher(source, RATE, 2)
    expected_matcher = TemplateOnsetMatcher(source, RATE, 2)
    expected = (*expected_matcher.feed(values), *expected_matcher.finish())
    cancel = Event()
    original = matcher._score_batch

    def stop_after_first(samples, start_frame, start_count):
        result = original(samples, start_frame, start_count)
        cancel.set()
        return result

    monkeypatch.setattr(matcher, "_score_batch", stop_after_first)
    with pytest.raises(OperationCancelled):
        matcher.feed(values, cancel)
    assert matcher.retained_frames == 0

    cancel.clear()
    monkeypatch.setattr(matcher, "_score_batch", original)
    actual = (*matcher.feed(values), *matcher.finish())
    assert actual == expected


def test_score_memory_budget_chunks_templates_without_changing_matches(monkeypatch):
    import detector.onset_matcher as module

    oracle_order = tuple(reversed(tuple(OracleId)))
    templates = tuple(
        TemplateWaveform(
            oracle_order[index % len(oracle_order)],
            f"sample-{index}",
            pattern(200 + index),
        )
        for index in range(21)
    )
    source = OracleClassifier(templates=templates)
    quiet = np.zeros((RATE // 4, 2), dtype=np.float32)
    values = np.concatenate((quiet, templates[0].clip.samples, quiet))

    baseline = TemplateOnsetMatcher(source, RATE, 2)
    expected = (*baseline.feed(values), *baseline.finish())

    monkeypatch.setattr(module, "MAX_MATCHER_BYTES", 9 * 1024 * 1024)
    bounded = TemplateOnsetMatcher(source, RATE, 2)
    assert 1 <= bounded._score_chunk_templates < bounded.template_count

    chunk_sizes = []
    inverse_refs = []
    original_irfft = module.irfft

    def tracked_irfft(frequency, *args, **kwargs):
        assert all(reference() is None for reference in inverse_refs)
        chunk_sizes.append(frequency.shape[0])
        result = original_irfft(frequency, *args, **kwargs)
        inverse_refs.append(weakref.ref(result))
        return result

    monkeypatch.setattr(module, "irfft", tracked_irfft)
    actual = (*bounded.feed(values), *bounded.finish())

    assert len(chunk_sizes) > 1
    assert max(chunk_sizes) <= bounded._score_chunk_templates
    assert all(reference() is None for reference in inverse_refs)
    assert bounded.max_working_bytes <= module.MAX_MATCHER_BYTES
    assert [(item.onset_frame, item.oracle) for item in actual] == [
        (item.onset_frame, item.oracle) for item in expected
    ]
    assert [item.score for item in actual] == pytest.approx(
        [item.score for item in expected], abs=1e-6
    )
    assert [item.margin for item in actual] == pytest.approx(
        [item.margin for item in expected], abs=1e-6
    )


def test_384khz_multichannel_feed_and_live_lookahead_are_resource_bounded(
        monkeypatch):
    import detector.onset_matcher as module
    from detector.live_sequence import LiveSequenceSession

    high_rate = 384000
    channels = 16
    cue = pattern(509)
    source = OracleClassifier(templates=(
        TemplateWaveform(OracleId.L1, "only", cue),
    ))
    matcher = TemplateOnsetMatcher(source, high_rate, channels)
    combined_sizes = []
    original = matcher._process

    def tracked(combined, *args, **kwargs):
        combined_sizes.append(len(combined))
        return original(combined, *args, **kwargs)

    monkeypatch.setattr(matcher, "_process", tracked)
    frames = 3 * matcher._feed_chunk_frames + 17
    mono = np.zeros((frames, 1), dtype=np.float32)
    values = np.broadcast_to(mono, (frames, channels))
    assert matcher.feed(values) == ()

    assert len(combined_sizes) == 4
    assert max(combined_sizes) <= (
        matcher.lookback_frames + matcher._feed_chunk_frames
    )
    assert matcher.max_working_bytes <= module.MAX_MATCHER_BYTES

    # The matcher itself fits, but its delayed live queue would exceed 16 MiB.
    # Live construction must retain the viable RMS-only path.
    session = LiveSequenceSession(source, high_rate, channels, "resource-test")
    assert session.detector.onset_matcher is None
    assert not session._matcher_active
    block = np.zeros((session.detector.block, channels), dtype=np.float32)
    assert session.feed(block) == ()


def test_template_preparation_workspace_is_preflighted_before_conversion(
        monkeypatch):
    import detector.onset_matcher as module

    source, _ = classifier()
    calls = []
    original = module.preprocess_clip

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "preprocess_clip", tracked)
    monkeypatch.setattr(module, "MAX_MATCHER_BYTES", int(8.4 * 1024 * 1024))

    with pytest.raises(OnsetMatcherResourceLimit, match="preparation workspace"):
        TemplateOnsetMatcher(source, RATE, 2)

    assert calls == []



def test_optional_matcher_resource_limit_warns_and_falls_back(
        caplog, monkeypatch):
    import detector.onset_matcher as module

    cue = pattern(401)
    templates = tuple(
        TemplateWaveform(
            tuple(OracleId)[index % len(OracleId)], f"sample-{index}", cue,
        )
        for index in range(141)
    )
    source = OracleClassifier(templates=templates)

    caplog.set_level("WARNING")
    assert build_optional_onset_matcher(source, RATE, 2) is None
    assert "resource limit" in caplog.text.lower()

    caplog.clear()
    normal_source, _ = classifier()
    monkeypatch.setattr(
        module, "MAX_MATCHER_BYTES", int(8.2 * 1024 * 1024),
    )
    assert build_optional_onset_matcher(normal_source, RATE, 2) is None
    assert "workspace" in caplog.text.lower()


def test_optional_matcher_does_not_swallow_cancellation_or_other_audio_errors(
        monkeypatch):
    import detector.onset_matcher as module

    source, _ = classifier()
    cancel = Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        build_optional_onset_matcher(source, RATE, 2, cancel=cancel)

    def invalid_matcher(*args, **kwargs):
        raise AudioDataError("invalid matcher input")

    monkeypatch.setattr(module, "TemplateOnsetMatcher", invalid_matcher)
    with pytest.raises(AudioDataError, match="invalid matcher input"):
        build_optional_onset_matcher(source, RATE, 2)
