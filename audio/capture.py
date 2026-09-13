"""A single callback stream: enqueue only, with a bounded low-latency queue."""

from dataclasses import dataclass
import logging
from queue import Empty, Full, Queue
from threading import Lock
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray

from audio.backend import AudioBackend, AudioDevice, AudioError

logger = logging.getLogger("oracle_assistant.audio")
CALLBACK_SECONDS = 0.02
QUEUE_BLOCKS = 50


@dataclass(frozen=True, slots=True)
class AudioBlock:
    samples: NDArray[np.float32]
    timestamp: float
    status_flags: int


@dataclass(frozen=True, slots=True)
class CaptureStats:
    received_frames: int
    dropped_frames: int
    overflow_count: int
    error: str | None


class CaptureSession:
    def __init__(
        self, interface: Any, module: Any, backend: AudioBackend,
        device: AudioDevice, queue_blocks: int = QUEUE_BLOCKS,
    ) -> None:
        if queue_blocks <= 0:
            raise ValueError("Audio queue must have a positive bound")
        self.device = device
        self._module = module
        self._queue: Queue[AudioBlock] = Queue(maxsize=queue_blocks)
        self._stats_lock = Lock()
        self._received = 0
        self._dropped = 0
        self._overflows = 0
        self._error: str | None = None
        self._closed = False
        self._stream = backend.open_stream(
            interface, module, device, self._callback,
            max(1, round(device.sample_rate * CALLBACK_SECONDS)),
        )

    def _callback(
        self, in_data: bytes | None, frame_count: int,
        time_info: dict[str, float], status_flags: int,
    ) -> tuple[None, int]:
        if self._closed:
            return None, self._module.paComplete
        try:
            if frame_count == 0:
                return None, self._module.paContinue
            if in_data is None:
                raise ValueError("No input data")
            values = np.frombuffer(in_data, dtype=np.float32)
            if values.size != frame_count * self.device.channels:
                raise ValueError("Unexpected input frame count")
            # The immutable bytes owner is retained by this zero-copy NumPy view.
            block = AudioBlock(
                values.reshape(frame_count, self.device.channels),
                time.monotonic() - frame_count / self.device.sample_rate, status_flags,
            )
            with self._stats_lock:
                self._received += frame_count
                if status_flags & self._module.paInputOverflow:
                    self._overflows += 1
            try:
                self._queue.put_nowait(block)
            except Full:
                try:
                    discarded = self._queue.get_nowait()
                except Empty:
                    discarded = None
                if discarded is not None:
                    with self._stats_lock:
                        self._dropped += len(discarded.samples)
                try:
                    self._queue.put_nowait(block)
                except Full:
                    with self._stats_lock:
                        self._dropped += frame_count
            return None, self._module.paContinue
        except (ValueError, TypeError, BufferError) as error:
            with self._stats_lock:
                self._error = str(error)
            return None, self._module.paAbort

    def start(self) -> None:
        try:
            self._stream.start_stream()
        except (OSError, RuntimeError):
            self.close()
            raise

    def pop(self, timeout: float = 0.02) -> AudioBlock | None:
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    def stats(self) -> CaptureStats:
        with self._stats_lock:
            return CaptureStats(self._received, self._dropped, self._overflows, self._error)

    def is_active(self) -> bool:
        return bool(self._stream.is_active())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._stream.is_active():
                self._stream.stop_stream()
        except (OSError, RuntimeError) as error:
            logger.warning("Could not stop input stream: %s", error)
        finally:
            try:
                self._stream.close()
            except (OSError, RuntimeError) as error:
                logger.warning("Could not close input stream: %s", error)
