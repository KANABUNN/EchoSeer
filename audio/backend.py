"""Backend-neutral audio device and stream contracts."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from config.schema import DeviceIdentity


class AudioError(RuntimeError):
    """A recoverable audio-device or stream failure."""


class DeviceNotFoundError(AudioError):
    """The selected identity is absent from the current catalog."""


class AmbiguousDeviceError(AudioError):
    """More than one device matches the saved identity."""


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int
    host_api: str
    name: str
    loopback: bool
    sample_rate: int
    channels: int

    @property
    def backend(self) -> str:
        return "wasapi_loopback" if self.loopback else "input_device"

    def identity(self) -> DeviceIdentity:
        return DeviceIdentity(
            host_api=self.host_api, device_name=self.name, loopback=self.loopback,
            channels=self.channels, native_sample_rate=self.sample_rate,
        )


class AudioBackend(ABC):
    key: str

    @abstractmethod
    def enumerate(self, interface: Any) -> list[AudioDevice]:
        """Enumerate compatible devices on the owning audio thread."""

    def open_stream(
        self, interface: Any, module: Any, device: AudioDevice,
        callback: Callable[..., tuple[None, int]], frames_per_buffer: int,
    ) -> Any:
        if device.backend != self.key:
            raise AudioError("音声デバイスと入力方式が一致していません。")
        return interface.open(
            format=module.paFloat32, channels=device.channels, rate=device.sample_rate,
            input=True, input_device_index=device.index,
            frames_per_buffer=frames_per_buffer, stream_callback=callback, start=False,
        )


def device_from_info(interface: Any, info: dict[str, Any]) -> AudioDevice:
    api = interface.get_host_api_info_by_index(int(info["hostApi"]))
    rate = int(round(float(info["defaultSampleRate"])))
    channels = int(info["maxInputChannels"])
    if not 8000 <= rate <= 384000 or not 1 <= channels <= 64:
        raise AudioError("このデバイスの音声形式には対応していません。")
    return AudioDevice(
        int(info["index"]), str(api["name"]), str(info["name"]),
        bool(info.get("isLoopbackDevice", False)), rate, channels,
    )
