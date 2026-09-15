"""Incremental native RMS windows with bounded template-onset lookahead."""
from collections import deque
from dataclasses import dataclass
from threading import Event
from collections.abc import Iterator
from typing import TYPE_CHECKING

import numpy as np

from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel
from detector.events import (
    EventWindow, MATCH_SAME_EVENT_SECONDS, MAX_EVENT_BYTES, RmsEventDetector,
    _AdaptiveRmsGate,
)

if TYPE_CHECKING:
    from detector.onset_matcher import OnsetMatch, TemplateOnsetMatcher


@dataclass(frozen=True, slots=True)
class StreamStep:
    end_frame: int
    silent: bool
    active: bool
    onset_frame: int | None = None
    window: EventWindow | None = None


@dataclass(frozen=True, slots=True)
class _GateCheckpoint:
    noise: tuple[float, ...]
    onset_frame: int | None
    quiet_start: int | None
    fixed_retrigger_start: int | None
    valley_start: int | None
    quiet_values: tuple[float, ...]
    active_noise: float | None
    active_start_level: float | None
    peak: float
    valley: float


@dataclass(frozen=True, slots=True)
class _StreamingCheckpoint:
    position: int
    input_position: int
    tail: np.ndarray
    queued: tuple[tuple[int, int, np.ndarray], ...]
    matches: tuple["OnsetMatch", ...]
    pre: tuple[np.ndarray, ...]
    parts: tuple[np.ndarray, ...]
    onset: int | None
    start: int | None
    matched: bool
    match_evidence: "OnsetMatch | None"
    stored: int
    suppress: bool
    finished: bool
    gate: _GateCheckpoint
    matcher: object | None


