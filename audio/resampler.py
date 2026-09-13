"""Whole-window polyphase conversion avoids callback-boundary artifacts."""

from math import gcd
import numpy as np
from numpy.typing import NDArray
from scipy.signal import resample_poly

from audio.data import AudioDataError


def resample_audio(
    samples: NDArray[np.float32], source_rate: int, target_rate: int = 48000,
) -> NDArray[np.float32]:
    for rate in (source_rate, target_rate):
        if type(rate) is not int or not 8000 <= rate <= 384000:
            raise AudioDataError("音声のサンプルレートには対応していません。")
    values = np.asarray(samples, dtype=np.float32)
    if values.ndim not in (1, 2) or not np.isfinite(values).all():
        raise AudioDataError("レート変換する音声が不正です。")
    if len(values) == 0 or source_rate == target_rate:
        return values.copy()
    divisor = gcd(source_rate, target_rate)
    converted = resample_poly(
        values, target_rate // divisor, source_rate // divisor,
        axis=0, window=("kaiser", 5.0), padtype="constant",
    )
    if not np.isfinite(converted).all():
        raise AudioDataError("レート変換した音声が不正です。")
    return np.ascontiguousarray(converted, dtype=np.float32)
