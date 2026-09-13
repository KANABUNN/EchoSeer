"""Deterministic peak normalization for a complete analysis window."""

import numpy as np
from numpy.typing import NDArray
from audio.data import AudioDataError

NORMALIZED_PEAK = 0.95
SILENCE_FLOOR = 1e-6


def normalize_audio(
    samples: NDArray[np.float32], target_peak: float = NORMALIZED_PEAK,
    silence_floor: float = SILENCE_FLOOR,
) -> NDArray[np.float32]:
    if not 0 < target_peak <= 1 or not 0 <= silence_floor < target_peak:
        raise ValueError("Invalid normalization settings")
    values = np.asarray(samples, dtype=np.float32)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise AudioDataError("正規化する音声が不正です。")
    if values.size == 0:
        return values.copy()
    peak = float(np.max(np.abs(values)))
    if peak <= silence_floor:
        return np.zeros_like(values)
    return (values.astype(np.float64) * (target_peak / peak)).astype(np.float32)
