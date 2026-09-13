"""Signal-preserving conversion, stable snapshots and repeatable common analysis."""

from math import ceil
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio.resampler import resample_audio
from audio.ring_buffer import RingBuffer
from audio.sources import ClipSource, LiveSource, WaveFileSource
from audio.waveio import read_wav, write_wav
from dsp.normalization import normalize_audio
from dsp.preprocess import preprocess_clip, remove_dc, to_mono
from replay.analyzer import Analyzer


@pytest.mark.parametrize("rate", [44100, 48000, 192000])
def test_conversion_preserves_frequency_duration_and_mono_shape(rate) -> None:
    time = np.arange(rate) / rate
    signal = 0.15 * np.sin(2 * np.pi * 1000 * time)
    stereo = np.stack((signal + 0.2, signal + 0.4), axis=1).astype(np.float32)
    output = preprocess_clip(AudioClip(stereo, rate))
    assert output.sample_rate == 48000 and output.channels == 1
    assert output.frame_count == 48000
    assert np.max(np.abs(output.samples)) == pytest.approx(0.95, abs=1e-6)
    expected = 0.95 * np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)
    np.testing.assert_allclose(output.samples[100:-100, 0], expected[100:-100], atol=0.003)


def test_resampling_filters_frequencies_above_target_nyquist() -> None:
    input = np.sin(2 * np.pi * 12000 * np.arange(44100) / 44100).astype(np.float32)
    output = resample_audio(input, 44100, 8000)
    assert len(output) == 8000
    assert np.sqrt(np.mean(output[100:-100] ** 2)) < 0.001


@pytest.mark.parametrize("length", [0, 1, 3, 44099, 44101])
def test_resampling_has_exact_rational_frame_count(length) -> None:
    output = resample_audio(np.zeros((length, 2), dtype=np.float32), 44100, 48000)
    assert output.shape == (ceil(length * 160 / 147), 2)


def test_channel_average_and_dc_removal_do_not_amplify_silence() -> None:
    phase_opposed = np.array([[0.2, -0.2], [0.4, -0.4]], dtype=np.float32)
    np.testing.assert_array_equal(to_mono(phase_opposed), [0, 0])
    np.testing.assert_array_equal(remove_dc(np.full(10, 0.2, dtype=np.float32)), np.zeros(10))
    np.testing.assert_array_equal(normalize_audio(np.array([1e-9, -1e-9], dtype=np.float32)), [0, 0])
    assert np.max(np.abs(normalize_audio(np.array([-2, 1], dtype=np.float32)))) == pytest.approx(0.95)


def test_clip_owns_samples_and_source_blocks_are_repeatable() -> None:
    source_values = np.arange(31, dtype=np.float32)[:, None] / 31
    clip = AudioClip(source_values, 48000)
    source_values[:] = 0
    source = ClipSource(clip)
    for size in (1, 7, 16, 100):
        blocks = list(source.blocks(size))
        np.testing.assert_array_equal(np.concatenate(blocks), clip.samples)
    with pytest.raises(ValueError):
        clip.samples[0] = 0


def test_live_dump_and_wav_share_identical_analyzer_samples(tmp_path: Path) -> None:
    rate = 44100
    samples = np.sin(2 * np.pi * 670 * np.arange(rate + 17) / rate).astype(np.float32)
    ring = RingBuffer(rate, 2, 0.5)
    native = np.stack((samples * 0.2 + 0.1, samples * 0.4 - 0.03), axis=1)
    ring.write(native[:20000])
    ring.write(native[20000:])
    path = ring.dump(tmp_path / "ring.wav")
    analyzer = Analyzer()
    live = analyzer.analyze(LiveSource(ring))
    wave_source = WaveFileSource(path)
    for _ in range(5):
        replay = analyzer.analyze(wave_source)
        np.testing.assert_array_equal(replay.original.samples, live.original.samples)
        np.testing.assert_array_equal(replay.processed.samples, live.processed.samples)
        assert replay.checksum == live.checksum
    assert live.processed.frame_count == 24000
    np.testing.assert_array_equal(read_wav(path).samples, native[-22050:])


def test_retained_live_copy_is_independent_of_later_buffer_writes() -> None:
    ring = RingBuffer(48000, 2, 5)
    samples = np.random.default_rng(7).normal(size=(200, 2)).astype(np.float32)
    ring.write(samples)
    original = LiveSource(ring, 123).read()
    ring.write(np.zeros((300, 2), dtype=np.float32))
    ring.clear()
    np.testing.assert_array_equal(original.samples, samples[-123:])
    source = ClipSource(original)
    assert Analyzer().analyze(source).checksum == Analyzer().analyze(source).checksum


def test_replay_reopens_changed_file_and_internal_rate_is_configurable(tmp_path: Path) -> None:
    path = tmp_path / "changing.wav"
    source = WaveFileSource(path)
    rng = np.random.default_rng(10)
    write_wav(path, AudioClip(rng.normal(size=1000).astype(np.float32), 44100))
    first = Analyzer(24000).analyze(source)
    write_wav(path, AudioClip(rng.normal(size=1000).astype(np.float32), 44100))
    second = Analyzer(24000).analyze(source)
    assert first.checksum != second.checksum and second.processed.sample_rate == 24000


def test_silence_clipping_invalid_audio_and_cancellation_are_reported() -> None:
    silence = Analyzer().analyze(ClipSource(AudioClip(np.ones(50, dtype=np.float32), 48000)))
    assert silence.processed_level.peak == 0 and len(silence.notices) == 2
    with pytest.raises(AudioDataError):
        AudioClip(np.array([float("nan")]), 48000)
    with pytest.raises(AudioDataError):
        Analyzer().analyze(ClipSource(AudioClip(np.empty(0, dtype=np.float32), 48000)))
    cancel = Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        Analyzer().analyze(ClipSource(AudioClip(np.zeros(5, dtype=np.float32), 48000)), cancel)


def test_large_resampling_output_is_rejected_before_conversion(monkeypatch) -> None:
    from replay import analyzer
    monkeypatch.setattr(analyzer, "MAX_PROCESSED_BYTES", 100)
    source = ClipSource(AudioClip(np.zeros(10, dtype=np.float32), 8000))
    def must_not_convert(*args, **kwargs):
        raise AssertionError("Oversized output reached conversion")
    monkeypatch.setattr(analyzer, "preprocess_clip", must_not_convert)
    with pytest.raises(AudioDataError, match="256 MiB"):
        Analyzer(192000).analyze(source)
