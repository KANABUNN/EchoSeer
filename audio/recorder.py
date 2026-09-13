"""Accumulate only post-request input frames, outside the PortAudio callback."""
from dataclasses import dataclass
import math

import numpy as np
from audio.capture import AudioBlock
from audio.data import AudioClip, AudioDataError


@dataclass(frozen=True, slots=True)
class RecordingRequest:
    seconds: float
    token: str


@dataclass(frozen=True, slots=True)
class RecordingEvent:
    state: str
    token: str
    progress: float = 0.0
    message: str = ""
    clip: AudioClip | None = None


class SampleRecorder:
    def __init__(self, sample_rate: int, channels: int, seconds: float, started: float) -> None:
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0.1 <= seconds <= 10:
            raise AudioDataError("録音時間は 0.1〜10 秒にしてください。")
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 384000:
            raise AudioDataError("録音レートが不正です。")
        if type(channels) is not int or not 1 <= channels <= 64:
            raise AudioDataError("録音チャンネル数が不正です。")
        self.sample_rate, self.channels = sample_rate, channels
        self.frames = math.ceil(seconds * sample_rate)
        if self.frames * channels * 4 > 64 * 1024 * 1024:
            raise AudioDataError("録音サイズが大きすぎます。時間を短くしてください。")
        self.started = started
        self.deadline = started + seconds + 2
        self._values = np.empty((self.frames, channels), dtype=np.float32)
        self.count = 0

    @property
    def progress(self) -> float:
        return self.count / self.frames

    def append(self, block: AudioBlock) -> bool:
        if block.samples.ndim != 2 or block.samples.shape[1] != self.channels:
            raise AudioDataError("録音中に音声形式が変わりました。")
        offset = max(0, math.ceil((self.started - block.timestamp) * self.sample_rate))
        values = block.samples[offset:]
        take = min(len(values), self.frames - self.count)
        self._values[self.count:self.count + take] = values[:take]
        self.count += take
        return self.count == self.frames

    def clip(self) -> AudioClip:
        if self.count != self.frames:
            raise AudioDataError("録音は完了していません。")
        return AudioClip(self._values, self.sample_rate)