class StreamingRmsDetector:
    def __init__(
        self, sample_rate: int, channels: int, threshold: float = .025,
        start_frame: int = 0, onset_matcher: "TemplateOnsetMatcher | None" = None,
    ) -> None:
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
        self.limit = min(
            round(sample_rate * self.settings.max_window_seconds),
            MAX_EVENT_BYTES // (4 * channels),
        )
        self.max_feed_frames = MAX_EVENT_BYTES // (4 * channels)
        self.onset_matcher = onset_matcher
        self.same_event_frames = round(sample_rate * MATCH_SAME_EVENT_SECONDS)
        if onset_matcher is not None and (
            onset_matcher.sample_rate != sample_rate
            or onset_matcher.channels != channels
        ):
            raise AudioDataError("\u958b\u59cb\u691c\u51fa\u3068\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u306e\u5f62\u5f0f\u304c\u4e00\u81f4\u3057\u307e\u305b\u3093\u3002")
        if onset_matcher is not None and onset_matcher.origin_frame != start_frame:
            raise AudioDataError(
                "\u958b\u59cb\u691c\u51fa\u3068\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0"
                "\u306e\u958b\u59cb\u30d5\u30ec\u30fc\u30e0\u304c\u4e00\u81f4\u3057\u307e\u305b\u3093\u3002"
            )
        if onset_matcher is not None and not onset_matcher.pristine:
            raise AudioDataError(
                "\u958b\u59cb\u691c\u51fa\u306b\u306f\u672a\u4f7f\u7528\u306e"
                "\u30de\u30c3\u30c1\u30e3\u30fc\u304c\u5fc5\u8981\u3067\u3059\u3002"
            )
        queued_frames = (
            onset_matcher.lookback_frames + self.same_event_frames + self.block
            if onset_matcher is not None else 0
        )
        queued_blocks = max(1, (queued_frames + self.block - 1) // self.block)
        if queued_blocks * self.block * channels * 4 > MAX_EVENT_BYTES:
            from detector.onset_matcher import OnsetMatcherResourceLimit

            raise OnsetMatcherResourceLimit(
                "\u958b\u59cb\u691c\u51fa\u7528\u306e\u97f3\u58f0\u5c65\u6b74\u304c16 MiB\u3092\u8d85\u3048\u307e\u3059\u3002"
            )
        self._first = self._position = self._input_position = start_frame
        self._tail = np.empty((0, channels), dtype=np.float32)
        self._queued: deque[tuple[int, int, np.ndarray]] = deque()
        self._matches: deque[OnsetMatch] = deque()
        self._pre = deque(
            maxlen=max(1, (self.pre_frames + self.block - 1) // self.block)
        )
        self._parts: list[np.ndarray] = []
        self._onset = self._start = None
        self._matched = False
        self._match_evidence = None
        self._stored = 0
        self._suppress = False
        self._finished = False

    @property
    def retained_frames(self) -> int:
        queued = sum(len(part) for _, _, part in self._queued)
        matcher = (
            self.onset_matcher.retained_frames
            if self.onset_matcher is not None else 0
        )
        return (
            len(self._tail) + queued + matcher
            + sum(len(part) for part in self._pre) + self._stored
        )

    def _checkpoint_transaction(self) -> _StreamingCheckpoint:
        gate = self.gate
        return _StreamingCheckpoint(
            self._position,
            self._input_position,
            self._tail.copy(),
            tuple(
                (start, end, block.copy())
                for start, end, block in self._queued
            ),
            tuple(self._matches),
            tuple(part.copy() for part in self._pre),
            tuple(part.copy() for part in self._parts),
            self._onset,
            self._start,
            self._matched,
            self._match_evidence,
            self._stored,
            self._suppress,
            self._finished,
            _GateCheckpoint(
                tuple(gate.noise),
                gate.onset_frame,
                gate.quiet_start,
                gate.fixed_retrigger_start,
                gate.valley_start,
                tuple(gate.quiet_values),
                gate.active_noise,
                getattr(gate, "active_start_level", None),
                gate.peak,
                gate.valley,
            ),
            (
                self.onset_matcher._checkpoint_stream()
                if self.onset_matcher is not None else None
            ),
        )

    def _restore_transaction(self, checkpoint: _StreamingCheckpoint) -> None:
        self._position = checkpoint.position
        self._input_position = checkpoint.input_position
        self._tail = checkpoint.tail
        self._queued = deque(checkpoint.queued)
        self._matches = deque(checkpoint.matches)
        self._pre = deque(checkpoint.pre, maxlen=self._pre.maxlen)
        self._parts = list(checkpoint.parts)
        self._onset = checkpoint.onset
        self._start = checkpoint.start
        self._matched = checkpoint.matched
        self._match_evidence = checkpoint.match_evidence
        self._stored = checkpoint.stored
        self._suppress = checkpoint.suppress
        self._finished = checkpoint.finished
        gate = self.gate
        gate.noise.clear()
        gate.noise.extend(checkpoint.gate.noise)
        gate.onset_frame = checkpoint.gate.onset_frame
        gate.quiet_start = checkpoint.gate.quiet_start
        gate.fixed_retrigger_start = checkpoint.gate.fixed_retrigger_start
        gate.valley_start = checkpoint.gate.valley_start
        gate.quiet_values[:] = checkpoint.gate.quiet_values
        gate.active_noise = checkpoint.gate.active_noise
        gate.active_start_level = checkpoint.gate.active_start_level
        gate.peak = checkpoint.gate.peak
        gate.valley = checkpoint.gate.valley
        if self.onset_matcher is not None:
            self.onset_matcher._restore_stream(checkpoint.matcher)

    def _transaction_steps(
        self, operation: Iterator[StreamStep], cancel: Event | None,
    ) -> tuple[StreamStep, ...]:
        checkpoint = self._checkpoint_transaction()
        try:
            steps = tuple(operation)
            # Cancellation may arrive on another thread after the operation's
            # final internal check. Recheck before committing the checkpoint so
            # the whole public call remains atomic through its last step.
            check_cancel(cancel)
            return steps
        except Exception:
            self._restore_transaction(checkpoint)
            raise

    def _append(self, values: np.ndarray) -> None:
        remaining = self.limit - self._stored
        if remaining <= 0 or not len(values):
            return
        part = values[:remaining].copy()
        self._parts.append(part)
        self._stored += len(part)

    def _window(self, end: int, signal_end: int, reason: str = "") -> EventWindow:
        if not reason and self._onset == self._first:
            reason = "CLIPPED_EVENT"
        if (
            not reason
            and signal_end - self._onset
            < round(self.rate * self.settings.min_signal_seconds)
        ):
            reason = "SHORT_EVENT"
        retained = np.concatenate(self._parts)
        frame_count = max(0, min(len(retained), self.limit, end - self._start))
        samples = retained[:frame_count]
        evidence = self._match_evidence
        window = EventWindow(
            AudioClip(samples, self.rate), self._start, self._start + len(samples),
            self._onset, signal_end, reason, self._matched,
            evidence.oracle.value if evidence is not None else None,
            evidence.score if evidence is not None else None,
            evidence.margin if evidence is not None else None,
        )
        self._parts, self._stored = [], 0
        self._onset = self._start = None
        self._matched = False
        self._match_evidence = None
        self._pre.clear()
        for offset in range(
            max(0, len(samples) - self.pre_frames), len(samples), self.block,
        ):
            self._pre.append(samples[offset:offset + self.block].copy())
        return window

    def _begin(self, start: int, block: np.ndarray) -> int:
        prefix = (
            np.concatenate(tuple(self._pre))[-self.pre_frames:]
            if self._pre and self.pre_frames
            else np.empty((0, self.channels), dtype=np.float32)
        )
        self._onset = start
        self._matched = False
        self._match_evidence = None
        self._start = start - len(prefix)
        self._parts = [prefix.copy(), block.copy()]
        self._stored = len(prefix) + len(block)
        self._pre.clear()
        return start

    def _begin_external(
        self, match: "OnsetMatch", block_start: int, block: np.ndarray,
        history_before: np.ndarray,
    ) -> int:
        onset = match.onset_frame
        offset = onset - block_start
        if not 0 <= offset < len(block):
            raise AudioDataError("\u958b\u59cb\u691c\u51fa\u4f4d\u7f6e\u304c\u51e6\u7406\u533a\u9593\u306e\u7bc4\u56f2\u5916\u3067\u3059\u3002")
        prefix = history_before[-self.pre_frames:] if self.pre_frames else history_before[:0]
        suffix = block[offset:]
        self._onset = onset
        self._matched = True
        self._match_evidence = match
        self._start = onset - len(prefix)
        self._parts = [prefix.copy(), suffix.copy()]
        self._stored = len(prefix) + len(suffix)
        self._pre.clear()
        return onset

    def _quiet_history(self, block: np.ndarray, offset: int) -> np.ndarray:
        parts = [*self._pre]
        if offset:
            parts.append(block[:offset])
        if not parts:
            return np.empty((0, self.channels), dtype=np.float32)
        return np.concatenate(tuple(parts))

    def _take_match(self, start: int, end: int) -> "OnsetMatch | None":
        match = None
        while self._matches and self._matches[0].onset_frame < end:
            candidate = self._matches.popleft()
            if candidate.onset_frame < start:
                raise AudioDataError("\u958b\u59cb\u691c\u51fa\u306e\u9045\u5ef6\u4e0a\u9650\u3092\u8d85\u3048\u307e\u3057\u305f\u3002")
            if match is not None:
                raise AudioDataError("1\u533a\u9593\u306b\u8907\u6570\u306e\u958b\u59cb\u5019\u88dc\u304c\u3042\u308a\u307e\u3059\u3002")
            match = candidate
        return match

    def _process_block(
        self, block: np.ndarray, start: int, end: int,
    ) -> Iterator[StreamStep]:
        centered = block - np.mean(block, axis=0, dtype=np.float64)
        rms = float(np.sqrt(np.mean(np.square(centered))))
        quiet = rms <= self.gate.fixed_quiet_level
        onset, window = None, None
        match = self._take_match(start, end)
        if match is not None:
            offset = match.onset_frame - start
            if self._suppress or self._onset is None:
                history = self._quiet_history(block, offset)
            else:
                # Preserve the immediate pre-match samples even if the current
                # event reaches its storage limit partway through this block.
                history = np.concatenate((*self._parts, block[:offset]))
                self._append(block[:offset])
                if match.onset_frame > self._onset + self.same_event_frames:
                    limit_end = self._start + self.limit
                    limited = match.onset_frame >= limit_end
                    reason = "EVENT_LIMIT" if limited else ""
                    close_end = limit_end if limited else match.onset_frame
                    previous = self._window(close_end, close_end, reason)
                    if limited:
                        self._suppress = True
                    yield StreamStep(
                        close_end if limited else end,
                        False, True, window=previous,
                    )

            # A nearby RMS retrigger is only a rough boundary.  Rebuild the
            # current event at the exact registered signature instead of
            # ignoring the stronger match.
            self._suppress = False
            self.gate.force_begin(match.onset_frame, rms)
            onset = self._begin_external(
                match, start, block, history,
            )
            yield StreamStep(end, False, True, onset)
            return

        upcoming = self._matches[0] if self._matches else None
        allow_retrigger = not (
            upcoming is not None
            and upcoming.onset_frame <= start + self.same_event_frames
        )

        if self._suppress:
            gate_step = self.gate.step(
                rms, start, end, allow_retrigger=allow_retrigger,
            )
            quiet = gate_step.quiet
            if gate_step.retrigger:
                self._suppress = False
                onset = self._begin(start, block)
            else:
                self._pre.append(block.copy())
                if gate_step.release:
                    self._suppress = False
        else:
            gate_step = self.gate.step(
                rms, start, end, allow_retrigger=allow_retrigger,
            )
            quiet = gate_step.quiet
            if gate_step.onset:
                onset = self._begin(start, block)
            elif gate_step.retrigger:
                window = self._window(start, gate_step.signal_end)
                # Close the prior cue before announcing the new onset.
                yield StreamStep(end, False, True, window=window)
                window = None
                onset = self._begin(start, block)
            elif self._onset is None:
                self._pre.append(block.copy())
            else:
                self._append(block)
                if gate_step.release:
                    window = self._window(end, gate_step.signal_end)
                if window is None and end - self._start >= self.limit:
                    limit_end = self._start + self.limit
                    window = self._window(
                        limit_end, limit_end, "EVENT_LIMIT",
                    )
                    self._suppress = True
        yield StreamStep(
            end, quiet, self._onset is not None or self._suppress, onset, window,
        )

    def _match_transaction(
        self, blocks: tuple[np.ndarray, ...], cancel: Event | None,
        *, finish: bool = False,
    ) -> tuple["OnsetMatch", ...]:
        """Advance the matcher inside the detector's enclosing transaction."""
        if self.onset_matcher is None:
            return ()
        matches = []
        for block in blocks:
            check_cancel(cancel)
            matches.extend(self.onset_matcher.feed(block, cancel))
        if finish:
            matches.extend(self.onset_matcher.finish(cancel))
        check_cancel(cancel)
        return tuple(matches)

    def feed(self, samples, cancel: Event | None = None) -> Iterator[StreamStep]:
        check_cancel(cancel)
        steps = self._transaction_steps(self._feed(samples, cancel), cancel)
        yield from steps

    def _feed(self, samples, cancel: Event | None = None) -> Iterator[StreamStep]:
        if self._finished:
            raise AudioDataError(
                "\u7d42\u4e86\u3057\u305f\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0"
                "\u3078\u97f3\u58f0\u3092\u8ffd\u52a0\u3067\u304d\u307e\u305b\u3093\u3002"
            )
        try:
            supplied_frames = len(samples)
        except (TypeError, ValueError, OverflowError) as error:
            raise AudioDataError(
                "\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u306b\u4e0d\u6b63\u306a\u5024"
                "\u307e\u305f\u306f\u5f62\u5f0f\u304c\u3042\u308a\u307e\u3059\u3002"
            ) from error
        if supplied_frames is not None and supplied_frames > self.max_feed_frames:
            raise AudioDataError(
                "1\u56de\u306e\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u5165\u529b\u304c"
                "16 MiB\u3092\u8d85\u3048\u307e\u3059\u3002"
            )
        try:
            source = np.asarray(samples)
        except (TypeError, ValueError, OverflowError) as error:
            raise AudioDataError(
                "\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u306b\u4e0d\u6b63\u306a\u5024"
                "\u307e\u305f\u306f\u5f62\u5f0f\u304c\u3042\u308a\u307e\u3059\u3002"
            ) from error
        if (
            source.ndim != 2
            or source.shape[1] != self.channels
            or source.dtype.kind not in "fiu"
        ):
            raise AudioDataError(
                "\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u306b\u4e0d\u6b63\u306a\u5024"
                "\u307e\u305f\u306f\u5f62\u5f0f\u304c\u3042\u308a\u307e\u3059\u3002"
            )
        if len(source) > self.max_feed_frames:
            raise AudioDataError(
                "1\u56de\u306e\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u5165\u529b\u304c"
                "16 MiB\u3092\u8d85\u3048\u307e\u3059\u3002"
            )
        if not np.isfinite(source).all():
            raise AudioDataError(
                "\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u306b\u4e0d\u6b63\u306a\u5024"
                "\u307e\u305f\u306f\u5f62\u5f0f\u304c\u3042\u308a\u307e\u3059\u3002"
            )
        with np.errstate(over="ignore", invalid="ignore"):
            values = source.astype(np.float32, copy=False)
        if not np.isfinite(values).all():
            raise AudioDataError(
                "\u97f3\u58f0\u30b9\u30c8\u30ea\u30fc\u30e0\u306e\u5024\u304c"
                "\u5927\u304d\u3059\u304e\u307e\u3059\u3002"
            )
        check_cancel(cancel)

        combined = np.concatenate((self._tail, values))
        complete = len(combined) // self.block * self.block
        next_tail = combined[complete:].copy()

        if self.onset_matcher is not None:
            pending = tuple(
                combined[offset:offset + self.block].copy()
                for offset in range(0, complete, self.block)
            )
            matches = self._match_transaction(pending, cancel)
            start = self._input_position
            queued = tuple(
                (start + offset, start + offset + len(block), block)
                for offset, block in zip(range(0, complete, self.block), pending)
            )
            self._tail = next_tail
            self._queued.extend(queued)
            self._matches.extend(matches)
            self._input_position += complete
        else:
            self._tail = next_tail
            for offset in range(0, complete, self.block):
                check_cancel(cancel)
                block = combined[offset:offset + self.block]
                start = self._input_position
                end = start + len(block)
                self._input_position = end
                self._position = end
                yield from self._process_block(block, start, end)

        safe_end = (
            self._input_position - self.onset_matcher.lookback_frames
            - self.same_event_frames
            if self.onset_matcher is not None else self._input_position
        )
        while self._queued and self._queued[0][1] <= safe_end:
            check_cancel(cancel)
            start, end, block = self._queued.popleft()
            self._position = end
            yield from self._process_block(block, start, end)

    def finish(self, cancel: Event | None = None) -> Iterator[StreamStep]:
        """Resolve matcher lookahead and the final partial native block once."""
        check_cancel(cancel)
        steps = self._transaction_steps(self._finish(cancel), cancel)
        yield from steps

    def _finish(self, cancel: Event | None = None) -> Iterator[StreamStep]:
        check_cancel(cancel)
        if self._finished:
            return

        if self.onset_matcher is not None:
            pending = (self._tail.copy(),) if len(self._tail) else ()
            matches = self._match_transaction(pending, cancel, finish=True)
            if pending:
                block = pending[0]
                start = self._input_position
                end = start + len(block)
                self._queued.append((start, end, block))
                self._input_position = end
            self._tail = np.empty((0, self.channels), dtype=np.float32)
            self._matches.extend(matches)
            while self._queued:
                check_cancel(cancel)
                start, end, block = self._queued.popleft()
                self._position = end
                yield from self._process_block(block, start, end)
            if self._matches:
                raise AudioDataError(
                    "\u958b\u59cb\u691c\u51fa\u306e\u672b\u5c3e\u5019\u88dc\u3092"
                    "\u97f3\u58f0\u533a\u9593\u306b\u5272\u308a\u5f53\u3066\u3089\u308c"
                    "\u307e\u305b\u3093\u3002"
                )
        elif len(self._tail):
            block = self._tail
            self._tail = np.empty((0, self.channels), dtype=np.float32)
            start = self._input_position
            end = start + len(block)
            self._input_position = end
            self._position = end
            yield from self._process_block(block, start, end)

        final_window = None
        if self._onset is not None:
            signal_end = (
                self.gate.quiet_start
                if self.gate.quiet_start is not None else self._input_position
            )
            short = (
                signal_end - self._onset
                < round(self.rate * self.settings.min_signal_seconds)
            )
            reason = "SHORT_EVENT" if short else "CLIPPED_EVENT"
            final_window = self._window(
                self._input_position, signal_end, reason,
            )
        self._finished = True
        if final_window is not None:
            yield StreamStep(
                self._input_position, True, False, window=final_window,
            )
