"""Short-sample playback through the current Windows default output."""
from threading import Event
import time
from typing import Any

import numpy as np
from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel
from audio.resampler import resample_audio


class AudioPlayer:
    def __init__(self, module: Any = None, interface_factory: Any = None) -> None:
        self._module = module
        self._factory = interface_factory

    def play(self, clip: AudioClip, volume: float = 0.2, stop: Event | None = None) -> None:
        if not 0 <= volume <= 1 or not np.isfinite(volume):
            raise AudioDataError("再生音量が不正です。")
        if not 0 < clip.duration_seconds <= 10.001:
            raise AudioDataError("再生するサンプルの長さが不正です。")
        check_cancel(stop)
        module = self._module
        if module is None:
            import pyaudiowpatch as module
        interface = self._factory() if self._factory is not None else module.PyAudio()
        stream = None
        try:
            try:
                info = interface.get_host_api_info_by_type(module.paWASAPI)
                index = int(info["defaultOutputDevice"])
                if index < 0:
                    raise OSError("WASAPI の既定出力がありません。")
                device = interface.get_device_info_by_index(index)
            except (KeyError, AttributeError):
                device = interface.get_default_output_device_info()
            if device.get("isLoopbackDevice", False):
                raise AudioDataError("再生用の出力デバイスが見つかりません。")
            rate, channels = round(device["defaultSampleRate"]), int(device["maxOutputChannels"])
            if not 1 <= channels <= 64:
                raise AudioDataError("再生用の出力デバイスが見つかりません。")
            mono = clip.samples.mean(axis=1, dtype=np.float64).astype(np.float32)
            values = resample_audio(mono, clip.sample_rate, rate)
            values = np.repeat(values[:, None], channels, axis=1)
            values *= volume
            check_cancel(stop)
            cursor = 0

            def callback(in_data: bytes | None, frame_count: int, time_info: dict, flags: int):
                nonlocal cursor
                if stop is not None and stop.is_set():
                    return bytes(frame_count * channels * 4), module.paComplete
                end = min(cursor + frame_count, len(values))
                data = values[cursor:end].tobytes()
                cursor = end
                data += bytes((frame_count - len(data) // (channels * 4)) * channels * 4)
                return data, module.paComplete if cursor == len(values) else module.paContinue

            stream = interface.open(
                format=module.paFloat32, channels=channels, rate=rate,
                output=True, output_device_index=int(device["index"]),
                frames_per_buffer=max(1, round(rate * 0.02)), stream_callback=callback, start=False,
            )
            stream.start_stream()
            deadline = time.monotonic() + len(values) / rate + 3
            while stream.is_active():
                check_cancel(stop)
                if time.monotonic() > deadline:
                    raise AudioDataError("再生が応答しません。出力先を確認してください。")
                time.sleep(0.01)
            check_cancel(stop)
            if cursor != len(values):
                raise AudioDataError("再生が途中で停止しました。出力デバイスを確認してください。")
        finally:
            try:
                if stream is not None:
                    try:
                        if stream.is_active():
                            stream.stop_stream()
                    finally:
                        stream.close()
            finally:
                interface.terminate()
