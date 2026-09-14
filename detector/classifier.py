"""Basic waveform-only Oracle ranking, independent of Qt and sequence state."""
from dataclasses import dataclass
import logging
from threading import Event

import numpy as np
from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled, check_cancel
from dsp.bandpass import BandpassSettings, bandpass_audio
from dsp.correlation import CorrelationMatch, normalized_correlation
from dsp.normalization import normalize_audio
from dsp.preprocess import preprocess_clip
from encounter.vog_oracles import OracleId
from templates.manager import TemplateManager

logger = logging.getLogger("oracle_assistant.classifier")
MAX_BANK_BYTES = 128 * 1024 * 1024
MIN_EVENT_SECONDS = 0.02
MAX_EVENT_SECONDS = 10.0
TIE_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class TemplateWaveform:
    oracle: OracleId
    sample_id: str
    clip: AudioClip


@dataclass(frozen=True, slots=True)
class SampleScore:
    sample_id: str
    match: CorrelationMatch


@dataclass(frozen=True, slots=True)
class OracleScore:
    oracle: OracleId
    score: float
    samples: tuple[SampleScore, ...]


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    status: str
    ranking: tuple[OracleScore, ...] = ()
    notices: tuple[str, ...] = ()
    aggregation: str = "best"
    top_n: int = 3

    @property
    def best_candidate(self) -> OracleId | None:
        return self.ranking[0].oracle if self.ranking else None

    @property
    def oracle(self) -> OracleId | None:
        return self.best_candidate if self.status == "RANKED" else None

    @property
    def best_score(self) -> float | None:
        return self.ranking[0].score if self.ranking else None

    @property
    def second_candidate(self) -> OracleId | None:
        return self.ranking[1].oracle if len(self.ranking) > 1 else None

    @property
    def second_score(self) -> float | None:
        return self.ranking[1].score if len(self.ranking) > 1 else None

    @property
    def margin(self) -> float | None:
        return self.best_score - self.second_score if len(self.ranking) > 1 else None

    @property
    def missing_oracles(self) -> tuple[OracleId, ...]:
        available = {item.oracle for item in self.ranking}
        return tuple(oracle for oracle in OracleId if oracle not in available)

    @property
    def sample_count(self) -> int:
        return sum(len(item.samples) for item in self.ranking)


class OracleClassifier:
    def __init__(self, manager: TemplateManager | None = None, sample_rate: int = 48000,
                 aggregation: str = "best", top_n: int = 3,
                 bandpass: BandpassSettings | None = None,
                 templates: tuple[TemplateWaveform, ...] | None = None) -> None:
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
            raise ValueError("Invalid internal sample rate")
        if aggregation not in ("best", "top_n_mean") or type(top_n) is not int or not 1 <= top_n <= 20:
            raise ValueError("Invalid template aggregation")
        if manager is not None and templates is not None:
            raise ValueError("Choose a stored or in-memory template source")
        if bandpass is not None:
            bandpass.validate(sample_rate)
        self.manager, self.sample_rate = manager, sample_rate
        self.aggregation, self.top_n, self.bandpass = aggregation, top_n, bandpass
        self.templates = tuple(templates or ())

    def _result(self, status: str, ranking=(), notices=()) -> ClassificationResult:
        return ClassificationResult(status, tuple(ranking), tuple(notices), self.aggregation, self.top_n)

    def _event_limit(self, clip: AudioClip) -> ClassificationResult | None:
        if clip.duration_seconds < MIN_EVENT_SECONDS:
            return self._result("TOO_SHORT", notices=("比較する音声は 20 ms 以上にしてください。",))
        if clip.duration_seconds > MAX_EVENT_SECONDS:
            return self._result("TOO_LONG", notices=("1 つの Oracle を含む 10 秒以内の区間を比較してください。",))
        return None

    def _filter(self, clip: AudioClip, cancel: Event | None) -> AudioClip:
        check_cancel(cancel)
        if self.bandpass is None:
            return clip
        values = bandpass_audio(clip.samples[:, 0], self.sample_rate, self.bandpass)
        check_cancel(cancel)
        return AudioClip(normalize_audio(values), self.sample_rate)

    def classify(self, event: AudioClip, cancel: Event | None = None) -> ClassificationResult:
        check_cancel(cancel)
        limited = self._event_limit(event)
        if limited:
            return limited
        processed = preprocess_clip(event, self.sample_rate, cancel)
        return self.classify_preprocessed(processed, cancel)

    def classify_preprocessed(self, event: AudioClip, cancel: Event | None = None) -> ClassificationResult:
        check_cancel(cancel)
        limited = self._event_limit(event)
        if limited:
            return limited
        if event.sample_rate != self.sample_rate or event.channels != 1:
            event = preprocess_clip(event, self.sample_rate, cancel)
        event = self._filter(event, cancel)
        if np.max(np.abs(event.samples)) <= 1e-6:
            return self._result("SILENT", notices=("比較音声は無音です。Oracle 候補を出しません。",))
        notices, prepared, bank_bytes = [], [], 0
        if self.manager is not None:
            catalog = self.manager.catalog(cancel)
            notices.extend(catalog.issues)
            inputs = catalog.samples
        else:
            inputs = self.templates
        # Refresh the stored catalog for every event; deleted/added samples cannot remain cached.
        for item in inputs:
            check_cancel(cancel)
            oracle = OracleId(item.metadata.oracle) if self.manager is not None else OracleId(item.oracle)
            sample_id = item.metadata.sample_id if self.manager is not None else item.sample_id
            try:
                clip = (self.manager.load_audio(oracle, sample_id, cancel, original=True)
                        if self.manager is not None else item.clip)
                if not MIN_EVENT_SECONDS <= clip.duration_seconds <= MAX_EVENT_SECONDS:
                    raise AudioDataError("比較用サンプルは 20 ms〜10 秒にしてください。")
                waveform = self._filter(preprocess_clip(clip, self.sample_rate, cancel), cancel)
                if np.max(np.abs(waveform.samples)) <= 1e-6:
                    raise AudioDataError("比較用サンプルが前処理後に無音です。")
                bank_bytes += waveform.samples.nbytes
                if bank_bytes > MAX_BANK_BYTES:
                    return self._result("LIMIT", notices=(*notices, "比較対象の mono 音声が合計 128 MiB を超えます。サンプル数を減らしてください。"))
                prepared.append(TemplateWaveform(oracle, sample_id, waveform))
            except OperationCancelled:
                raise
            except (AudioDataError, OSError, ValueError) as error:
                logger.warning("Template unavailable during classification: %s / %s: %s", oracle.value, sample_id, error)
                notices.append(f"{oracle.value} / {sample_id[:8]}：{error}")
        if not prepared:
            return self._result("NO_TEMPLATES", notices=(*notices, "Calibration で Oracle ごとのサンプルを登録してください。"))
        scores: dict[OracleId, list[SampleScore]] = {}
        for item in prepared:
            check_cancel(cancel)
            match = normalized_correlation(event.samples[:, 0], item.clip.samples[:, 0], cancel)
            scores.setdefault(item.oracle, []).append(SampleScore(item.sample_id, match))
        ranking = []
        for oracle, samples in scores.items():
            samples.sort(key=lambda s: (-s.match.score, s.sample_id))
            selected = samples[:1] if self.aggregation == "best" else samples[:self.top_n]
            score = float(np.mean([s.match.score for s in selected]))
            ranking.append(OracleScore(oracle, score, tuple(samples)))
        canonical = {oracle: index for index, oracle in enumerate(OracleId)}
        ranking.sort(key=lambda s: (-s.score, canonical[s.oracle]))
        if ranking[0].score == 0:
            status = "NO_MATCH"
        elif len(ranking) > 1 and ranking[0].score - ranking[1].score <= TIE_EPSILON:
            status = "TIED"
        else:
            status = "RANKED"
        check_cancel(cancel)
        return self._result(status, ranking, notices)
