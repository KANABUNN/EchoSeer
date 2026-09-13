"""The same finite-window preprocessing for Live snapshots and WAV files."""

from threading import Event
import numpy as np
from numpy.typing import NDArray

from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel
from audio.resampler import resample_audio
from dsp.normalization import normalize_audio


def to_mono(samples: NDArray[np.float32]) -> NDArray[np.float32]:
    values = np.asarray(samples, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] < 1 or not np.isfinite(values).all():
        raise AudioDataError("モノラル化する音声が不正です。")
    return values.mean(axis=1, dtype=np.float64).astype(np.float32)


def remove_dc(samples: NDArray[np.float32]) -> NDArray[np.float32]:
    values = np.asarray(samples, dtype=np.float32)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise AudioDataError("DC 成分を除去する音声が不正です。")
    if len(values) == 0:
        return values.copy()
    return (values.astype(np.float64) - values.mean(dtype=np.float64)).astype(np.float32)


def preprocess_clip(
    clip: AudioClip, target_rate: int = 48000, cancel: Event | None = None,
) -> AudioClip:
    check_cancel(cancel)
    mono = to_mono(clip.samples)
    check_cancel(cancel)
    centered = remove_dc(mono)
    check_cancel(cancel)
    converted = resample_audio(centered, clip.sample_rate, target_rate)
    check_cancel(cancel)
    normalized = normalize_audio(converted)
    check_cancel(cancel)
    return AudioClip(normalized, target_rate)
