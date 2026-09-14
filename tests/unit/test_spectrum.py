"""Spectral pitch/harmonics, decay, finite bounds, workspace and cancellation."""
from dataclasses import replace
from threading import Event
import numpy as np
import pytest
from audio.data import AudioDataError
from audio.operations import OperationCancelled
from dsp.spectrum import spectral_template, spectral_similarity
from tests.unit.test_classifier import oracle_wave


@pytest.mark.parametrize("rate", [8000, 44100, 48000, 192000])
def test_spectrum_retains_fundamental_harmonics_and_owns_readonly_arrays(rate):
    t = np.arange(round(rate * .5)) / rate
    source = np.sin(2*np.pi*523*t) + .7*np.sin(2*np.pi*1046*t)
    result = spectral_template(source, rate)
    for frequency in (523, 1046):
        i = np.argmin(abs(result.frequencies - frequency))
        assert result.log_magnitude[i] > .2
    i = np.argmin(abs(result.frequencies - 1800))
    assert result.log_magnitude[i] < .01
    assert result.temporal_log_magnitude.shape == (len(result.frequencies), 8)
    assert result.nbytes < 200000
    for values in (result.frequencies, result.log_magnitude, result.temporal_log_magnitude):
        assert not values.flags.writeable and np.isfinite(values).all()
    source[:] = 0
    assert result.log_magnitude.max() > .5


def test_gain_dc_polarity_and_phase_do_not_change_spectral_pitch():
    a = oracle_wave(2, seconds=.6)
    b = oracle_wave(2, seconds=.6, phase=2, amplitude=.03, dc=.8)
    x = spectral_template(a.samples[:,0], 48000)
    y = spectral_template(-b.samples[:,0], 48000)
    wrong = spectral_template(oracle_wave(3, seconds=.6).samples[:,0], 48000)
    assert spectral_similarity(x, y) > .98
    assert spectral_similarity(x, y) > spectral_similarity(x, wrong) + .4
    assert spectral_similarity(x, x) == pytest.approx(1, abs=1e-12)


def test_unequal_decaying_portions_match_closest_template_slices():
    rate = 48000
    t = np.arange(rate) / rate
    x = np.sin(2*np.pi*700*t)*np.exp(-3*t) + .6*np.sin(2*np.pi*1400*t)*np.exp(-t)
    full = spectral_template(x, rate)
    tail = spectral_template(x[rate//2:], rate)
    unrelated = spectral_template(np.sin(2*np.pi*900*t), rate)
    assert spectral_similarity(tail, full) > .85
    assert spectral_similarity(tail, full) > spectral_similarity(tail, unrelated) + .5


@pytest.mark.parametrize("source", [np.empty(0), np.zeros(960), np.ones(960)])
def test_silence_empty_and_dc_never_have_spectral_matches(source):
    silent = spectral_template(source, 48000)
    active = spectral_template(oracle_wave(0).samples[:,0], 48000)
    assert spectral_similarity(silent, active) == 0
    assert spectral_similarity(silent, silent) == 0


def test_minimum_event_can_be_zero_padded_without_invalid_stft():
    source = oracle_wave(0, seconds=.02).samples[:,0]
    descriptor = spectral_template(source, 48000)
    assert spectral_similarity(descriptor, descriptor) == pytest.approx(1)


@pytest.mark.parametrize("source", [np.ones((100,2)), np.array([np.nan]), np.array([np.inf]), np.array([1j]), np.array(["x"])])
def test_invalid_audio_is_rejected(source):
    with pytest.raises(AudioDataError):
        spectral_template(source, 48000)


@pytest.mark.parametrize("rate", [0, True, 384000])
def test_invalid_rate_is_rejected(rate):
    with pytest.raises(AudioDataError):
        spectral_template(np.zeros(100), rate)


def test_stft_blocks_are_bounded_equivalent_and_cancellable(monkeypatch):
    import dsp.spectrum as module
    source = oracle_wave(1, seconds=.7).samples[:,0]
    normal = spectral_template(source, 48000)
    monkeypatch.setattr(module, "STFT_BLOCK", 3)
    small = spectral_template(source, 48000)
    np.testing.assert_allclose(small.log_magnitude, normal.log_magnitude, atol=1e-7)
    np.testing.assert_allclose(small.temporal_log_magnitude, normal.temporal_log_magnitude, atol=1e-7)
    original = module.ShortTimeFFT.stft
    calls = []
    cancel = Event()
    def stft(self, x, **kwargs):
        calls.append(kwargs["p1"] - kwargs["p0"])
        value = original(self, x, **kwargs)
        cancel.set()
        return value
    monkeypatch.setattr(module.ShortTimeFFT, "stft", stft)
    with pytest.raises(OperationCancelled):
        spectral_template(source, 48000, cancel)
    assert calls == [3]
    with pytest.raises(AudioDataError):
        spectral_template(np.zeros(80001), 8000)


def test_corrupt_descriptor_and_mismatched_frequency_grid_are_rejected():
    descriptor = spectral_template(oracle_wave(0).samples[:,0], 48000)
    for invalid in (np.full_like(descriptor.log_magnitude, np.nan),
                    descriptor.log_magnitude.astype(np.complex128) + 1j,
                    -np.ones_like(descriptor.log_magnitude)):
        with pytest.raises(AudioDataError):
            replace(descriptor, log_magnitude=invalid)
    shifted = replace(descriptor, frequencies=descriptor.frequencies + .01)
    with pytest.raises(AudioDataError):
        spectral_similarity(descriptor, shifted)
    other_rate = spectral_template(oracle_wave(0, rate=44100).samples[:,0], 44100)
    with pytest.raises(AudioDataError):
        spectral_similarity(descriptor, other_rate)
