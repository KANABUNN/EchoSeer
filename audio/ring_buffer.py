"""Bounded, thread-safe native-format audio storage, measured in frames."""

from dataclasses import dataclass
import math
import time
from uuid import uuid4
from pathlib import Path
from threading import Event, Lock

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class BufferRead:
    samples: NDArray[np.float32]
    stream_id: str
    timestamp: float
    start_frame: int
    end_frame: int
    latest_frame: int
    overrun: bool


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
        self._total_frames = 0
        self._stream_id = uuid4().hex
        self._last_write_time = 0.0
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
            self._total_frames = 0
            self._stream_id = uuid4().hex
            self._last_write_time = 0.0

    def write(self, samples: NDArray[np.float32]) -> None:
        values = np.asarray(samples, dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != self.channels:
            raise ValueError("Audio frames do not match the ring-buffer channel count")
        count = len(values)
        if count == 0:
            return
        with self._lock:
            self._total_frames += count
            self._last_write_time = time.monotonic()
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
        return self.snapshot_event(frames)[0]

    def snapshot_event(self, frames: int | None = None) -> tuple[NDArray[np.float32], str, float, int, int]:
        """Copy frames and their stream identity/time atomically under the same lock."""
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
            return output, self._stream_id, self._last_write_time, self._total_frames - count, self._total_frames


    def read_since(self, frame: int, stream_id: str | None = None,
                   max_frames: int | None = None) -> BufferRead:
        """Copy earliest unread frames atomically; report wrap/reset instead of joining gaps."""
        if type(frame) is not int or frame < 0:
            raise ValueError("Requested frame must be nonnegative")
        if max_frames is not None and (type(max_frames) is not int or max_frames <= 0):
            raise ValueError("Read limit must be positive")
        with self._lock:
            oldest = self._total_frames - self._size
            changed = stream_id is not None and stream_id != self._stream_id
            overrun = changed or frame < oldest or frame > self._total_frames
            start_frame = oldest if overrun else frame
            count = self._total_frames - start_frame
            if max_frames is not None:
                count = min(count, max_frames)
            position = (self._position - (self._total_frames - start_frame)) % self.capacity_frames
            output = np.empty((count, self.channels), dtype=np.float32)
            first = min(count, self.capacity_frames - position)
            output[:first] = self._data[position:position + first]
            if first < count:
                output[first:] = self._data[:count - first]
            return BufferRead(output, self._stream_id, self._last_write_time, start_frame,
                              start_frame + count, self._total_frames, overrun)

    def dump(
        self, path: Path | str, frames: int | None = None, encoding: str = "float32",
        cancel: Event | None = None,
    ) -> Path:
        """Copy under the ring lock, then write outside the capture critical section."""
        from audio.data import AudioClip
        from audio.operations import check_cancel
        from audio.waveio import write_wav
        check_cancel(cancel)
        clip = AudioClip(self.snapshot(frames), self.sample_rate)
        return write_wav(path, clip, encoding, cancel)
