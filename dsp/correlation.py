"""Lag-search normalized cross correlation, with substantial overlap only."""
from dataclasses import dataclass
import math
from threading import Event

import numpy as np
from scipy.signal import correlate

from audio.data import AudioDataError
from audio.operations import check_cancel

MAX_CORRELATION_FRAMES = 1920000
LAG_BLOCK = 65536


@dataclass(frozen=True, slots=True)
class CorrelationMatch:
    score: float = 0.0
    lag_frames: int = 0
    overlap_frames: int = 0


def _centered(values) -> np.ndarray:
    source = np.asarray(values)
    if source.ndim != 1 or source.dtype.kind not in "fiu" or not np.isfinite(source).all():
        raise AudioDataError("相関の入力は有限のモノラル波形にしてください。")
    if len(source) > MAX_CORRELATION_FRAMES:
        raise AudioDataError("相関の音声区間が長すぎます。10 秒以内に分割してください。")
    x = source.astype(np.float64)
    if x.size:
        peak = np.max(np.abs(x))
        if peak:
            x /= peak
        x -= x.mean()
        peak = np.max(np.abs(x))
        if peak:
            x /= peak
    return x


def normalized_correlation(event, template, cancel: Event | None = None,
                           min_overlap: float = 0.8) -> CorrelationMatch:
    """Peak absolute Pearson correlation across lags, bounded to [0, 1].

    Require 80% of the shorter window and half its centered energy.
    This excludes tiny/quiet edge overlaps that can otherwise score 1.
    Positive lag places the template later in the event window.
    """
    if type(min_overlap) not in (int, float) or not math.isfinite(min_overlap) or not 0 < min_overlap <= 1:
        raise ValueError("Invalid overlap fraction")
    check_cancel(cancel)
    x, y = _centered(event), _centered(template)
    n, m = len(x), len(y)
    if min(n, m) < 32:
        return CorrelationMatch()
    total_x, total_y = float(x @ x), float(y @ y)
    if total_x == 0 or total_y == 0:
        return CorrelationMatch()
    required = max(32, math.ceil(min(n, m) * min_overlap))
    sx, sy = np.r_[0.0, np.cumsum(x)], np.r_[0.0, np.cumsum(y)]
    ex, ey = np.r_[0.0, np.cumsum(x * x)], np.r_[0.0, np.cumsum(y * y)]
    check_cancel(cancel)
    dots = correlate(x, y, mode="full", method="fft")
    check_cancel(cancel)
    best = CorrelationMatch()
    for start in range(required - m, n - required + 1, LAG_BLOCK):
        check_cancel(cancel)
        lags = np.arange(start, min(start + LAG_BLOCK, n - required + 1))
        x0, x1 = np.maximum(lags, 0), np.minimum(n, lags + m)
        count = x1 - x0
        y0 = np.maximum(-lags, 0)
        y1 = y0 + count
        sums_x, sums_y = sx[x1] - sx[x0], sy[y1] - sy[y0]
        energy_x = np.maximum(0, ex[x1] - ex[x0] - sums_x * sums_x / count)
        energy_y = np.maximum(0, ey[y1] - ey[y0] - sums_y * sums_y / count)
        covariance = dots[lags + m - 1] - sums_x * sums_y / count
        denominator = np.sqrt(energy_x * energy_y)
        shorter_energy = energy_x if n <= m else energy_y
        total_shorter = total_x if n <= m else total_y
        valid = (denominator > 1e-14) & (shorter_energy >= total_shorter * 0.5)
        scores = np.zeros(len(lags), dtype=np.float64)
        np.divide(np.abs(covariance), denominator, out=scores, where=valid)
        np.clip(scores, 0, 1, out=scores)
        index = int(np.argmax(scores))
        if scores[index] > best.score:
            best = CorrelationMatch(float(scores[index]), int(lags[index]), int(count[index]))
    return best
