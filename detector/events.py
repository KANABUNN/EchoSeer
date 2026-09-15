"""Finite native audio event windows, detected before amplitude normalization."""
from collections import deque
from dataclasses import dataclass
import math
from threading import Event
from collections.abc import Iterator
from typing import TYPE_CHECKING

import numpy as np

from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel

if TYPE_CHECKING:
    from detector.onset_matcher import TemplateOnsetMatcher

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 128
AUTO_NOISE_SECONDS = 1.0
AUTO_WARMUP_SECONDS = 0.2
AUTO_MIN_LEVEL = 0.002
AUTO_NOISE_PERCENTILE = 20
AUTO_NOISE_RATIO = 3.0
AUTO_LOUD_NOISE_RATIO = 2.0
AUTO_QUIET_RATIO = 1.5
LOCAL_RETRIGGER_SECONDS = 1.0
LOCAL_DECAY_RATIO = 0.55
LOCAL_RISE_RATIO = 1.8
MATCH_SAME_EVENT_SECONDS = 0.2


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
        self.active_noise: float | None = None
        self.peak = 0.0
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
        if baseline <= self.settings.threshold / AUTO_NOISE_RATIO:
            return min(self.settings.threshold, adaptive), baseline
        return max(self.settings.threshold, baseline * AUTO_LOUD_NOISE_RATIO), baseline

    def _begin(self, start: int, rms: float, level: float, baseline: float | None) -> None:
        self.onset_frame = start
        self.active_noise = baseline
        self.active_start_level = level
        self.peak = rms
        self.quiet_start = self.fixed_retrigger_start = self.valley_start = None
        self.quiet_values.clear()
        self.valley = math.inf

    def _reset_event(self, remember_quiet: bool) -> None:
        if remember_quiet:
            self.noise.clear()
            self.noise.extend(self.quiet_values)
        self.onset_frame = self.quiet_start = self.fixed_retrigger_start = None
        self.valley_start = None
        self.quiet_values.clear()
        self.active_noise = None
        self.peak = 0.0
        self.valley = math.inf

    def abort(self) -> None:
        """Drop the current event and its recent level history."""
        self._reset_event(False)
        self.noise.clear()

    def force_begin(self, onset: int, rms: float) -> None:
        """Start at an externally confirmed onset while retaining the recent floor."""
        level, baseline = self._start_level()
        # A loud steady background can make its percentile-derived start level
        # exceed the matched block itself.  Keep release below the level that
        # was explicitly matched so the cue is not closed after one block.
        level = min(level, max(rms, 1e-6))
        self._begin(onset, rms, level, baseline)

    def step(
        self, rms: float, start: int, end: int, *, allow_retrigger: bool = True,
    ) -> _RmsStep:
        if self.onset_frame is None:
            level, baseline = self._start_level()
            if rms > 1e-6 and rms >= level:
                self._begin(start, rms, level, baseline)
                return _RmsStep(False, onset=True)
            self.noise.append(rms)
            return _RmsStep(rms <= level)

        baseline = self._baseline()
        self.noise.append(rms)
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

        if allow_retrigger and (fixed_retrigger or local_retrigger):
            level, fresh_baseline = self._start_level()
            if fresh_baseline is not None:
                baseline = fresh_baseline
            if baseline is None:
                baseline = self.active_noise
            self._begin(start, rms, level, baseline)
            return _RmsStep(False, retrigger=True, signal_end=start)

        if self.fixed_quiet_level < rms < self.settings.threshold:
            if self.fixed_retrigger_start is None:
                self.fixed_retrigger_start = start
        else:
            self.fixed_retrigger_start = None

        adaptive_quiet_level = (
            max(
                AUTO_MIN_LEVEL * 0.4,
                self.active_noise * AUTO_QUIET_RATIO,
                1e-6,
            )
            if self.active_noise is not None
            else self.fixed_quiet_level
        )
        active_quiet_level = min(
            self.active_start_level * 0.75, adaptive_quiet_level,
        )
        quiet = rms <= active_quiet_level
        if quiet:
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
    matched: bool = False
    match_oracle: str | None = None
    match_score: float | None = None
    match_margin: float | None = None

    @property
    def onset_detection(self) -> dict:
        """Serializable coarse-onset evidence for diagnostics and tuning."""
        return {
            "matched": self.matched,
            "candidate": self.match_oracle,
            "score": self.match_score,
            "margin": self.match_margin,
        }


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

    def detect(
        self, original: AudioClip, cancel: Event | None = None,
        onset_matcher: "TemplateOnsetMatcher | None" = None,
    ) -> Iterator[EventWindow]:
        rate = original.sample_rate
        block = max(1, round(rate * self.block_seconds))
        pre = round(rate * self.pre_roll)
        same_event = round(rate * MATCH_SAME_EVENT_SECONDS)
        if onset_matcher is not None and (
            onset_matcher.sample_rate != rate
            or onset_matcher.channels != original.channels
        ):
            raise AudioDataError(
                "\u958b\u59cb\u691c\u51fa\u3068\u97f3\u58f0\u306e"
                "\u5f62\u5f0f\u304c\u4e00\u81f4\u3057\u307e\u305b\u3093\u3002"
            )
        if onset_matcher is not None and onset_matcher.origin_frame != 0:
            raise AudioDataError(
                "\u6709\u9650\u97f3\u58f0\u306e\u958b\u59cb\u691c\u51fa\u306f"
                "0\u30d5\u30ec\u30fc\u30e0\u304b\u3089\u958b\u59cb\u3059\u308b\u5fc5\u8981\u304c\u3042\u308a\u307e\u3059\u3002"
            )

        if onset_matcher is not None and not onset_matcher.pristine:
            raise AudioDataError(
                "\u6709\u9650\u97f3\u58f0\u306e\u958b\u59cb\u691c\u51fa\u306b\u306f"
                "\u672a\u4f7f\u7528\u306e\u30de\u30c3\u30c1\u30e3\u30fc\u304c\u5fc5\u8981\u3067\u3059\u3002"
            )

        # Resolve template peaks before walking the finite RMS timeline.  A peak
        # needs future audio to become final; precomputing keeps all emitted
        # event times chronological and also lets a match start below the RMS
        # floor.
        matches = []
        if onset_matcher is not None:
            checkpoint = onset_matcher._checkpoint_stream()
            try:
                for matcher_start in range(0, original.frame_count, block):
                    check_cancel(cancel)
                    values = original.samples[matcher_start:matcher_start + block]
                    matches.extend(onset_matcher.feed(values, cancel))
                matches.extend(onset_matcher.finish(cancel))
                matches.sort(key=lambda item: item.onset_frame)
            finally:
                # Finite analysis borrows a matcher without consuming its stream.
                # This also makes cancellation safe for callers that retry.
                onset_matcher._restore_stream(checkpoint)
        match_by_frame = {item.onset_frame: item for item in matches}
        match_index = 0

        gate = _AdaptiveRmsGate(self, rate)
        limit = min(
            round(rate * self.max_window_seconds),
            MAX_EVENT_BYTES // (4 * original.channels),
        )
        onset = None
        onset_matched = False
        suppressed = False
        count = 0

        def window(
            event_onset, end, signal_end, eof=False, forced_reason="", matched=False,
        ):
            start = max(0, event_onset - pre)
            reason = "CLIPPED_EVENT" if event_onset == 0 or eof else ""
            if signal_end - event_onset < round(rate * self.min_signal_seconds):
                reason = "SHORT_EVENT"
            if forced_reason:
                reason = forced_reason
            stop = min(end, start + limit)
            match = match_by_frame.get(event_onset) if matched else None
            return EventWindow(
                AudioClip(original.samples[start:stop], rate),
                start, stop, event_onset, signal_end, reason, matched,
                match.oracle.value if match is not None else None,
                match.score if match is not None else None,
                match.margin if match is not None else None,
            )

        def check_count():
            nonlocal count
            count += 1
            if count > MAX_EVENTS:
                raise AudioDataError(
                    "\u97f3\u306e\u5019\u88dc\u304c\u591a\u3059\u304e\u307e\u3059\u3002"
                    " \u30e9\u30a6\u30f3\u30c9\u306e\u77ed\u3044\u533a\u9593\u3092"
                    "\u9078\u3093\u3067\u304f\u3060\u3055\u3044\u3002"
                )

        for start in range(0, original.frame_count, block):
            check_cancel(cancel)
            values = original.samples[start:start + block]
            centered = values - np.mean(values, axis=0, dtype=np.float64)
            rms = float(np.sqrt(np.mean(np.square(centered))))
            end = start + len(values)

            match = None
            while match_index < len(matches) and matches[match_index].onset_frame < end:
                candidate = matches[match_index]
                match_index += 1
                if candidate.onset_frame < start:
                    raise AudioDataError(
                        "\u958b\u59cb\u691c\u51fa\u306e\u6642\u523b\u304c"
                        "\u51e6\u7406\u4e2d\u306e\u533a\u9593\u3088\u308a\u524d\u3067\u3059\u3002"
                    )
                if match is not None:
                    raise AudioDataError(
                        "1\u533a\u9593\u306b\u8907\u6570\u306e"
                        "\u958b\u59cb\u5019\u88dc\u304c\u3042\u308a\u307e\u3059\u3002"
                    )
                match = candidate

            if match is not None:
                exact_onset = match.onset_frame
                if suppressed:
                    suppressed = False
                elif onset is not None:
                    event_start = max(0, onset - pre)
                    limit_end = event_start + limit
                    if exact_onset >= limit_end:
                        check_count()
                        yield window(
                            onset, limit_end, limit_end,
                            forced_reason="EVENT_LIMIT", matched=onset_matched,
                        )
                    elif exact_onset > onset + same_event:
                        check_count()
                        yield window(
                            onset, exact_onset, exact_onset,
                            matched=onset_matched,
                        )

                # If RMS fired shortly before the signature, replace that rough
                # boundary instead of discarding the stronger exact onset.
                onset = exact_onset
                onset_matched = True
                gate.force_begin(exact_onset, rms)
                continue

            upcoming = matches[match_index] if match_index < len(matches) else None
            allow_retrigger = not (
                upcoming is not None
                and upcoming.onset_frame <= start + same_event
            )

            if suppressed:
                step = gate.step(
                    rms, start, end, allow_retrigger=allow_retrigger,
                )
                if step.retrigger or step.release:
                    onset = start if step.retrigger else None
                    onset_matched = False
                    suppressed = False
                continue

            step = gate.step(
                rms, start, end, allow_retrigger=allow_retrigger,
            )
            if step.onset:
                onset = start
                onset_matched = False
            elif step.retrigger:
                check_count()
                yield window(
                    onset, start, step.signal_end, matched=onset_matched,
                )
                onset = start
                onset_matched = False
            elif step.release:
                check_count()
                yield window(
                    onset, end, step.signal_end, matched=onset_matched,
                )
                onset = None
                onset_matched = False
            elif onset is not None:
                event_start = max(0, onset - pre)
                if end - event_start >= limit:
                    limit_end = event_start + limit
                    check_count()
                    yield window(
                        onset, limit_end, limit_end,
                        forced_reason="EVENT_LIMIT", matched=onset_matched,
                    )
                    onset = None
                    onset_matched = False
                    suppressed = True

        check_cancel(cancel)
        if onset is not None:
            if count >= MAX_EVENTS:
                raise AudioDataError(
                    "\u97f3\u306e\u5019\u88dc\u304c\u591a\u3059\u304e\u307e\u3059\u3002"
                    " \u30e9\u30a6\u30f3\u30c9\u306e\u77ed\u3044\u533a\u9593\u3092"
                    "\u9078\u3093\u3067\u304f\u3060\u3055\u3044\u3002"
                )
            signal_end = (
                gate.quiet_start
                if gate.quiet_start is not None else original.frame_count
            )
            yield window(
                onset, original.frame_count, signal_end,
                eof=True, matched=onset_matched,
            )
