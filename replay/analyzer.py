"""Shared audio foundation; Oracle classification is added in later phases."""

from dataclasses import dataclass
from hashlib import sha256
from threading import Event

from audio.data import AudioClip, AudioDataError
from audio.level import AudioLevel, measure_level
from audio.operations import check_cancel
from audio.sources import AudioSource
from dsp.preprocess import preprocess_clip

MAX_PROCESSED_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    original: AudioClip
    processed: AudioClip
    original_level: AudioLevel
    processed_level: AudioLevel
    checksum: str
    notices: tuple[str, ...]


class Analyzer:
    def __init__(self, sample_rate: int = 48000) -> None:
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
            raise ValueError("Invalid internal sample rate")
        self.sample_rate = sample_rate

    def analyze(self, source: AudioSource, cancel: Event | None = None) -> AnalysisResult:
        original = source.read(cancel)
        if original.frame_count == 0:
            raise AudioDataError("音声がありません。音を取り込んでから再試行してください。")
        output_frames = (
            original.frame_count * self.sample_rate + original.sample_rate - 1
        ) // original.sample_rate
        if output_frames * 4 > MAX_PROCESSED_BYTES:
            raise AudioDataError("解析後の音声が 256 MiB を超えます。短い区間に分割してください。")
        check_cancel(cancel)
        original_level = measure_level(original.samples)
        processed = preprocess_clip(original, self.sample_rate, cancel)
        processed_level = measure_level(processed.samples)
        check_cancel(cancel)
        checksum = sha256(processed.samples.astype("<f4", copy=False).tobytes()).hexdigest()
        notices = []
        if original_level.clipping:
            notices.append("入力にクリッピングがあります。音量を下げて再取得することを推奨します。")
        if processed_level.peak == 0:
            notices.append("解析後の音声は無音です。出力先・入力バス・左右の位相を確認してください。")
        return AnalysisResult(
            original, processed, original_level, processed_level, checksum, tuple(notices),
        )
