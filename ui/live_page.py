"""Live audio controls and metering, updated only on the Qt main thread."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from audio.backend import AudioDevice, AudioError
from audio.device_manager import DeviceCatalog, resolve_device
from audio.level import AudioLevel
from audio.service import CaptureStatus
from config.schema import AudioSettings

ACTIVE_STATES = {"STARTING", "LIVE", "DEVICE_LOST", "RECONNECTING"}


class LivePage(QWidget):
    replay_requested = Signal()
    dump_requested = Signal()
    sequence_requested = Signal()
    start_requested = Signal(object)
    stop_requested = Signal()
    refresh_requested = Signal()
    selection_changed = Signal(object)
    backend_changed = Signal(str)

    def __init__(self, settings: AudioSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(600)
        self.settings = settings
        self._buffer_available = False
        self._operation_busy = False
        self._catalog: DeviceCatalog | None = None
        self._state = "STOPPED"
        self._status_message = ""
        self._selection_problem = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(18)

        audio_group = QGroupBox("音声入力")
        grid = QGridLayout(audio_group)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.backend_combo = QComboBox()
        self.backend_combo.setAccessibleName("音声の入力方式")
        self.backend_combo.addItem("再生デバイス（WASAPI Loopback）", "wasapi_loopback")
        self.backend_combo.addItem("録音デバイス（VoiceMeeter など）", "input_device")
        self.backend_combo.setCurrentIndex(
            self.backend_combo.findData(settings.backend)
        )
        self.device_combo = QComboBox()
        self.device_combo.setAccessibleName("取得する音声デバイス")
        self.device_combo.setPlaceholderText("音声デバイスを選択")
        self.device_combo.setMinimumWidth(320)
        self.device_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.device_combo.setMinimumContentsLength(30)
        self.refresh_button = QPushButton("再検索")
        self.refresh_button.setToolTip("停止中に音声デバイス一覧を更新します")
        grid.addWidget(QLabel("入力方式"), 0, 0)
        grid.addWidget(self.backend_combo, 0, 1)
        grid.addWidget(self.refresh_button, 0, 2)
        grid.addWidget(QLabel("デバイス"), 1, 0)
        grid.addWidget(self.device_combo, 1, 1, 1, 2)
        self.format_label = QLabel("デバイス未選択")
        self.format_label.setObjectName("deviceInfo")
        self.format_label.setWordWrap(True)
        grid.addWidget(self.format_label, 2, 1, 1, 2)
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setAccessibleName("音声取得を開始")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setAccessibleName("音声取得を停止")
        self.state_label = QLabel("STOPPED")
        self.state_label.setObjectName("captureStatus")
        self.state_label.setAccessibleName("音声取得の状態")
        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        controls.addWidget(self.state_label)
        grid.addLayout(controls, 3, 0, 1, 3)
        self.message_label = QLabel("")
        self.message_label.setObjectName("audioMessage")
        self.message_label.setWordWrap(True)
        grid.addWidget(self.message_label, 4, 0, 1, 3)
        snapshot_row = QHBoxLayout()
        self.replay_button = QPushButton("直近音声を Replay へ")
        self.dump_button = QPushButton("直近音声を WAV 保存")
        self.sequence_button = QPushButton("直近音声の順序を解析")
        snapshot_row.addWidget(self.replay_button)
        snapshot_row.addWidget(self.dump_button)
        snapshot_row.addWidget(self.sequence_button)
        snapshot_row.addStretch()
        grid.addLayout(snapshot_row, 5, 0, 1, 3)
        layout.addWidget(audio_group)

        meter_group = QGroupBox("Audio Level")
        meter_layout = QVBoxLayout(meter_group)
        meter_layout.setSpacing(12)
        row = QHBoxLayout()
        self.rms_label = QLabel("RMS  −∞ dBFS")
        self.rms_label.setObjectName("levelValue")
        self.peak_label = QLabel("Peak  −∞ dBFS")
        row.addWidget(self.rms_label)
        row.addStretch()
        row.addWidget(self.peak_label)
        meter_layout.addLayout(row)
        self.level_meter = QProgressBar()
        self.level_meter.setAccessibleName("音声の RMS 音量")
        self.level_meter.setRange(0, 800)
        self.level_meter.setValue(0)
        self.level_meter.setTextVisible(False)
        self.level_meter.setMinimumHeight(26)
        meter_layout.addWidget(self.level_meter)
        self.buffer_label = QLabel(f"バッファ  0.0 / {settings.buffer_duration:g} 秒")
        self.quality_label = QLabel("欠落 0 フレーム · オーバーフロー 0")
        self.quality_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom = QHBoxLayout()
        bottom.addWidget(self.buffer_label)
        bottom.addStretch()
        bottom.addWidget(self.quality_label)
        meter_layout.addLayout(bottom)
        layout.addWidget(meter_group)
        note = QLabel("直近音声を Replay へ送るか、保持音声の順序を解析できます。")
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

        self.backend_combo.currentIndexChanged.connect(self._backend_changed)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self.stop_requested)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.replay_button.clicked.connect(self.replay_requested)
        self.dump_button.clicked.connect(self.dump_requested)
        self.sequence_button.clicked.connect(self.sequence_requested)
        self._update_controls()

    def set_buffer_available(self, available: bool) -> None:
        self._buffer_available = available
        self._update_controls()

    def set_operation_busy(self, busy: bool) -> None:
        self._operation_busy = busy
        self._update_controls()

    @property
    def selected_device(self) -> AudioDevice | None:
        value = self.device_combo.currentData()
        return value if isinstance(value, AudioDevice) else None

    def _backend_changed(self) -> None:
        self.backend_changed.emit(self.backend_combo.currentData())
        self._populate(retain_current=False)

    def _device_changed(self) -> None:
        self._selection_problem = ""
        self._update_format()
        self._update_controls()
        self._update_message()
        self.selection_changed.emit(self.selected_device)

    def _start(self) -> None:
        if self.selected_device is not None:
            self.start_requested.emit(self.selected_device)

    def set_catalog(self, catalog: DeviceCatalog) -> None:
        self._catalog = catalog
        self._populate()

    def _populate(self, retain_current: bool = True) -> None:
        previous = self.selected_device if retain_current else None
        backend = self.backend_combo.currentData()
        identity = self.settings.device
        if previous is not None and previous.backend == backend:
            identity = previous.identity()
        self.device_combo.blockSignals(True)
        try:
            self.device_combo.clear()
            devices = self._catalog.for_backend(backend) if self._catalog else ()
            for device in devices:
                self.device_combo.addItem(f"{device.name} [{device.host_api}]", device)
            selection = None
            self._selection_problem = ""
            if identity is not None and identity.loopback == (backend == "wasapi_loopback"):
                try:
                    selection = resolve_device(devices, identity)
                except AudioError as error:
                    self._selection_problem = str(error)
            elif self._catalog:
                selection = self._catalog.default_for(backend)
            if not devices:
                self._selection_problem = "利用できる音声デバイスがありません。接続を確認して再検索してください。"
            index = next((i for i, item in enumerate(devices) if item == selection), -1)
            self.device_combo.setCurrentIndex(index)
        finally:
            self.device_combo.blockSignals(False)
        self._update_format()
        self._update_controls()
        self._update_message()

    def _update_format(self) -> None:
        device = self.selected_device
        if device is None:
            self.format_label.setText("デバイス未選択")
            self.device_combo.setToolTip("")
        else:
            self.format_label.setText(
                f"{device.sample_rate:,} Hz · {device.channels} ch · {device.host_api}"
            )
            self.device_combo.setToolTip(f"{device.name}\n{self.format_label.text()}")

    def _update_controls(self) -> None:
        idle = self._state in {"STOPPED", "ERROR"}
        self.start_button.setEnabled(idle and self.selected_device is not None)
        self.stop_button.setEnabled(self._state in ACTIVE_STATES)
        self.backend_combo.setEnabled(idle)
        self.device_combo.setEnabled(idle)
        self.refresh_button.setEnabled(idle)
        snapshot_enabled = self._buffer_available and not self._operation_busy
        self.replay_button.setEnabled(snapshot_enabled)
        self.dump_button.setEnabled(snapshot_enabled)
        self.sequence_button.setEnabled(snapshot_enabled)

    def _update_message(self) -> None:
        self.message_label.setText(self._selection_problem or self._status_message)

    def set_status(self, status: CaptureStatus) -> None:
        self._state, self._status_message = status.state, status.message
        self.state_label.setText(status.state.replace("_", " "))
        self.state_label.setProperty("state", status.state)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        if status.device is not None:
            self.format_label.setText(
                f"{status.device.sample_rate:,} Hz · {status.device.channels} ch · {status.device.host_api}"
            )
        self._update_controls()
        self._update_message()

    def set_level(self, level: AudioLevel) -> None:
        rms = f"{level.rms_dbfs:.1f}" if level.rms > 0 else "−∞"
        peak = f"{level.peak_dbfs:.1f}" if level.peak > 0 else "−∞"
        self.rms_label.setText(f"RMS  {rms} dBFS")
        self.peak_label.setText(f"Peak  {peak} dBFS")
        self.level_meter.setValue(max(0, min(800, round((level.rms_dbfs + 80) * 10))) if level.rms else 0)
        self.buffer_label.setText(
            f"バッファ  {level.buffered_seconds:.1f} / {self.settings.buffer_duration:g} 秒"
        )
        detail = f"欠落 {level.dropped_frames:,} フレーム · オーバーフロー {level.overflow_count}"
        self.quality_label.setText(("CLIP · " if level.clipping else "") + detail)
