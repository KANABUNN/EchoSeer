"""Incremental native RMS windows; retain at most one bounded cue and pre-roll."""
from collections import deque
from dataclasses import dataclass
from threading import Event
from collections.abc import Iterator

import numpy as np

from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel
from detector.events import EventWindow, MAX_EVENT_BYTES, RmsEventDetector, _AdaptiveRmsGate


@dataclass(frozen=True, slots=True)
class StreamStep:
    end_frame: int
    silent: bool
    active: bool
    onset_frame: int | None = None
    window: EventWindow | None = None


class StreamingRmsDetector:
    def __init__(self, sample_rate: int, channels: int, threshold: float = .025,
                 start_frame: int = 0) -> None:
        self.settings = RmsEventDetector(threshold)
        # Validate native format without allocating an audio history.
        AudioClip(np.empty((0, channels), dtype=np.float32), sample_rate)
        if type(start_frame) is not int or start_frame < 0:
            raise ValueError("Invalid stream frame")
        self.rate, self.channels = sample_rate, channels
        self.block = max(1, round(sample_rate * self.settings.block_seconds))
        self.pre_frames = round(sample_rate * self.settings.pre_roll)
        self.gate = _AdaptiveRmsGate(self.settings, sample_rate)
        self.release = round(sample_rate * self.settings.release_seconds)
        self.quiet_level = self.gate.fixed_quiet_level
        self.limit = min(round(sample_rate * self.settings.max_window_seconds),
                         MAX_EVENT_BYTES // (4 * channels))
        self._first = self._position = start_frame
        self._tail = np.empty((0, channels), dtype=np.float32)
        self._pre = deque(maxlen=max(1, (self.pre_frames + self.block - 1) // self.block))
        self._parts = []
        self._onset = self._start = self._suppress_quiet = None
        self._stored = 0
        self._suppress = False
        self._suppress_values = []

    @property
    def retained_frames(self) -> int:
        return len(self._tail) + sum(len(part) for part in self._pre) + self._stored

    def _window(self, end: int, signal_end: int, reason: str = "") -> EventWindow:
        if not reason and self._onset == self._first:
            reason = "CLIPPED_EVENT"
        if not reason and signal_end - self._onset < round(self.rate * self.settings.min_signal_seconds):
            reason = "SHORT_EVENT"
        samples = np.concatenate(self._parts)[:self.limit]
        window = EventWindow(AudioClip(samples, self.rate), self._start,
                             self._start + len(samples), self._onset, signal_end, reason)
        self._parts, self._stored = [], 0
        self._onset = self._start = None
        self._pre.clear()
        for offset in range(max(0, len(samples) - self.pre_frames), len(samples), self.block):
            self._pre.append(samples[offset:offset + self.block].copy())
        return window

    def _begin(self, start: int, block: np.ndarray) -> int:
        prefix = (
            np.concatenate(tuple(self._pre))[-self.pre_frames:]
            if self._pre and self.pre_frames
            else np.empty((0, self.channels), dtype=np.float32)
        )
        self._onset = start
        self._start = start - len(prefix)
        self._parts = [prefix.copy(), block.copy()]
        self._stored = len(prefix) + len(block)
        self._pre.clear()
        return start

    def feed(self, samples, cancel: Event | None = None) -> Iterator[StreamStep]:
        values = np.asarray(samples, dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != self.channels or not np.isfinite(values).all():
            raise AudioDataError("音声ストリームに不正な値または形式があります。")
        combined = np.concatenate((self._tail, values))
        complete = len(combined) // self.block * self.block
        self._tail = combined[complete:].copy()
        for offset in range(0, complete, self.block):
            check_cancel(cancel)
            block = combined[offset:offset + self.block]
            start, end = self._position, self._position + len(block)
            self._position = end
            centered = block - np.mean(block, axis=0, dtype=np.float64)
            rms = float(np.sqrt(np.mean(np.square(centered))))
            quiet = rms <= self.gate.fixed_quiet_level
            onset, window = None, None
            if self._suppress:
                if quiet:
                    if self._suppress_quiet is None:
                        self._suppress_quiet = start
                        self._suppress_values = []
                    self._suppress_values.append(rms)
                else:
                    self._suppress_quiet = None
                    self._suppress_values = []
                if (self._suppress_quiet is not None
                        and end - self._suppress_quiet >= self.release):
                    self._suppress, self._suppress_quiet = False, None
                    self.gate.resume_after_suppression(self._suppress_values)
                    self._suppress_values = []
                    self._pre.clear()
            else:
                gate_step = self.gate.step(rms, start, end)
                if gate_step.onset:
                    onset = self._begin(start, block)
                elif gate_step.retrigger:
                    window = self._window(start, gate_step.signal_end)
                    # Close the previous cue before announcing the new onset to
                    # keep sequence state transitions in chronological order.
                    yield StreamStep(start, False, True, window=window)
                    window = None
                    onset = self._begin(start, block)
                elif self._onset is None:
                    self._pre.append(block.copy())
                else:
                    remaining = self.limit - self._stored
                    if remaining > 0:
                        part = block[:remaining].copy()
                        self._parts.append(part)
                        self._stored += len(part)
                    if gate_step.release:
                        window = self._window(end, gate_step.signal_end)
                    if window is None and end - self._start >= self.limit:
                        window = self._window(end, end, "EVENT_LIMIT")
                        self._suppress = True
                        self._suppress_quiet = None
                        self._suppress_values = []
                        self.gate.abort()
            yield StreamStep(end, quiet, self._onset is not None or self._suppress, onset, window)
