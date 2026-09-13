"""WASAPI rendering endpoints exposed as loopback inputs."""

from typing import Any

from audio.backend import AudioBackend, AudioDevice, device_from_info


class WasapiLoopbackBackend(AudioBackend):
    key = "wasapi_loopback"

    def enumerate(self, interface: Any) -> list[AudioDevice]:
        return [
            device_from_info(interface, info)
            for info in interface.get_loopback_device_info_generator()
            if info.get("isLoopbackDevice") and int(info["maxInputChannels"]) > 0
        ]
