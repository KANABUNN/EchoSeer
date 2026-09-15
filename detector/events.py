"""Finite native audio event windows, detected before amplitude normalization."""
from collections import deque
from dataclasses import dataclass
import math
from threading import Event
from collections.abc import Iterator

import numpy as np

from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 128
AUTO_NOISE_SECONDS = 1.0
AUTO_WARMUP_SECONDS = 0.2
AUTO_MIN_LEVEL = 0.002
AUTO_NOISE_PERCENTILE = 20
AUTO_NOISE_RATIO = 3.0
AUTO_QUIET_RATIO = 1.5
LOCAL_RETRIGGER_SECONDS = 1.0
LOCAL_DECAY_RATIO = 0.55
LOCAL_RISE_RATIO = 1.8


@dataclass(frozen=True, slots=True)
class _RmsStep:
    quiet: bool
    onset: bool = False
    retrigger: bool = False
    release: bool = False
    signal_end: int | None = None


class _AdaptiveRmsGate:
    """Shared block state for finite and streaming event boundaries."""

    def __init__(self, settings: "RmsEventDetector", sample_rate: int) -> None:
        self.settings = settings
        self.release_frames = round(sample_rate * settings.release_seconds)
        self.retrigger_frames = round(sample_rate * settings.retrigger_seconds)
        self.local_retrigger_frames = round(sample_rate * LOCAL_RETRIGGER_SECONDS)
        blocks_per_second = max(1, round(AUTO_NOISE_SECONDS / settings.block_seconds))
        self.warmup_blocks = max(1, math.ceil(AUTO_WARMUP_SECONDS / settings.block_seconds))
        self.noise = deque(maxlen=blocks_per_second)
        self.fixed_quiet_level = max(settings.threshold * 0.4, 1e-6)
        self.fixed_retrigger_level = max(
            settings.threshold * settings.retrigger_ratio, 1e-6
        )
        self.onset_frame = self.quiet_start = self.fixed_retrigger_start = None
        self.valley_start = None
        self.quiet_values: list[float] = []
        self.active_noise = self.peak = 0.0
        self.valley = math.inf

    def _baseline(self) -> float | None:
        if len(self.noise) < self.warmup_blocks:
            return None
        return float(np.percentile(np.asarray(self.noise), AUTO_NOISE_PERCENTILE))

    def _start_level(self) -> tuple[float, float | None]:
        baseline = self._baseline()
        if baseline is None:
            return self.settings.threshold, None
        adaptive = max(AUTO_MIN_LEVEL, baseline * AUTO_NOISE_RATIO)
        return min(self.settings.threshold, adaptive), baseline

    def _begin(self, start: int, rms: float, level: float, baseline: float | None) -> None:
        self.onset_frame = start
        self.active_noise = baseline if baseline is not None else 0.0
        self.active_start_level = level
        self.peak = rms
        self.quiet_start = self.fixed_retrigger_start = self.valley_start = None
        self.quiet_values.clear()
        self.valley = math.inf

    def _reset_event(self, remember_quiet: bool) -> None:
        if remember_quiet:
            self.noise.extend(self.quiet_values)
        self.onset_frame = self.quiet_start = self.fixed_retrigger_start = None
        self.valley_start = None
        self.quiet_values.clear()
        self.active_noise = self.peak = 0.0
        self.valley = math.inf

    def abort(self) -> None:
        """Drop an overlong event before streaming suppression."""
        self._reset_event(False)
        self.noise.clear()

    def resume_after_suppression(self, quiet_values) -> None:
        self.noise.clear()
        self.noise.extend(quiet_values)

    def step(self, rms: float, start: int, end: int) -> _RmsStep:
        quiet = rms <= self.fixed_quiet_level
        if self.onset_frame is None:
            level, baseline = self._start_level()
            if rms > 1e-6 and rms >= level:
                self._begin(start, rms, level, baseline)
                return _RmsStep(quiet, onset=True)
            self.noise.append(rms)
            return _RmsStep(quiet)

        self.peak = max(self.peak, rms)
        fixed_retrigger = (
            self.fixed_retrigger_start is not None
            and start - self.fixed_retrigger_start >= self.retrigger_frames
            and rms >= self.fixed_retrigger_level
        )
        decay_level = self.peak * LOCAL_DECAY_RATIO
        local_retrigger = False
        if rms <= decay_level:
            if self.valley_start is None:
                self.valley_start = start
            self.valley = min(self.valley, rms)
        elif self.valley_start is not None:
            local_retrigger = (
                start - self.valley_start >= self.retrigger_frames
                and start - self.onset_frame >= self.local_retrigger_frames
                and rms >= max(AUTO_MIN_LEVEL, decay_level, self.valley * LOCAL_RISE_RATIO)
            )
            if not local_retrigger:
                self.valley_start = None
                self.valley = math.inf

        if fixed_retrigger or local_retrigger:
            level, baseline = self.active_start_level, self.active_noise
            self._begin(start, rms, level, baseline)
            return _RmsStep(quiet, retrigger=True, signal_end=start)

        if self.fixed_quiet_level < rms < self.settings.threshold:
            if self.fixed_retrigger_start is None:
                self.fixed_retrigger_start = start
        else:
            self.fixed_retrigger_start = None

        active_quiet_level = min(
            self.fixed_quiet_level,
            max(AUTO_MIN_LEVEL * 0.4, self.active_noise * AUTO_QUIET_RATIO, 1e-6),
        )
        if rms <= active_quiet_level:
            if self.quiet_start is None:
                self.quiet_start = start
            self.quiet_values.append(rms)
            if end - self.quiet_start >= self.release_frames:
                signal_end = self.quiet_start
                self._reset_event(True)
                return _RmsStep(quiet, release=True, signal_end=signal_end)
        else:
            self.quiet_start = None
            self.quiet_values.clear()
        return _RmsStep(quiet)


