"""Owned, read-only audio frames; independent of Qt."""

from dataclasses import dataclass
import numpy as np
from numpy.typing import NDArray


class AudioDataError(ValueError):
    """Audio cannot be represented as finite float32 frames."""


@dataclass(frozen=True, slots=True)
class AudioClip:
    samples: NDArray[np.float32]
    sample_rate: int

    def __post_init__(self) -> None:
        if type(self.sample_rate) is not int or not 8000 <= self.sample_rate <= 384000:
            raise AudioDataError("音声のサンプルレートには対応していません。")
        source = np.asarray(self.samples)
        if source.ndim == 1:
            source = source[:, None]
        if source.ndim != 2 or not 1 <= source.shape[1] <= 64:
            raise AudioDataError("音声のチャンネル数には対応していません。")
        if source.dtype.kind not in "fiu" or not np.isfinite(source).all():
            raise AudioDataError("音声に不正な数値が含まれています。")
        if source.dtype.kind == "f" and source.dtype.itemsize > 4 and source.size:
            if np.max(np.abs(source)) > np.finfo(np.float32).max:
                raise AudioDataError("音声の数値が float32 の範囲を超えています。")
        owned = np.array(source, dtype=np.float32, copy=True, order="C")
        owned.setflags(write=False)
        object.__setattr__(self, "samples", owned)

    @property
    def channels(self) -> int:
        return self.samples.shape[1]

    @property
    def frame_count(self) -> int:
        return len(self.samples)

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate
