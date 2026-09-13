"""Device catalogs and stable identity resolution; indices are session-only."""

from dataclasses import dataclass
import importlib
from typing import Any, Callable

from audio.backend import (
    AmbiguousDeviceError, AudioBackend, AudioDevice, DeviceNotFoundError,
)
from audio.input_device import InputDeviceBackend
from audio.wasapi import WasapiLoopbackBackend
from config.schema import DeviceIdentity


@dataclass(frozen=True, slots=True)
class DeviceCatalog:
    devices: tuple[AudioDevice, ...]
    default_loopback_index: int | None = None
    default_input_index: int | None = None

    def for_backend(self, backend: str) -> tuple[AudioDevice, ...]:
        return tuple(device for device in self.devices if device.backend == backend)

    def default_for(self, backend: str) -> AudioDevice | None:
        index = self.default_loopback_index if backend == "wasapi_loopback" else self.default_input_index
        return next((device for device in self.for_backend(backend) if device.index == index), None)


def resolve_device(
    devices: tuple[AudioDevice, ...] | list[AudioDevice], identity: DeviceIdentity,
) -> AudioDevice:
    matches = [
        device for device in devices
        if device.host_api == identity.host_api and device.name == identity.device_name
        and device.loopback == identity.loopback
    ]
    if not matches:
        raise DeviceNotFoundError("保存された音声デバイスが見つかりません。再選択してください。")
    if len(matches) > 1:
        for field, value in (
            ("channels", identity.channels), ("sample_rate", identity.native_sample_rate),
        ):
            if value is not None:
                matches = [device for device in matches if getattr(device, field) == value]
        if len(matches) != 1:
            raise AmbiguousDeviceError("同名のデバイスを区別できません。手動で再選択してください。")
    return matches[0]


class DeviceManager:
    def __init__(
        self, module: Any = None, interface_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._module = module
        self._factory = interface_factory
        self._backends: dict[str, AudioBackend] = {
            backend.key: backend for backend in (WasapiLoopbackBackend(), InputDeviceBackend())
        }

    @property
    def module(self) -> Any:
        if self._module is None:
            self._module = importlib.import_module("pyaudiowpatch")
        return self._module

    def create_interface(self) -> Any:
        return (self._factory or self.module.PyAudio)()

    def backend(self, key: str) -> AudioBackend:
        return self._backends[key]

    def enumerate(self, interface: Any) -> DeviceCatalog:
        devices = tuple(
            device for backend in self._backends.values() for device in backend.enumerate(interface)
        )
        try:
            output_index = int(interface.get_default_wasapi_loopback()["index"])
        except (OSError, LookupError, ValueError):
            output_index = None
        try:
            info = interface.get_host_api_info_by_type(self.module.paWASAPI)
            input_index = int(info["defaultInputDevice"])
            if input_index < 0:
                raise OSError("No WASAPI default input")
        except (OSError, LookupError, ValueError):
            try:
                input_index = int(interface.get_default_input_device_info()["index"])
            except (OSError, LookupError, ValueError):
                input_index = None
        return DeviceCatalog(devices, output_index, input_index)
