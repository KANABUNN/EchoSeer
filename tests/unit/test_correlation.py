"""Correlation normalization, overlap guards, deterministic lags and cancellation."""
from threading import Event
import numpy as np
import pytest
from audio.data import AudioDataError
from audio.operations import OperationCancelled
from dsp.correlation import normalized_correlation


def test_delay_gain_dc_and_polarity_do_not_change_matching():
    rng = np.random.default_rng(12)
    pattern = rng.normal(size=200)
    event = np.r_[rng.normal(size=53), -7 * pattern + 9, rng.normal(size=70)]
    match = normalized_correlation(event, pattern)
    assert match.score == pytest.approx(1, abs=1e-12)
    assert match.lag_frames == 53 and match.overlap_frames == 200


def test_a_shorter_query_matches_inside_a_longer_template():
    values = np.random.default_rng(31).normal(size=400)
    match = normalized_correlation(values[83:283], values)
    assert match.score == pytest.approx(1, abs=1e-12)
    assert match.lag_frames == -83 and match.overlap_frames == 200


def test_fft_score_agrees_with_direct_pearson_reference():
    rng = np.random.default_rng(5)
    x, y = rng.normal(size=79), rng.normal(size=63)
    expected = []
    for lag in range(-(len(y) - 1), len(x)):
        x0, x1 = max(lag, 0), min(len(x), lag + len(y))
        count = x1 - x0
        if count < max(32, int(np.ceil(min(len(x), len(y)) * .8))):
            continue
        a, b = x[x0:x1], y[max(-lag, 0):max(-lag, 0) + count]
        if np.sum((b-b.mean())**2) < .5*np.sum((y-y.mean())**2):
            continue
        score = abs(float(np.corrcoef(a,b)[0,1]))
        expected.append((score,lag,count))
    score, lag, count = max(expected)
    actual = normalized_correlation(x,y)
    assert actual.score == pytest.approx(score, abs=1e-12)
    assert (actual.lag_frames, actual.overlap_frames) == (lag,count)


@pytest.mark.parametrize("values", [np.zeros(300), np.ones(300), np.empty(0), np.arange(16)])
def test_silence_constant_empty_and_tiny_overlaps_are_not_matches(values):
    assert normalized_correlation(values,np.arange(300)).score == 0


def test_one_matching_edge_sample_cannot_score_as_a_full_match():
    rng=np.random.default_rng(99)
    x,y=rng.normal(size=400),rng.normal(size=400)
    x[-1]=y[0]=100
    match=normalized_correlation(x,y)
    assert match.score < .5
    assert match.overlap_frames == 0 or match.overlap_frames >= 320


@pytest.mark.parametrize("values", [np.array([np.nan]), np.array([np.inf]), np.ones((100,2)), np.array(["x"]), np.array([1j])])
def test_invalid_waveform_is_rejected(values):
    with pytest.raises(AudioDataError):
        normalized_correlation(values,np.ones(100))


@pytest.mark.parametrize("fraction", [0,1.1,float("nan"),True])
def test_invalid_overlap_fraction_is_rejected(fraction):
    with pytest.raises(ValueError):
        normalized_correlation(np.ones(100),np.ones(100),min_overlap=fraction)


def test_oversized_input_and_cancellation_are_rejected(monkeypatch):
    import dsp.correlation as module
    monkeypatch.setattr(module,"MAX_CORRELATION_FRAMES",100)
    with pytest.raises(AudioDataError):
        normalized_correlation(np.ones(101),np.ones(100))
    cancel=Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        normalized_correlation(np.ones(100),np.ones(100),cancel)
