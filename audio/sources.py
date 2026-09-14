"""Replayable finite audio windows; no Qt or device lifecycle dependency."""

from abc import ABC, abstractmethod
from pathlib import Path
from threading import Event
from collections.abc import Iterator
from numpy.typing import NDArray
import numpy as np

from audio.data import AudioClip
from audio.event import EventContext
from audio.operations import check_cancel
from audio.ring_buffer import RingBuffer
from audio.waveio import read_wav


class AudioSource(ABC):
    @abstractmethod
    def read(self, cancel: Event | None = None) -> AudioClip:
        """Read one complete native-format window without preprocessing."""

    def blocks(
        self, block_frames: int = 4096, cancel: Event | None = None,
    ) -> Iterator[NDArray[np.float32]]:
        if type(block_frames) is not int or block_frames <= 0:
            raise ValueError("Block size must be a positive integer")
        clip = self.read(cancel)
        for start in range(0, clip.frame_count, block_frames):
            check_cancel(cancel)
            yield clip.samples[start:start + block_frames]


class LiveSource(AudioSource):
    """Read the current ring once; capture continues while its copy is analyzed."""

    def __init__(self, ring: RingBuffer, frames: int | None = None) -> None:
        self.ring, self.frames = ring, frames
        self.event_context: EventContext | None = None

    def read(self, cancel: Event | None = None) -> AudioClip:
        check_cancel(cancel)
        values, stream_id, timestamp, start, end = self.ring.snapshot_event(self.frames)
        self.event_context = EventContext("live", timestamp, stream_id, start, end)
        check_cancel(cancel)
        return AudioClip(values, self.ring.sample_rate)


class WaveFileSource(AudioSource):
    """Each read reopens the file, giving identical frames for unchanged bytes."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def read(self, cancel: Event | None = None) -> AudioClip:
        return read_wav(self.path, cancel)


class ClipSource(AudioSource):
    """Replay the same owned snapshot, independent of later capture writes."""

    def __init__(self, clip: AudioClip) -> None:
        self.clip = clip

    def read(self, cancel: Event | None = None) -> AudioClip:
        check_cancel(cancel)
        return self.clip
