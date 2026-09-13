"""Qt signal bridge; the audio service has no dependency on Qt."""

from PySide6.QtCore import QObject, Signal, Slot

from audio.backend import AudioDevice
from audio.device_manager import DeviceManager
from audio.service import CaptureService


class AudioController(QObject):
    event_received = Signal(object)

    def __init__(
        self, buffer_duration: float = 10, manager: DeviceManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = CaptureService(self.event_received.emit, buffer_duration, manager)

    def start_worker(self) -> None:
        self.service.start_worker()

    @Slot(object)
    def start(self, device: AudioDevice) -> None:
        self.service.start(device)

    @Slot()
    def stop(self) -> None:
        self.service.stop()

    @Slot()
    def refresh(self) -> None:
        self.service.refresh()

    def shutdown(self, timeout: float = 0) -> bool:
        return self.service.shutdown(timeout)
