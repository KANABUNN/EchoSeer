"""Bounded STFT log-magnitude templates and phase-independent cosine comparison."""
from dataclasses import dataclass
import math
from threading import Event

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import ShortTimeFFT
from scipy.signal.windows import hann

from audio.data import AudioDataError
from audio.operations import check_cancel

TIME_BINS = 8
STFT_BLOCK = 32
MAX_SECONDS = 10


@dataclass(frozen=True, slots=True)
class SpectralTemplate:
    sample_rate: int
    fft_size: int
    hop_frames: int
    frequencies: np.ndarray
    log_magnitude: np.ndarray
    temporal_log_magnitude: np.ndarray

    def __post_init__(self) -> None:
        if type(self.sample_rate) is not int or not 8000 <= self.sample_rate <= 192000:
            raise AudioDataError("スペクトルのレートが不正です。")
        if type(self.fft_size) is not int or self.fft_size < 2 or type(self.hop_frames) is not int or self.hop_frames < 1:
            raise AudioDataError("スペクトルの形式が不正です。")
        arrays = tuple(_owned(v) for v in (self.frequencies, self.log_magnitude, self.temporal_log_magnitude))
        f, profile, temporal = arrays
        if f.ndim != 1 or len(f) == 0 or profile.shape != f.shape or temporal.shape != (len(f), TIME_BINS):
            raise AudioDataError("スペクトルの形状が不正です。")
        if not all(np.isfinite(v).all() for v in arrays) or any(np.any(v < 0) for v in arrays):
            raise AudioDataError("スペクトルの値が不正です。")
        if np.any(np.diff(f) <= 0) or f[-1] > self.sample_rate / 2:
            raise AudioDataError("スペクトルの周波数軸が不正です。")
        for name, value in zip(("frequencies", "log_magnitude", "temporal_log_magnitude"), arrays):
            object.__setattr__(self, name, value)

    @property
    def nbytes(self) -> int:
        return self.frequencies.nbytes + self.log_magnitude.nbytes + self.temporal_log_magnitude.nbytes


def _owned(values) -> np.ndarray:
    source = np.asarray(values)
    if source.dtype.kind not in "fiu" or not np.isfinite(source).all():
        raise AudioDataError("スペクトル特徴は有限の実数にしてください。")
    with np.errstate(over="ignore", invalid="ignore"):
        array = np.array(source, dtype=np.float32, copy=True)
    array.setflags(write=False)
    return array


def _log_features(power: np.ndarray) -> np.ndarray:
    # Remove a local broadband floor; narrow fundamentals and harmonics survive.
    floor = median_filter(power, size=(31,) + (1,) * (power.ndim - 1), mode="nearest")
    magnitude = np.sqrt(np.maximum(power - 1.5 * floor, 0))
    peak = float(magnitude.max(initial=0))
    return np.log1p(magnitude / peak) if peak > 1e-14 else np.zeros_like(magnitude)


def spectral_template(samples, sample_rate: int, cancel: Event | None = None) -> SpectralTemplate:
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise AudioDataError("スペクトルの内部レートが不正です。")
    values = np.asarray(samples)
    if values.ndim != 1 or values.dtype.kind not in "fiu" or not np.isfinite(values).all():
        raise AudioDataError("スペクトルの入力は有限のモノラル波形にしてください。")
    if len(values) > MAX_SECONDS * sample_rate:
        raise AudioDataError("スペクトルの音声区間は10秒以内にしてください。")
    check_cancel(cancel)
    x = values.astype(np.float64)
    peak = float(np.max(np.abs(x), initial=0))
    if peak:
        x /= peak
    if x.size:
        x -= x.mean()
    window_size = round(sample_rate * 0.128)
    hop = max(1, round(sample_rate * 0.010))
    fft_size = 1 << math.ceil(math.log2(window_size * 2))
    transform = ShortTimeFFT(hann(window_size, sym=False), hop, sample_rate,
                            mfft=fft_size, scale_to="magnitude")
    mask = (transform.f >= 100) & (transform.f <= min(8000, sample_rate / 2))
    frequencies = transform.f[mask]
    profile = np.zeros(len(frequencies), dtype=np.float64)
    temporal = np.zeros((len(frequencies), TIME_BINS), dtype=np.float64)
    counts = np.zeros(TIME_BINS, dtype=np.int64)
    if np.max(np.abs(x), initial=0) > 1e-14:
        # Short events use zero padding; every block has a bounded complex workspace.
        x = np.pad(x, (0, max(0, window_size - len(x))))
        frame_count = (len(x) - 1) // hop + 1
        for start in range(0, frame_count, STFT_BLOCK):
            check_cancel(cancel)
            end = min(start + STFT_BLOCK, frame_count)
            stft = transform.stft(x, p0=start, p1=end, padding="zeros")
            power = np.abs(stft[mask]) ** 2
            profile += power.sum(axis=1)
            bins = np.minimum(TIME_BINS - 1, np.arange(start, end) * TIME_BINS // frame_count)
            for index in np.unique(bins):
                temporal[:, index] += power[:, bins == index].sum(axis=1)
                counts[index] += np.count_nonzero(bins == index)
        profile /= frame_count
        temporal /= np.maximum(counts, 1)[None, :]
    check_cancel(cancel)
    return SpectralTemplate(sample_rate, fft_size, hop, frequencies,
                            _log_features(profile), _log_features(temporal))


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    x, y = a.astype(np.float64).ravel(), b.astype(np.float64).ravel()
    denominator = np.linalg.norm(x) * np.linalg.norm(y)
    if denominator <= 1e-14:
        return 0.0
    return float(np.clip(np.dot(x, y) / denominator, 0, 1))


def spectral_similarity(event: SpectralTemplate, template: SpectralTemplate) -> float:
    if (event.sample_rate, event.fft_size, event.hop_frames) != (template.sample_rate, template.fft_size, template.hop_frames):
        raise AudioDataError("スペクトルの形式が一致しません。共通前処理を使ってください。")
    if not np.array_equal(event.frequencies, template.frequencies) or event.log_magnitude.shape != template.log_magnitude.shape or event.temporal_log_magnitude.shape != template.temporal_log_magnitude.shape:
        raise AudioDataError("スペクトルの形状が一致しません。")
    # Decay changes harmonic balance; compare each active event slice with the
    # closest template slice, independent of event onset and unequal duration.
    x = event.temporal_log_magnitude.astype(np.float64)
    y = template.temporal_log_magnitude.astype(np.float64)
    nx, ny = np.linalg.norm(x, axis=0), np.linalg.norm(y, axis=0)
    similarities = np.divide(x.T @ y, nx[:, None] * ny[None, :],
                             out=np.zeros((TIME_BINS, TIME_BINS)), where=(nx[:, None] * ny[None, :]) > 1e-14)
    temporal = float(np.mean(np.max(similarities, axis=1)[nx > 1e-14])) if np.any(nx > 1e-14) else 0.0
    return float(np.clip(0.25 * _cosine(event.log_magnitude, template.log_magnitude) + 0.75 * temporal, 0, 1))
