"""Main window with live audio controls and asynchronous clean shutdown."""

from datetime import datetime
import logging
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from audio.backend import AudioDevice
from audio.device_manager import DeviceCatalog
from audio.level import AudioLevel
from audio.service import CaptureStatus
from audio.sources import LiveSource
from audio.recorder import RecordingEvent
from encounter.vog_oracles import OracleId
from detector.classifier import OracleClassifier
from detector.confidence import ConfidenceEngine
from logging_ext.recognition_logger import RecognitionRecorder
from dsp.bandpass import BandpassSettings
from config.manager import ConfigManager
from config.schema import AppConfig, ConfigValidationError
from ui.audio_controller import AudioController
from ui.live_page import LivePage
from ui.theme import DARK_STYLE
from ui.operation_controller import OperationController, OperationResult
from ui.replay_page import ReplayPage
from ui.calibration_page import CalibrationPage
from ui.template_controller import TemplateController, TemplateTask, TemplateResult

logger = logging.getLogger("oracle_assistant.ui")


class MainWindow(QMainWindow):
    def __init__(
        self, data_root: Path, warning: str | None = None,
        settings: AppConfig | None = None, config_manager: ConfigManager | None = None,
        controller: AudioController | None = None,
        operations: OperationController | None = None,
        templates: TemplateController | None = None,
    ) -> None:
        super().__init__()
        self.data_root = data_root
        self.settings = settings or AppConfig()
        self.config_manager = config_manager or ConfigManager(data_root / "config.json")
        self._closing = False
        self.setWindowTitle("Destiny 2 · Oracle Assistant")
        self.resize(1024, 820)
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
        self.replay_page = ReplayPage(labels=self.settings.oracle_labels)
        self.replay_scroll = QScrollArea()
        self.replay_scroll.setWidgetResizable(True)
        self.replay_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.replay_scroll.setWidget(self.replay_page)
        self.tabs.addTab(self.replay_scroll, "Replay")
        self.calibration_page = CalibrationPage(self.settings.oracle_labels)
        self.calibration_scroll = QScrollArea()
        self.calibration_scroll.setWidgetResizable(True)
        self.calibration_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.calibration_scroll.setWidget(self.calibration_page)
        self.tabs.addTab(self.calibration_scroll, "Calibration")
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
        self.operations = operations or OperationController(
            self.settings.audio.internal_sample_rate, parent=self,
            confidence=ConfidenceEngine(self.settings.recognition),
            recorder=RecognitionRecorder(data_root / "logs", self.settings.logging),
        )
        self.operations.finished.connect(self._on_operation, Qt.ConnectionType.QueuedConnection)
        self.operations.busy_changed.connect(self._on_operation_busy)
        self.replay_page.open_requested.connect(self._open_wave)
        self.replay_page.analyze_requested.connect(self._analyze_wave)
        self.replay_page.save_requested.connect(self._save_processed)
        self.live_page.replay_requested.connect(self._analyze_live)
        self.live_page.dump_requested.connect(self._dump_live)
        self.templates = templates or TemplateController(
            data_root / "templates", self.settings.audio.internal_sample_rate, parent=self,
        )
        if self.operations.classifier is None:
            recognition = self.settings.recognition
            self.operations.classifier = OracleClassifier(
                self.templates.manager, self.settings.audio.internal_sample_rate,
                recognition.template_aggregation, recognition.top_n,
                BandpassSettings(recognition.bandpass_low_hz, recognition.bandpass_high_hz)
                if recognition.bandpass_enabled else None,
                waveform_weight=recognition.waveform_weight,
                spectrum_weight=recognition.spectrum_weight,
            )
        recognition_view = self.replay_page.recognition
        recognition_view.event_logs_checkbox.setChecked(self.settings.logging.event_logs)
        recognition_view.uncertain_audio_checkbox.setChecked(self.settings.logging.uncertain_audio)
        recognition_view.event_logging_changed.connect(self._save_event_logging)
        recognition_view.uncertain_audio_changed.connect(self._save_uncertain_audio)
        self._record_target = None
        self._last_deleted = None
        self.templates.finished.connect(self._on_template_result, Qt.ConnectionType.QueuedConnection)
        self.templates.busy_changed.connect(self.calibration_page.set_busy)
        self.calibration_page.import_requested.connect(self._import_templates)
        self.calibration_page.record_requested.connect(self._record_template)
        self.calibration_page.cancel_requested.connect(self.controller.service.cancel_recording)
        self.calibration_page.play_requested.connect(self._play_template)
        self.calibration_page.stop_play_requested.connect(self.templates.stop_playback)
        self.calibration_page.delete_requested.connect(self._delete_template)
        self.calibration_page.restore_requested.connect(self._restore_template)
        self.calibration_page.refresh_requested.connect(lambda: self.templates.submit(TemplateTask("refresh")))
        self.templates.submit(TemplateTask("refresh"))
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
            self.warning_label.setText("設定を保存できません。保存先を確認してください。")
            self.warning_label.show()

    @Slot(bool)
    def _save_event_logging(self, enabled: bool) -> None:
        self.settings.logging.event_logs = enabled
        if self.operations.recorder is not None:
            self.operations.recorder.settings.event_logs = enabled
            self.operations.recorder.events.enabled = enabled
        self._save_config()

    @Slot(bool)
    def _save_uncertain_audio(self, enabled: bool) -> None:
        self.settings.logging.uncertain_audio = enabled
        if self.operations.recorder is not None:
            self.operations.recorder.settings.uncertain_audio = enabled
        self._save_config()

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
            self.calibration_page.set_live(event.state == "LIVE")
            self.statusBar().showMessage(event.state)
            if event.state == "LIVE" and event.device is not None:
                self.settings.audio.backend = event.device.backend
                self._save_selection(event.device)
        elif isinstance(event, RecordingEvent):
            self._on_recording(event)
        elif isinstance(event, AudioLevel):
            self.live_page.set_level(event)
        ring = self.controller.service.buffer
        self.live_page.set_buffer_available(ring is not None and ring.size_frames > 0)

    @Slot(bool)
    def _on_operation_busy(self, busy: bool) -> None:
        if self._closing:
            return
        self.replay_page.set_busy(busy)
        self.replay_page.recognition.event_logs_checkbox.setEnabled(not busy)
        self.replay_page.recognition.uncertain_audio_checkbox.setEnabled(not busy)
        self.live_page.set_operation_busy(busy)

    @Slot()
    def _open_wave(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "WAV を開く", str(self.data_root), "WAV (*.wav *.WAV)")
        if path:
            self.replay_page.set_wave_path(Path(path))

    @Slot()
    def _analyze_wave(self) -> None:
        if self.replay_page.source is not None:
            if self.operations.analyze(self.replay_page.source):
                self.replay_page.begin_analysis()

    def _live_source(self) -> LiveSource | None:
        ring = self.controller.service.buffer
        return LiveSource(ring) if ring is not None and ring.size_frames > 0 else None

    @Slot()
    def _analyze_live(self) -> None:
        source = self._live_source()
        if source is not None and self.operations.analyze(source, live=True):
            self.replay_page.begin_analysis()
            self.tabs.setCurrentIndex(1)

    def _save_path(self, prefix: str) -> Path | None:
        suggestion = self.data_root / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.wav"
        path, _ = QFileDialog.getSaveFileName(self, "WAV 保存", str(suggestion), "WAV (*.wav)")
        if not path:
            return None
        destination = Path(path)
        return destination if destination.suffix.lower() == ".wav" else destination.with_name(destination.name + ".wav")

    @Slot()
    def _dump_live(self) -> None:
        source = self._live_source()
        if source is not None:
            path = self._save_path("live-buffer")
            if path is not None:
                self.operations.dump(source, path)

    @Slot()
    def _save_processed(self) -> None:
        result = self.replay_page.result
        if result is not None:
            path = self._save_path("processed")
            if path is not None:
                self.operations.save(result.processed, path, self.replay_page.encoding_combo.currentData())

    @Slot(object)
    def _on_operation(self, event: OperationResult) -> None:
        if self._closing:
            return
        if event.error:
            if event.kind == "dump":
                self.live_page.message_label.setText(event.error)
            else:
                self.replay_page.show_error(event.error)
        elif event.cancelled:
            self.replay_page.show_error("処理を中止しました。")
        elif event.analysis is not None:
            self.replay_page.set_result(event.analysis, live=event.kind == "live")
            self.replay_page.recognition.set_result(event.classification)
            self.replay_page.recognition.set_detection(event.detection)
            if event.persistence is not None and event.persistence.notices:
                self.replay_page.recognition.append_notices(event.persistence.notices)
        elif event.path is not None:
            message = f"保存しました：{event.path}"
            if event.kind == "dump":
                self.live_page.message_label.setText(message)
            else:
                self.replay_page.message_label.setText(message)
            self.statusBar().showMessage(message)

    @Slot()
    def _import_templates(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Oracle WAV を登録", str(self.data_root), "WAV (*.wav *.WAV)")
        if paths:
            self.templates.submit(TemplateTask("import", self.calibration_page.oracle, tuple(Path(p) for p in paths)))

    @Slot()
    def _record_template(self) -> None:
        if self._record_target is not None or self.templates.busy:
            return
        token = uuid4().hex
        self._record_target = (token, self.calibration_page.oracle)
        self.calibration_page.set_recording(True)
        self.calibration_page.progress_bar.setValue(0)
        self.controller.service.record(self.calibration_page.duration_spin.value(), token)

    def _on_recording(self, event: RecordingEvent) -> None:
        if self._record_target is None or event.token != self._record_target[0]:
            return
        self.calibration_page.show_recording(event)
        if event.state == "COMPLETE":
            oracle = self._record_target[1]
            self._record_target = None
            self.calibration_page.set_recording(False)
            if not self.templates.submit(TemplateTask("record", oracle, clip=event.clip)):
                self.calibration_page.message_label.setText("保存処理が使用中です。再録音してください。")
        elif event.state in ("ERROR", "CANCELLED"):
            self._record_target = None
            self.calibration_page.set_recording(False)

    @Slot()
    def _play_template(self) -> None:
        sample = self.calibration_page.sample
        if sample and self.templates.submit(TemplateTask(
            "play", self.calibration_page.oracle, sample_id=sample.metadata.sample_id,
            volume=self.calibration_page.volume_slider.value() / 100,
        )):
            self.calibration_page.set_playing()

    @Slot()
    def _delete_template(self) -> None:
        sample = self.calibration_page.sample
        if sample:
            self.templates.submit(TemplateTask("delete", self.calibration_page.oracle, sample_id=sample.metadata.sample_id))

    @Slot()
    def _restore_template(self) -> None:
        if self._last_deleted:
            self.templates.submit(TemplateTask("restore", self._last_deleted.oracle, deleted=self._last_deleted))
            self.calibration_page.oracle_combo.setCurrentIndex(list(OracleId).index(self._last_deleted.oracle))

    @Slot(object)
    def _on_template_result(self, event: TemplateResult) -> None:
        if self._closing:
            return
        if event.catalog is not None:
            self.calibration_page.set_catalog(event.catalog, event.selected_id)
        if event.deleted is not None:
            self._last_deleted = event.deleted
        elif event.kind == "restore" and event.selected_id:
            self._last_deleted = None
        self.calibration_page.set_undo(self._last_deleted is not None)
        if event.message:
            self.calibration_page.message_label.setText(event.message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        audio_closed = self.controller.shutdown(timeout=0)
        operations_closed = self.operations.shutdown(timeout=0)
        templates_closed = self.templates.shutdown(timeout=0)
        if audio_closed and operations_closed and templates_closed:
            self._close_timer.stop()
            event.accept()
        else:
            self.live_page.set_status(CaptureStatus("STOPPING", "音声取得を終了しています。"))
            self._close_timer.start()
            event.ignore()