@dataclass(frozen=True, slots=True)
class EventWindow:
    clip: AudioClip
    start_frame: int
    end_frame: int
    onset_frame: int
    signal_end_frame: int
    reason: str = ""


class RmsEventDetector:
    def __init__(self, threshold: float = .025, block_seconds: float = .02,
                 pre_roll: float = .06, release_seconds: float = .12,
                 min_signal_seconds: float = .08, max_window_seconds: float = 3.0,
                 retrigger_seconds: float = .06, retrigger_ratio: float = 1.5) -> None:
        values = (threshold, block_seconds, pre_roll, release_seconds, min_signal_seconds,
                  max_window_seconds, retrigger_seconds, retrigger_ratio)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("Non-finite event detector settings")
        if not (0 <= threshold <= 1 and .005 <= block_seconds <= .1 and 0 <= pre_roll <= .5
                and block_seconds <= retrigger_seconds < release_seconds <= .5
                and 1 < retrigger_ratio <= 4 and .02 <= min_signal_seconds <= 1
                and 1 <= max_window_seconds <= 10):
            raise ValueError("Invalid event detector settings")
        self.threshold, self.block_seconds, self.pre_roll = threshold, block_seconds, pre_roll
        self.release_seconds, self.min_signal_seconds, self.max_window_seconds = (
            release_seconds, min_signal_seconds, max_window_seconds
        )
        self.retrigger_seconds, self.retrigger_ratio = retrigger_seconds, retrigger_ratio

    def detect(self, original: AudioClip, cancel: Event | None = None) -> Iterator[EventWindow]:
        rate = original.sample_rate
        block = max(1, round(rate * self.block_seconds))
        pre = round(rate * self.pre_roll)
        gate = _AdaptiveRmsGate(self, rate)
        limit = min(round(rate * self.max_window_seconds), MAX_EVENT_BYTES // (4 * original.channels))
        onset = pending_onset = pending_end = suppress_quiet = None
        suppress_values: list[float] = []
        suppressed = False
        count = 0

        def window(event_onset, end, signal_end, eof=False, forced_reason=""):
            start = max(0, event_onset - pre)
            reason = "CLIPPED_EVENT" if event_onset == 0 or eof else ""
            if signal_end - event_onset < round(rate * self.min_signal_seconds):
                reason = "SHORT_EVENT"
            if forced_reason:
                reason = forced_reason
            stop = min(end, start + limit)
            return EventWindow(
                AudioClip(original.samples[start:stop], rate),
                start, stop, event_onset, signal_end, reason,
            )

        def check_count():
            nonlocal count
            count += 1
            if count > MAX_EVENTS:
                raise AudioDataError("音の候補が多すぎます。1 ラウンドの短い区間を選んでください。")

        for start in range(0, original.frame_count, block):
            check_cancel(cancel)
            values = original.samples[start:start + block]
            centered = values - np.mean(values, axis=0, dtype=np.float64)
            rms = float(np.sqrt(np.mean(np.square(centered))))
            end = start + len(values)
            if suppressed:
                quiet = rms <= gate.fixed_quiet_level
                if quiet:
                    if suppress_quiet is None:
                        suppress_quiet = start
                        suppress_values = []
                    suppress_values.append(rms)
                else:
                    suppress_quiet = None
                    suppress_values = []
                if (suppress_quiet is not None
                        and end - suppress_quiet >= gate.release_frames):
                    check_count()
                    yield window(
                        pending_onset, pending_end, suppress_quiet,
                        forced_reason="EVENT_LIMIT",
                    )
                    gate.resume_after_suppression(suppress_values)
                    onset = pending_onset = pending_end = suppress_quiet = None
                    suppress_values = []
                    suppressed = False
                continue

            step = gate.step(rms, start, end)
            if step.onset:
                onset = start
            elif step.retrigger:
                check_count()
                yield window(onset, start, step.signal_end)
                onset = start
            elif step.release:
                check_count()
                yield window(onset, end, step.signal_end)
                onset = None
            elif onset is not None:
                event_start = max(0, onset - pre)
                if end - event_start >= limit:
                    pending_onset = onset
                    pending_end = event_start + limit
                    onset = None
                    suppressed = True
                    suppress_quiet = None
                    suppress_values = []
                    gate.abort()
        check_cancel(cancel)
        if suppressed:
            if count >= MAX_EVENTS:
                raise AudioDataError("音の候補が多すぎます。1 ラウンドの短い区間を選んでください。")
            signal_end = suppress_quiet if suppress_quiet is not None else original.frame_count
            yield window(
                pending_onset, pending_end, signal_end, forced_reason="EVENT_LIMIT",
            )
        elif onset is not None:
            if count >= MAX_EVENTS:
                raise AudioDataError("音の候補が多すぎます。1 ラウンドの短い区間を選んでください。")
            signal_end = gate.quiet_start if gate.quiet_start is not None else original.frame_count
            yield window(onset, original.frame_count, signal_end, eof=True)
