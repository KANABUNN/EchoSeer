"""Finite native audio event windows, detected before amplitude normalization."""
from dataclasses import dataclass
import math
from threading import Event
from collections.abc import Iterator
import numpy as np
from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 128


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
        quiet_frames = round(rate * self.release_seconds)
        retrigger_frames = round(rate * self.retrigger_seconds)
        quiet_level = max(self.threshold * .4, 1e-6)
        retrigger_level = max(self.threshold * self.retrigger_ratio, 1e-6)
        limit = min(round(rate * self.max_window_seconds), MAX_EVENT_BYTES // (4 * original.channels))
        onset = quiet_start = retrigger_start = None
        count = 0

        def window(end, signal_end, eof=False):
            start = max(0, onset - pre)
            reason = ("CLIPPED_EVENT" if onset == 0 or eof else "")
            if signal_end - onset < round(rate * self.min_signal_seconds):
                reason = "SHORT_EVENT"
            if end - start > limit:
                reason = "EVENT_LIMIT"
            stop = min(end, start + limit)
            return EventWindow(AudioClip(original.samples[start:stop], rate), start, stop, onset, signal_end, reason)

        for start in range(0, original.frame_count, block):
            check_cancel(cancel)
            values = original.samples[start:start + block]
            centered = values - np.mean(values, axis=0, dtype=np.float64)
            rms = float(np.sqrt(np.mean(np.square(centered))))
            end = start + len(values)
            if onset is None:
                if rms > 1e-6 and rms >= self.threshold:
                    onset, quiet_start = start, None
            else:
                retriggered = (
                    retrigger_start is not None
                    and start - retrigger_start >= retrigger_frames
                    and rms >= retrigger_level
                )
                if retriggered:
                    count += 1
                    if count > MAX_EVENTS:
                        raise AudioDataError("音の候補が多すぎます。1 ラウンドの短い区間を選んでください。")
                    yield window(start, start)
                    onset, quiet_start, retrigger_start = start, None, None
                    continue
                if quiet_level < rms < self.threshold:
                    if retrigger_start is None:
                        retrigger_start = start
                else:
                    retrigger_start = None
                if rms <= quiet_level:
                    if quiet_start is None:
                        quiet_start = start
                    if end - quiet_start >= quiet_frames:
                        count += 1
                        if count > MAX_EVENTS:
                            raise AudioDataError("音の候補が多すぎます。1 ラウンドの短い区間を選んでください。")
                        yield window(end, quiet_start)
                        onset = quiet_start = retrigger_start = None
                else:
                    quiet_start = None
        check_cancel(cancel)
        if onset is not None:
            if count >= MAX_EVENTS:
                raise AudioDataError("音の候補が多すぎます。1 ラウンドの短い区間を選んでください。")
            yield window(original.frame_count, quiet_start if quiet_start is not None else original.frame_count, eof=True)
