"""Optional symmetric finite-window filtering for event and template comparison."""
from dataclasses import dataclass
import math

import numpy as np
from scipy.signal import butter, sosfiltfilt
from audio.data import AudioDataError


@dataclass(frozen=True, slots=True)
class BandpassSettings:
    low_hz: float = 350.0
    high_hz: float = 3000.0

    def validate(self, sample_rate: int) -> None:
        if any(type(f) not in (int, float) or not math.isfinite(f) for f in (self.low_hz, self.high_hz)):
            raise AudioDataError("帯域設定が不正です。")
        if not 0 < self.low_hz < self.high_hz < sample_rate / 2:
            raise AudioDataError("帯域設定は内部レートの Nyquist 周波数未満にしてください。")


def bandpass_audio(samples, sample_rate: int, settings: BandpassSettings) -> np.ndarray:
    settings.validate(sample_rate)
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise AudioDataError("帯域処理の音声が不正です。")
    if len(values) < 2:
        return values.astype(np.float32)
    sos = butter(4, (settings.low_hz, settings.high_hz), btype="bandpass",
                 fs=sample_rate, output="sos")
    # Bounded padding also supports short imported clips.
    padlen = min(3 * (2 * len(sos) + 1), len(values) - 1)
    filtered = sosfiltfilt(sos, values, padlen=padlen)
    if not np.isfinite(filtered).all():
        raise AudioDataError("帯域処理した音声が不正です。")
    return filtered.astype(np.float32)
