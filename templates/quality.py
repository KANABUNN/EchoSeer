"""Recording/import quality notices before templates are committed."""
from audio.data import AudioClip, AudioDataError
from replay.analyzer import AnalysisResult

MAX_SAMPLE_SECONDS = 10.0


def validate_length(clip: AudioClip) -> None:
    if not 0 < clip.duration_seconds <= MAX_SAMPLE_SECONDS:
        raise AudioDataError("サンプルは 0 秒より長く、10 秒以内の WAV にしてください。")


def quality_notices(result: AnalysisResult) -> tuple[str, ...]:
    if result.processed_level.peak == 0:
        raise AudioDataError("無音のサンプルは登録できません。入力先・音量・左右の位相を確認してください。")
    notices = list(result.notices)
    if result.original_level.peak >= 0.999 and not result.original_level.clipping:
        notices.append("入力が最大振幅に達しています。クリッピングの可能性があります。")
    if result.original.duration_seconds < 0.1:
        notices.append("サンプルが短すぎる可能性があります。Oracle の音全体を収録してください。")
    if result.original.duration_seconds > 5:
        notices.append("サンプルが長めです。周囲の音や無音を減らすことを推奨します。")
    if result.original_level.rms < 0.0001:
        notices.append("入力音量が小さすぎる可能性があります。録音音量を確認してください。")
    return tuple(notices)
