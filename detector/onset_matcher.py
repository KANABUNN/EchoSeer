"""Bounded streaming onset proposals from short registered waveforms."""

from dataclasses import dataclass
import logging
import math
from threading import Event

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled, check_cancel
from detector.classifier import OracleClassifier
from dsp.preprocess import preprocess_clip
from encounter.vog_oracles import OracleId


logger = logging.getLogger("oracle_assistant.onset_matcher")

TEMPLATE_SECONDS = 0.4
HOP_SECONDS = 0.02
SEARCH_BATCH_HOPS = 10
DEFAULT_THRESHOLD = 0.60
DEFAULT_MARGIN = 0.10
DEFAULT_REFRACTORY_SECONDS = 0.9
MAX_TEMPLATES = 140
MAX_MATCHER_BYTES = 128 * 1024 * 1024
MATCHER_MEMORY_RESERVE_BYTES = 8 * 1024 * 1024
MAX_FEED_SECONDS = 30.0
SILENCE_ENERGY = 1e-12


class OnsetTemplatesUnavailable(AudioDataError):
    """No usable onset signatures exist; callers may retain RMS-only detection."""


class OnsetMatcherResourceLimit(AudioDataError):
    """The optional matcher cannot fit its bounded template/working set."""


@dataclass(frozen=True, slots=True)
class OnsetMatch:
    onset_frame: int
    oracle: OracleId
    score: float
    margin: float


@dataclass(frozen=True, slots=True)
class _ScoredWindow:
    onset_frame: int
    oracle: OracleId
    score: float
    margin: float


@dataclass(frozen=True, slots=True)
class _MatcherCheckpoint:
    buffer: np.ndarray
    buffer_start: int
    stream_end: int
    next_start: int
    older: _ScoredWindow | None
    previous: _ScoredWindow | None
    last_match: int | None
    finished: bool


