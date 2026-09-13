"""Ordinary recording inputs, including VoiceMeeter virtual outputs."""

from typing import Any

from audio.backend import AudioBackend, AudioDevice, device_from_info


class InputDeviceBackend(AudioBackend):
    key = "input_device"

    def enumerate(self, interface: Any) -> list[AudioDevice]:
        devices = []
        for index in range(interface.get_device_count()):
            info = interface.get_device_info_by_index(index)
            if int(info["maxInputChannels"]) > 0 and not info.get("isLoopbackDevice", False):
                devices.append(device_from_info(interface, info))
        return devices
