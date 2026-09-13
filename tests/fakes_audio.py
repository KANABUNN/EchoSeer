"""Deterministic PortAudio double with real callback threads."""

from threading import Event, Thread, current_thread
from typing import Any

import numpy as np


class FakeModule:
    paFloat32 = 1
    paContinue = 0
    paComplete = 1
    paAbort = 2
    paInputOverflow = 2
    paWASAPI = 19


class FakeStream:
    def __init__(self, factory: "AudioFactory", options: dict[str, Any]) -> None:
        self.factory, self.options = factory, options
        self.closed = False
        self.active = False
        self._stop = Event()
        self.thread: Thread | None = None

    def start_stream(self) -> None:
        self.active = True
        self.thread = Thread(target=self._run, name="FakeAudioCallback", daemon=False)
        self.thread.start()

    def _run(self) -> None:
        frames, channels = self.options["frames_per_buffer"], self.options["channels"]
        samples = np.full((frames, channels), 0.2, dtype=np.float32).tobytes()
        while not self._stop.is_set():
            if not self.factory.no_data:
                _, status = self.options["stream_callback"](samples, frames, {}, 0)
                if status != FakeModule.paContinue:
                    break
            self._stop.wait(0.01)
        self.active = False

    def is_active(self) -> bool:
        return self.active and not self.closed

    def lose(self) -> None:
        self.active = False
        self._stop.set()

    def stop_stream(self) -> None:
        self._stop.set()
        if self.thread is not None:
            self.thread.join(1)
            assert not self.thread.is_alive()
        self.active = False

    def close(self) -> None:
        self.stop_stream()
        self.closed = True


class FakeInterface:
    def __init__(self, factory: "AudioFactory") -> None:
        self.factory = factory
        self.created_thread = current_thread().name
        self.terminated = False
        self.streams: list[FakeStream] = []
        self.devices = {
            0: self._info(0, "Speakers", 0, 2, factory.rate),
            1: self._info(1, "VoiceMeeter Output", 2, 0, 44100),
            factory.index: self._info(factory.index, "Speakers [Loopback]", 2, 0, factory.rate, True),
        }

    @staticmethod
    def _info(index: int, name: str, inputs: int, outputs: int, rate: int, loopback: bool = False) -> dict[str, Any]:
        return {
            "index": index, "name": name, "hostApi": 0, "maxInputChannels": inputs,
            "maxOutputChannels": outputs, "defaultSampleRate": float(rate), "isLoopbackDevice": loopback,
        }

    def get_host_api_info_by_index(self, index: int) -> dict[str, Any]:
        return {"name": "Windows WASAPI", "defaultInputDevice": 1}

    def get_host_api_info_by_type(self, api_type: int) -> dict[str, Any]:
        return self.get_host_api_info_by_index(0)

    def get_device_count(self) -> int:
        return max(self.devices) + 1

    def get_device_info_by_index(self, index: int) -> dict[str, Any]:
        return self.devices.get(index, self._info(index, "unused", 0, 0, 0))

    def get_loopback_device_info_generator(self):
        return (info for info in self.devices.values() if info["isLoopbackDevice"])

    def get_default_wasapi_loopback(self) -> dict[str, Any]:
        return next(self.get_loopback_device_info_generator())

    def get_default_input_device_info(self) -> dict[str, Any]:
        return self.devices[1]

    def open(self, **options: Any) -> FakeStream:
        self.factory.open_entered.set()
        if self.factory.open_gate is not None:
            if not self.factory.open_gate.wait(2):
                raise OSError("Simulated open timeout")
        if self.factory.fail_open:
            raise OSError("Device unavailable")
        stream = FakeStream(self.factory, options)
        self.streams.append(stream)
        self.factory.streams.append(stream)
        return stream

    def terminate(self) -> None:
        for stream in self.streams:
            stream.close()
        self.terminated = True


class AudioFactory:
    def __init__(self) -> None:
        self.index = 2
        self.rate = 48000
        self.fail_open = False
        self.no_data = False
        self.open_gate: Event | None = None
        self.open_entered = Event()
        self.interfaces: list[FakeInterface] = []
        self.streams: list[FakeStream] = []

    def __call__(self) -> FakeInterface:
        interface = FakeInterface(self)
        self.interfaces.append(interface)
        return interface