class TemplateOnsetMatcher:
    """Find local correlation peaks without depending on silence boundaries.

    Every registered template is converted to mono at the input's native rate
    once. Correlations cover every possible sample position, and the strongest
    position in each fixed 20 ms interval becomes one score. Processing uses
    fixed 200 ms overlap-save batches, so callback chunk boundaries cannot move
    a score or estimated onset.
    """

    def __init__(
        self,
        classifier: OracleClassifier,
        sample_rate: int,
        channels: int,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        margin_threshold: float = DEFAULT_MARGIN,
        refractory_seconds: float = DEFAULT_REFRACTORY_SECONDS,
        start_frame: int = 0,
        cancel: Event | None = None,
    ) -> None:
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 384000:
            raise ValueError("Invalid onset matcher sample rate")
        if type(channels) is not int or not 1 <= channels <= 64:
            raise ValueError("Invalid onset matcher channel count")
        values = (threshold, margin_threshold, refractory_seconds)
        if any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("Non-finite onset matcher setting")
        if not 0 <= threshold <= 1 or not 0 <= margin_threshold <= 1:
            raise ValueError("Invalid onset matcher score setting")
        if not 0.8 <= refractory_seconds <= 1.0:
            raise ValueError("Onset matcher refractory must be 0.8 to 1.0 seconds")
        if type(start_frame) is not int or start_frame < 0:
            raise ValueError("Invalid onset matcher stream frame")

        self.sample_rate = sample_rate
        self.channels = channels
        self.threshold = float(threshold)
        self.margin_threshold = float(margin_threshold)
        self.window_frames = round(sample_rate * TEMPLATE_SECONDS)
        self.hop_frames = max(1, round(sample_rate * HOP_SECONDS))
        self.batch_frames = self.hop_frames * SEARCH_BATCH_HOPS
        self.lookback_frames = (
            self.window_frames + self.batch_frames + self.hop_frames
        )
        self.refractory_frames = round(sample_rate * refractory_seconds)
        self.max_feed_frames = round(sample_rate * MAX_FEED_SECONDS)
        # A public feed may contain up to 30 seconds, but conversion and scoring
        # always consume one fixed search batch at a time.
        self._feed_chunk_frames = self.batch_frames
        self._fft_size = next_fast_len(self.window_frames + self.batch_frames - 1)

        oracles, rows, preparation_peak = self._prepare_templates(classifier, cancel)
        template_count = len(rows)
        frequency_bins = self._fft_size // 2 + 1
        probe = rfft(rows[0][::-1], self._fft_size)
        real_dtype = probe.real.dtype
        kernel_row_bytes = frequency_bins * probe.dtype.itemsize
        row_bytes = sum(row.nbytes for row in rows)
        metadata_bytes = template_count * (
            2 * np.dtype(np.float64).itemsize + 2 * np.dtype(np.intp).itemsize
        )
        construction_bytes = (
            row_bytes + template_count * kernel_row_bytes + probe.nbytes
            + metadata_bytes + MATCHER_MEMORY_RESERVE_BYTES
        )
        if construction_bytes > MAX_MATCHER_BYTES:
            raise OnsetMatcherResourceLimit(
                "Onset matcher templates and construction workspace exceed 128 MiB."
            )

        template_sums = np.asarray(
            [row.sum(dtype=np.float64) for row in rows], dtype=np.float64,
        )
        template_norms = np.asarray(
            [np.sqrt(np.sum(np.square(row, dtype=np.float64))) for row in rows],
            dtype=np.float64,
        )
        kernels = np.empty((template_count, frequency_bins), dtype=probe.dtype)
        kernels[0] = probe
        del probe
        for index, row in enumerate(rows[1:], 1):
            check_cancel(cancel)
            kernels[index] = rfft(row[::-1], self._fft_size)
        check_cancel(cancel)

        # Scoring owns cumulative arrays plus a frequency product and inverse
        # FFT for each template processed at once. Keep persistent kernels and
        # the largest scoring batch within the same 128 MiB budget. The reserve
        # covers FFT workspace and small Python/NumPy allocation overhead.
        largest_input = self.window_frames + self.batch_frames - 1
        oracle_count = len(set(oracles))
        float32_bytes = np.dtype(np.float32).itemsize
        float64_bytes = np.dtype(np.float64).itemsize
        # Include feed-side temporaries in the same cap as the FFT workspace.
        # Retained-buffer slots cover this feed transaction, an enclosing
        # detector transaction, and the next-buffer copy allocated at commit.
        # Validation, mono conversion, and combined-buffer phases do not
        # overlap, so only their largest phase is counted.
        validation_bytes = (
            self._feed_chunk_frames * channels * np.dtype(np.bool_).itemsize
        )
        conversion_bytes = self._feed_chunk_frames * (
            float64_bytes + float32_bytes
        )
        combined_bytes = (
            self.lookback_frames + self._feed_chunk_frames
        ) * float32_bytes
        feed_workspace_bytes = (
            3 * self.lookback_frames * float32_bytes
            + max(
                validation_bytes,
                conversion_bytes,
                self._feed_chunk_frames * float32_bytes + combined_bytes,
            )
        )
        fixed_score_bytes = (
            MATCHER_MEMORY_RESERVE_BYTES
            + self.lookback_frames * float32_bytes
            + feed_workspace_bytes
            + (3 * largest_input + 2) * float64_bytes
            + 4 * self.batch_frames * float64_bytes
            + frequency_bins * kernels.dtype.itemsize
            + oracle_count * self.batch_frames * real_dtype.itemsize
        )
        persistent_bytes = kernels.nbytes + metadata_bytes
        per_template_score_bytes = (
            frequency_bins * kernels.dtype.itemsize
            + self._fft_size * real_dtype.itemsize
        )
        available = MAX_MATCHER_BYTES - persistent_bytes - fixed_score_bytes
        score_chunk_templates = available // per_template_score_bytes
        if score_chunk_templates < 1:
            raise OnsetMatcherResourceLimit(
                "Onset matcher templates and scoring workspace exceed 128 MiB."
            )
        self._score_chunk_templates = min(
            template_count, int(score_chunk_templates),
        )
        self._max_working_bytes = max(
            preparation_peak,
            construction_bytes,
            persistent_bytes + fixed_score_bytes
            + self._score_chunk_templates * per_template_score_bytes,
        )

        for array in (template_sums, template_norms, kernels):
            array.setflags(write=False)
        self._oracles = tuple(oracles)
        self._template_sums = template_sums
        self._template_norms = template_norms
        self._kernels = kernels
        oracle_order = tuple(oracle for oracle in OracleId if oracle in oracles)
        self._template_oracle_indices = np.asarray(
            [oracle_order.index(oracle) for oracle in oracles], dtype=np.intp,
        )
        self._template_oracle_indices.setflags(write=False)
        self._oracle_indices = tuple(
            (
                oracle,
                np.asarray(
                    [index for index, value in enumerate(oracles) if value == oracle],
                    dtype=np.intp,
                ),
            )
            for oracle in OracleId
            if oracle in oracles
        )

        self._origin_frame = start_frame
        self._buffer = np.empty(0, dtype=np.float32)
        self._buffer_start = start_frame
        self._stream_end = start_frame
        self._next_start = start_frame
        self._older: _ScoredWindow | None = None
        self._previous: _ScoredWindow | None = None
        self._last_match: int | None = None
        self._finished = False

    def _prepare_templates(
        self, classifier: OracleClassifier, cancel: Event | None,
    ) -> tuple[list[OracleId], list[np.ndarray], int]:
        oracles: list[OracleId] = []
        rows: list[np.ndarray] = []
        preparation_peak = MATCHER_MEMORY_RESERVE_BYTES

        def prepare(oracle: OracleId, clip: AudioClip) -> None:
            nonlocal preparation_peak
            check_cancel(cancel)
            source_frames = round(clip.sample_rate * TEMPLATE_SECONDS)
            if clip.frame_count < source_frames:
                logger.warning("Onset template is shorter than 400 ms: %s", oracle.value)
                return
            # Template conversion temporarily owns a native multichannel prefix,
            # mono/DC/resampling work arrays, and the normalized row while all
            # previously prepared rows remain live. This estimate is deliberately
            # conservative because scipy's polyphase workspace is implementation-
            # dependent. Source templates and manager I/O remain outside the
            # matcher's owned-workspace contract.
            prospective_rows = (
                (len(rows) + 1) * self.window_frames
                * np.dtype(np.float32).itemsize
            )
            source_values = source_frames * clip.channels
            conversion_workspace = (
                source_values * 9
                + (source_frames + self.window_frames + self.hop_frames) * 64
            )
            estimated_peak = (
                prospective_rows + conversion_workspace
                + MATCHER_MEMORY_RESERVE_BYTES
            )
            if estimated_peak > MAX_MATCHER_BYTES:
                raise OnsetMatcherResourceLimit(
                    "Onset matcher template preparation workspace exceeds 128 MiB."
                )
            preparation_peak = max(preparation_peak, estimated_peak)
            prefix = AudioClip(clip.samples[:source_frames], clip.sample_rate)
            converted = preprocess_clip(prefix, self.sample_rate, cancel)
            if converted.frame_count < self.window_frames:
                logger.warning("Converted onset template is shorter than 400 ms: %s", oracle.value)
                return
            values = converted.samples[:self.window_frames, 0].astype(np.float64)
            values -= values.mean()
            norm = float(np.linalg.norm(values))
            if norm <= SILENCE_ENERGY:
                logger.warning("Onset template is silent: %s", oracle.value)
                return
            row = np.asarray(values / norm, dtype=np.float32)
            if (
                (len(rows) + 1) * row.nbytes
                + MATCHER_MEMORY_RESERVE_BYTES > MAX_MATCHER_BYTES
            ):
                raise OnsetMatcherResourceLimit(
                    "Onset matcher templates exceed the 128 MiB budget."
                )
            oracles.append(oracle)
            rows.append(row)

        if classifier.manager is None:
            if len(classifier.templates) > MAX_TEMPLATES:
                raise OnsetMatcherResourceLimit(
                    "Onset matcher supports at most 140 templates."
                )
            for item in classifier.templates:
                prepare(OracleId(item.oracle), item.clip)
        else:
            catalog = classifier.manager.catalog(cancel)
            if len(catalog.samples) > MAX_TEMPLATES:
                raise OnsetMatcherResourceLimit(
                    "Onset matcher supports at most 140 templates."
                )
            for item in catalog.samples:
                check_cancel(cancel)
                try:
                    clip = classifier.manager.load_audio(
                        item.metadata.oracle, item.metadata.sample_id, cancel,
                        original=True,
                    )
                    prepare(OracleId(item.metadata.oracle), clip)
                except (OperationCancelled, OnsetMatcherResourceLimit):
                    raise
                except (AudioDataError, OSError, ValueError) as error:
                    logger.warning(
                        "Onset template unavailable: %s / %s: %s",
                        item.metadata.oracle, item.metadata.sample_id, error,
                    )
        if not rows:
            raise OnsetTemplatesUnavailable("400 ms以上の開始検出用テンプレートがありません。")
        return oracles, rows, preparation_peak

    @property
    def template_count(self) -> int:
        return len(self._oracles)

    @property
    def origin_frame(self) -> int:
        """Immutable native frame where this matcher stream begins."""
        return self._origin_frame

    @property
    def retained_frames(self) -> int:
        return len(self._buffer)

    @property
    def max_working_bytes(self) -> int:
        """Conservative peak workspace, excluding source audio and manager I/O."""
        return self._max_working_bytes

    @property
    def pristine(self) -> bool:
        """Whether no audio or EOF has consumed this matcher's origin."""
        return (
            not self._finished
            and not len(self._buffer)
            and self._buffer_start == self._origin_frame
            and self._stream_end == self._origin_frame
            and self._next_start == self._origin_frame
            and self._older is None
            and self._previous is None
            and self._last_match is None
        )

    def _checkpoint_stream(self) -> _MatcherCheckpoint:
        """Capture bounded state for an enclosing streaming transaction."""
        return _MatcherCheckpoint(
            self._buffer.copy(), self._buffer_start, self._stream_end,
            self._next_start, self._older, self._previous, self._last_match,
            self._finished,
        )

    def _restore_stream(self, checkpoint: _MatcherCheckpoint) -> None:
        """Restore a checkpoint after cancellation of an enclosing operation."""
        self._buffer = checkpoint.buffer
        self._buffer_start = checkpoint.buffer_start
        self._stream_end = checkpoint.stream_end
        self._next_start = checkpoint.next_start
        self._older = checkpoint.older
        self._previous = checkpoint.previous
        self._last_match = checkpoint.last_match
        self._finished = checkpoint.finished

    def _score_batch(
        self, samples: np.ndarray, start_frame: int, start_count: int,
    ) -> tuple[_ScoredWindow, ...]:
        expected = self.window_frames + start_count - 1
        if start_count < 1 or len(samples) != expected:
            raise AudioDataError("開始検出の内部窓が不正です。")
        values = samples.astype(np.float64)
        sums = np.empty(len(values) + 1, dtype=np.float64)
        sums[0] = 0.0
        np.cumsum(values, out=sums[1:])
        np.square(values, out=values)
        energies = np.empty(len(values) + 1, dtype=np.float64)
        energies[0] = 0.0
        np.cumsum(values, out=energies[1:])
        window_sums = sums[self.window_frames:] - sums[:-self.window_frames]
        centered_energy = (
            energies[self.window_frames:] - energies[:-self.window_frames]
            - window_sums * window_sums / self.window_frames
        )
        np.maximum(centered_energy, 0, out=centered_energy)
        np.sqrt(centered_energy, out=centered_energy)
        window_means = window_sums / self.window_frames
        transformed = rfft(samples, self._fft_size)
        oracle_scores = np.zeros(
            (len(self._oracle_indices), start_count), dtype=transformed.real.dtype,
        )
        for first in range(0, self.template_count, self._score_chunk_templates):
            stop = min(self.template_count, first + self._score_chunk_templates)
            frequency = self._kernels[first:stop] * transformed[None, :]
            convolution = irfft(frequency, self._fft_size, axis=1)
            del frequency
            dots = convolution[
                :, self.window_frames - 1:self.window_frames - 1 + start_count
            ]
            for local_index, template_index in enumerate(range(first, stop)):
                row = dots[local_index]
                row -= self._template_sums[template_index] * window_means
                np.abs(row, out=row)
                denominator = self._template_norms[template_index] * centered_energy
                denominator[denominator <= SILENCE_ENERGY] = np.inf
                np.divide(row, denominator, out=row)
                np.clip(row, 0, 1, out=row)
                oracle_index = self._template_oracle_indices[template_index]
                np.maximum(
                    oracle_scores[oracle_index], row,
                    out=oracle_scores[oracle_index],
                )
            # `dots` and the final `row` are views of the inverse FFT. Drop all
            # views before allocating the next template chunk's inverse FFT.
            del row, dots, denominator, convolution

        output = []
        for offset in range(0, start_count, self.hop_frames):
            stop = min(start_count, offset + self.hop_frames)
            block = oracle_scores[:, offset:stop]
            oracle_index, position = np.unravel_index(
                int(np.argmax(block)), block.shape,
            )
            position += offset
            values_at_peak = oracle_scores[:, position]
            best = float(values_at_peak[oracle_index])
            second = (
                float(np.partition(values_at_peak, -2)[-2])
                if len(values_at_peak) > 1
                else 0.0
            )
            output.append(_ScoredWindow(
                int(start_frame + position),
                self._oracle_indices[oracle_index][0],
                best,
                best - second,
            ))
        return tuple(output)

    def _peak(self, current: _ScoredWindow) -> OnsetMatch | None:
        previous, older = self._previous, self._older
        self._older, self._previous = previous, current
        if previous is None:
            return None
        older_score = -math.inf if older is None else older.score
        if not (previous.score >= older_score and previous.score > current.score):
            return None
        return self._accept(previous)

    def _accept(self, candidate: _ScoredWindow) -> OnsetMatch | None:
        if candidate.score < self.threshold or candidate.margin < self.margin_threshold:
            return None
        if (
            self._last_match is not None
            and candidate.onset_frame - self._last_match < self.refractory_frames
        ):
            return None
        self._last_match = candidate.onset_frame
        return OnsetMatch(
            candidate.onset_frame, candidate.oracle, candidate.score, candidate.margin,
        )

    def _process(
        self, combined: np.ndarray, buffer_start: int, stream_end: int,
        complete_batches_only: bool, cancel: Event | None,
    ) -> list[OnsetMatch]:
        matches = []
        while True:
            available = stream_end - self._next_start - self.window_frames + 1
            if available <= 0 or (complete_batches_only and available < self.batch_frames):
                break
            check_cancel(cancel)
            count = min(self.batch_frames, available)
            offset = self._next_start - buffer_start
            stop = offset + self.window_frames + count - 1
            for current in self._score_batch(
                combined[offset:stop], self._next_start, count,
            ):
                match = self._peak(current)
                if match is not None:
                    matches.append(match)
            self._next_start += count
            check_cancel(cancel)
        return matches

    def finish(self, cancel: Event | None = None) -> tuple[OnsetMatch, ...]:
        """Process the short EOF tail and resolve its pending local peak once."""
        check_cancel(cancel)
        if self._finished:
            return ()
        saved = (self._next_start, self._older, self._previous, self._last_match)
        try:
            matches = self._process(
                self._buffer, self._buffer_start, self._stream_end, False, cancel,
            )
            previous, older = self._previous, self._older
            if previous is not None:
                older_score = -math.inf if older is None else older.score
                if previous.score >= older_score:
                    match = self._accept(previous)
                    if match is not None:
                        matches.append(match)
            check_cancel(cancel)
        except OperationCancelled:
            self._next_start, self._older, self._previous, self._last_match = saved
            raise
        self._previous = None
        self._buffer = np.empty(0, dtype=np.float32)
        self._buffer_start = self._stream_end
        self._next_start = self._stream_end
        self._finished = True
        return tuple(matches)

    def feed(self, samples, cancel: Event | None = None) -> tuple[OnsetMatch, ...]:
        check_cancel(cancel)
        if self._finished:
            raise AudioDataError("終了した開始検出ストリームへ音声を追加できません。")
        invalid_input = (
            "\u958b\u59cb\u691c\u51fa\u30b9\u30c8\u30ea\u30fc\u30e0\u306b\u4e0d\u6b63\u306a\u5024"
            "\u307e\u305f\u306f\u5f62\u5f0f\u304c\u3042\u308a\u307e\u3059\u3002"
        )
        too_long = (
            "\u958b\u59cb\u691c\u51fa\u3078\u4e00\u5ea6\u306b\u6e21\u3059\u97f3\u58f0\u306f"
            "30\u79d2\u4ee5\u5185\u306b\u3057\u3066\u304f\u3060\u3055\u3044\u3002"
        )
        try:
            supplied_frames = len(samples)
        except (TypeError, ValueError, OverflowError) as error:
            raise AudioDataError(invalid_input) from error
        if supplied_frames > self.max_feed_frames:
            raise AudioDataError(too_long)
        try:
            source = np.asarray(samples)
        except (TypeError, ValueError, OverflowError) as error:
            raise AudioDataError(invalid_input) from error
        if (
            source.ndim != 2
            or source.shape[1] != self.channels
            or source.dtype.kind not in "fiu"
        ):
            raise AudioDataError(invalid_input)
        # Recheck an array protocol implementation that reports an inconsistent
        # length, while the cheap preflight protects honest large inputs.
        if len(source) > self.max_feed_frames:
            raise AudioDataError(too_long)
        if len(source) == 0:
            return ()

        # Validate bounded slices before changing stream state. This preserves
        # the former all-or-nothing behavior when a late sample is invalid.
        for first in range(0, len(source), self._feed_chunk_frames):
            check_cancel(cancel)
            part = source[first:first + self._feed_chunk_frames]
            if not np.isfinite(part).all():
                raise AudioDataError(invalid_input)
        check_cancel(cancel)

        # Internal chunks replace, rather than mutate, the retained buffer. A
        # reference checkpoint is therefore sufficient for atomic rollback and
        # avoids duplicating a potentially large buffer inside public feed().
        checkpoint = _MatcherCheckpoint(
            self._buffer, self._buffer_start, self._stream_end,
            self._next_start, self._older, self._previous, self._last_match,
            self._finished,
        )
        matches = []
        try:
            for first in range(0, len(source), self._feed_chunk_frames):
                check_cancel(cancel)
                part = source[first:first + self._feed_chunk_frames]
                with np.errstate(over="ignore", invalid="ignore"):
                    mean = part.mean(axis=1, dtype=np.float64)
                    mono = mean.astype(np.float32)
                del mean
                if not np.isfinite(mono).all():
                    raise AudioDataError("\u958b\u59cb\u691c\u51fa\u30b9\u30c8\u30ea\u30fc\u30e0\u306e\u5024\u304c\u5927\u304d\u3059\u304e\u307e\u3059\u3002")

                combined = np.concatenate((self._buffer, mono))
                buffer_start = self._buffer_start
                stream_end = self._stream_end + len(part)
                matches.extend(self._process(
                    combined, buffer_start, stream_end, True, cancel,
                ))
                discard = self._next_start - buffer_start
                self._buffer = combined[discard:].copy()
                self._buffer_start = self._next_start
                self._stream_end = stream_end
                del mono, combined
                check_cancel(cancel)
        except Exception:
            self._restore_stream(checkpoint)
            raise
        return tuple(matches)



def build_optional_onset_matcher(
    classifier: OracleClassifier,
    sample_rate: int,
    channels: int,
    *,
    start_frame: int = 0,
    cancel: Event | None = None,
) -> TemplateOnsetMatcher | None:
    """Create the auxiliary matcher only when a usable template source exists."""
    check_cancel(cancel)
    manager = getattr(classifier, "manager", None)
    templates = getattr(classifier, "templates", ())
    if manager is None and not templates:
        return None
    try:
        return TemplateOnsetMatcher(
            classifier, sample_rate, channels, start_frame=start_frame, cancel=cancel,
        )
    except OnsetTemplatesUnavailable as error:
        logger.info("Template onset matching unavailable; using adaptive RMS only: %s", error)
        return None
    except OnsetMatcherResourceLimit as error:
        logger.warning(
            "Template onset matching resource limit; using adaptive RMS only: %s",
            error,
        )
        return None
