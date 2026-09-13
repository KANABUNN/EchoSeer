"""Bounded, thread-safe native-format audio storage, measured in frames."""

import math
from threading import Lock

import numpy as np
from numpy.typing import NDArray


class RingBuffer:
    def __init__(self, sample_rate: int, channels: int, duration_seconds: float = 10.0) -> None:
        if sample_rate <= 0 or channels <= 0 or not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("Audio format and buffer duration must be positive")
        self.sample_rate = sample_rate
        self.channels = channels
        self.capacity_frames = max(1, math.ceil(sample_rate * duration_seconds))
        self._data = np.empty((self.capacity_frames, channels), dtype=np.float32)
        self._position = 0
        self._size = 0
        self._lock = Lock()

    @property
    def size_frames(self) -> int:
        with self._lock:
            return self._size

    @property
    def duration_seconds(self) -> float:
        return self.size_frames / self.sample_rate

    def clear(self) -> None:
        with self._lock:
            self._position = 0
            self._size = 0

    def write(self, samples: NDArray[np.float32]) -> None:
        values = np.asarray(samples, dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != self.channels:
            raise ValueError("Audio frames do not match the ring-buffer channel count")
        count = len(values)
        if count == 0:
            return
        with self._lock:
            if count >= self.capacity_frames:
                self._data[:] = values[-self.capacity_frames:]
                self._position = 0
                self._size = self.capacity_frames
                return
            first = min(count, self.capacity_frames - self._position)
            self._data[self._position:self._position + first] = values[:first]
            if first < count:
                self._data[:count - first] = values[first:]
            self._position = (self._position + count) % self.capacity_frames
            self._size = min(self.capacity_frames, self._size + count)

    def snapshot(self, frames: int | None = None) -> NDArray[np.float32]:
        if frames is not None and frames < 0:
            raise ValueError("Requested frames must not be negative")
        with self._lock:
            count = self._size if frames is None else min(frames, self._size)
            start = (self._position - count) % self.capacity_frames
            output = np.empty((count, self.channels), dtype=np.float32)
            first = min(count, self.capacity_frames - start)
            output[:first] = self._data[start:start + first]
            if first < count:
                output[first:] = self._data[:count - first]
            return output
