"""Worker-side metering; no Oracle detection or amplitude normalization."""

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class AudioLevel:
    rms: float
    peak: float
    rms_dbfs: float
    peak_dbfs: float
    clipping: bool
    buffered_seconds: float
    received_frames: int
    dropped_frames: int
    overflow_count: int


def measure_level(
    samples: NDArray[np.float32], buffered_seconds: float = 0,
    received_frames: int = 0, dropped_frames: int = 0, overflow_count: int = 0,
) -> AudioLevel:
    if not np.isfinite(samples).all():
        raise ValueError("Non-finite samples in the audio stream")
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0
    db = lambda value: max(-80.0, 20 * math.log10(value)) if value > 0 else -80.0
    return AudioLevel(
        rms, peak, db(rms), db(peak), peak >= 1.0, buffered_seconds,
        received_frames, dropped_frames, overflow_count,
    )
