"""Main window with live audio controls and asynchronous clean shutdown."""

import logging
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QLabel, QMainWindow, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from audio.backend import AudioDevice
from audio.device_manager import DeviceCatalog
from audio.level import AudioLevel
from audio.service import CaptureStatus
from config.manager import ConfigManager
from config.schema import AppConfig, ConfigValidationError
from ui.audio_controller import AudioController
from ui.live_page import LivePage
from ui.theme import DARK_STYLE

logger = logging.getLogger("oracle_assistant.ui")


class MainWindow(QMainWindow):
    def __init__(
        self, data_root: Path, warning: str | None = None,
        settings: AppConfig | None = None, config_manager: ConfigManager | None = None,
        controller: AudioController | None = None,
    ) -> None:
        super().__init__()
        self.data_root = data_root
        self.settings = settings or AppConfig()
        self.config_manager = config_manager or ConfigManager(data_root / "config.json")
        self._closing = False
        self.setWindowTitle("Destiny 2 · Oracle Assistant")
        self.resize(1024, 760)
        self.setMinimumSize(760, 600)
        self.setStyleSheet(DARK_STYLE)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel("Oracle Assistant")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Destiny 2 · Vault of Glass")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        self.warning_label = QLabel(warning or "")
        self.warning_label.setObjectName("warning")
        self.warning_label.setWordWrap(True)
        self.warning_label.setAccessibleName("設定または保存先の警告")
        self.warning_label.setVisible(bool(warning))
        layout.addWidget(self.warning_label)
        self.live_page = LivePage(self.settings.audio)
        self.tabs = QTabWidget()
        self.live_scroll = QScrollArea()
        self.live_scroll.setWidgetResizable(True)
        self.live_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.live_scroll.setWidget(self.live_page)
        self.tabs.addTab(self.live_scroll, "Live")
        layout.addWidget(self.tabs, stretch=1)
        self.setCentralWidget(central)
        exit_action = QAction("終了", self)
        exit_action.triggered.connect(self.close)
        self.menuBar().addMenu("ファイル").addAction(exit_action)
        self.statusBar().showMessage("STOPPED")

        self.controller = controller or AudioController(
            self.settings.audio.buffer_duration, parent=self,
        )
        self.controller.event_received.connect(self._on_audio_event, Qt.ConnectionType.QueuedConnection)
        self.live_page.selection_changed.connect(self._save_selection)
        self.live_page.backend_changed.connect(self._save_backend)
        self.live_page.start_requested.connect(self._start_capture)
        self.live_page.stop_requested.connect(self._stop_capture)
        self.live_page.refresh_requested.connect(self._refresh_devices)
        self._close_timer = QTimer(self)
        self._close_timer.setInterval(50)
        self._close_timer.timeout.connect(self.close)
        self.live_page.set_status(CaptureStatus("DISCOVERING", "音声デバイスを確認しています。"))
        self.controller.start_worker()

    def _save_config(self) -> None:
        try:
            self.config_manager.save(self.settings)
        except (OSError, ConfigValidationError):
            logger.exception("Could not save audio selection")
            self.warning_label.setText("音声デバイスの設定を保存できません。保存先を確認してください。")
            self.warning_label.show()

    @Slot(object)
    def _save_selection(self, device: AudioDevice | None) -> None:
        self.settings.audio.device = device.identity() if device is not None else None
        self._save_config()

    @Slot(str)
    def _save_backend(self, backend: str) -> None:
        self.settings.audio.backend = backend
        self.settings.audio.device = None
        self._save_config()

    @Slot(object)
    def _start_capture(self, device: AudioDevice) -> None:
        self._save_selection(device)
        self.live_page.set_status(CaptureStatus("STARTING", "音声入力を開始しています。", device))
        self.controller.start(device)

    @Slot()
    def _stop_capture(self) -> None:
        self.live_page.set_status(CaptureStatus("STOPPING", "音声取得を停止しています。"))
        self.controller.stop()

    @Slot()
    def _refresh_devices(self) -> None:
        self.live_page.set_status(CaptureStatus("DISCOVERING", "音声デバイスを確認しています。"))
        self.controller.refresh()

    @Slot(object)
    def _on_audio_event(self, event: object) -> None:
        if self._closing:
            return
        if isinstance(event, DeviceCatalog):
            self.live_page.set_catalog(event)
        elif isinstance(event, CaptureStatus):
            self.live_page.set_status(event)
            self.statusBar().showMessage(event.state)
            if event.state == "LIVE" and event.device is not None:
                self.settings.audio.backend = event.device.backend
                self._save_selection(event.device)
        elif isinstance(event, AudioLevel):
            self.live_page.set_level(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        if self.controller.shutdown(timeout=0):
            self._close_timer.stop()
            event.accept()
        else:
            self.live_page.set_status(CaptureStatus("STOPPING", "音声取得を終了しています。"))
            self._close_timer.start()
            event.ignore()
