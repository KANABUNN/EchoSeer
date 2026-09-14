"""Main window with live audio controls and asynchronous clean shutdown."""

from dataclasses import replace,asdict
from datetime import datetime
import logging
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMainWindow, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from audio.backend import AudioDevice
from audio.device_manager import DeviceCatalog
from audio.level import AudioLevel
from audio.service import CaptureStatus
from audio.sources import LiveSource,ClipSource
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
from ui.live_controller import LiveController
from ui.overlay import OverlayWindow
from ui.overlay_settings import OverlaySettingsDialog
from ui.oracle_map import copy_positions
from ui.presentation import present
from replay.timeline import cue_clip
from ui.theme import DARK_STYLE
from ui.operation_controller import OperationController, OperationResult
from ui.replay_page import ReplayPage
from ui.calibration_page import CalibrationPage
from ui.evaluation_page import EvaluationPage
from ui.review_page import ReviewPage
from ui.template_controller import TemplateController, TemplateTask, TemplateResult

logger = logging.getLogger("oracle_assistant.ui")


class MainWindow(QMainWindow):
    def __init__(
        self, data_root: Path, warning: str | None = None,
        settings: AppConfig | None = None, config_manager: ConfigManager | None = None,
        controller: AudioController | None = None,
        operations: OperationController | None = None,
        templates: TemplateController | None = None,
        recognizer: LiveController | None = None,
    ) -> None:
        super().__init__()
        self.data_root = data_root
        self.settings = settings or AppConfig()
        self.config_manager = config_manager or ConfigManager(data_root / "config.json")
        self._closing = False
        self.setWindowTitle("Destiny 2 · Oracle Assistant")
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry()
        width, height = max(620, available.width() - 40), max(400, available.height() - 60)
        self.setMinimumSize(min(760, width), min(600, height))
        self.resize(min(1024, width), min(820, height))
        self.setStyleSheet(DARK_STYLE)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)
        title = QLabel("Oracle Assistant · Destiny 2 / Vault of Glass")
        title.setObjectName("subtitle")
        layout.addWidget(title)
        self.warning_label = QLabel(warning or "")
        self.warning_label.setObjectName("warning")
        self.warning_label.setWordWrap(True)
        self.warning_label.setAccessibleName("設定または保存先の警告")
        self.warning_label.setVisible(bool(warning))
        layout.addWidget(self.warning_label)
        self.live_page = LivePage(self.settings.audio, labels=self.settings.oracle_labels,
                                  positions=self.settings.oracle_map_positions)
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
        self.evaluation_page=EvaluationPage()
        self.evaluation_scroll=QScrollArea()
        self.evaluation_scroll.setWidgetResizable(True)
        self.evaluation_scroll.setWidget(self.evaluation_page)
        self.tabs.addTab(self.evaluation_scroll,"評価")
        self.review_page=ReviewPage()
        self.review_scroll=QScrollArea()
        self.review_scroll.setWidgetResizable(True)
        self.review_scroll.setWidget(self.review_page)
        self.tabs.addTab(self.review_scroll,"記録")
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
            recorder=RecognitionRecorder(data_root / "logs", self.settings.logging, conditions={"sample_rate":self.settings.audio.internal_sample_rate,"recognition":asdict(self.settings.recognition)}),
            sequence_settings=self.settings.sequence,
        )
        self.operations.finished.connect(self._on_operation, Qt.ConnectionType.QueuedConnection)
        self.operations.busy_changed.connect(self._on_operation_busy)
        self.operations.evaluation_progress.connect(self.evaluation_page.set_progress,Qt.ConnectionType.QueuedConnection)
        self.evaluation_page.open_requested.connect(self._open_dataset)
        self.evaluation_page.baseline_requested.connect(self._open_baseline)
        self.evaluation_page.evaluate_requested.connect(self._evaluate_dataset)
        self.evaluation_page.export_requested.connect(self._export_evaluation)
        self.evaluation_page.cancel_requested.connect(self.operations.cancel_current)
        self.replay_page.review_widget.save_requested.connect(self._save_review_case)
        self.review_page.open_requested.connect(self._open_logs)
        self.review_page.replay_requested.connect(self._replay_log_audio)
        self.review_page.evaluate_requested.connect(self._evaluate_review_cases)
        self.review_page.cancel_requested.connect(self.operations.cancel_current)
        self.live_page.review_button.clicked.connect(lambda:self._review_live(False))
        self.live_page.incorrect_button.clicked.connect(lambda:self._review_live(True))
        self.replay_page.timeline.analyze_requested.connect(self._analyze_timeline)
        self.replay_page.timeline.selection_changed.connect(self._select_timeline_cue)
        self.replay_page.timeline.listen_requested.connect(self._listen_timeline_cue)
        self.replay_page.timeline.stop_requested.connect(self.operations.stop_playback)
        self.replay_page.open_requested.connect(self._open_wave)
        self.replay_page.analyze_requested.connect(self._analyze_wave)
        self.replay_page.sequence_requested.connect(self._analyze_sequence)
        self.operations.sequence_progress.connect(self._on_sequence_progress, Qt.ConnectionType.QueuedConnection)
        self.replay_page.save_requested.connect(self._save_processed)
        self.live_page.replay_requested.connect(self._analyze_live)
        self.live_page.dump_requested.connect(self._dump_live)
        self.live_page.sequence_requested.connect(self._analyze_live_sequence)
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
        self.recognizer = recognizer or LiveController(
            self._make_live_classifier, self.settings.recognition, self.settings.sequence,
            RecognitionRecorder(data_root / "logs", self.settings.logging, conditions={"sample_rate":self.settings.audio.internal_sample_rate,"recognition":asdict(self.settings.recognition)}), parent=self,
        )
        self.recognizer.updated.connect(self._on_live_update, Qt.ConnectionType.QueuedConnection)
        self.live_page.dashboard.round_requested.connect(self._select_live_round)
        self._capture_live = False
        self._last_audio_loss = (0, 0)
        self.overlay = OverlayWindow(self.settings.overlay, self.settings.oracle_labels,
                                     self.settings.oracle_map_positions, parent=self)
        self.settings.overlay.x, self.settings.overlay.y = self.overlay.x(), self.overlay.y()
        self.overlay_dialog = OverlaySettingsDialog(self.settings.overlay, self)
        self.overlay.position_changed.connect(self._save_overlay_position)
        self.overlay.enabled_changed.connect(lambda value: self._update_overlay({"enabled": value}))
        self.overlay.drag_mode_changed.connect(self._sync_overlay_controls)
        self.overlay_dialog.settings_changed.connect(self._update_overlay)
        self.overlay_dialog.drag_requested.connect(self.overlay.set_drag_mode)
        self.overlay_dialog.map_edit_requested.connect(self.live_page.dashboard.oracle_map.set_edit_mode)
        self.overlay_dialog.map_reset_requested.connect(lambda: self._save_map_positions(copy_positions()))
        self.live_page.dashboard.oracle_map.positions_changed.connect(self._save_map_positions)
        self.live_page.dashboard.overlay_button.setChecked(self.settings.overlay.enabled)
        self.live_page.dashboard.overlay_requested.connect(lambda value: self._update_overlay({"enabled": value}))
        self.live_page.dashboard.overlay_settings_requested.connect(self._show_overlay_settings)
        self.overlay.set_result(present(self.live_page.dashboard.result))
        recognition_view = self.replay_page.recognition
        recognition_view.event_logs_checkbox.setChecked(self.settings.logging.event_logs)
        recognition_view.uncertain_audio_checkbox.setChecked(self.settings.logging.uncertain_audio)
        recognition_view.success_audio_checkbox.setChecked(self.settings.logging.success_audio)
        self.review_page.success_checkbox.setChecked(self.settings.logging.success_audio)
        recognition_view.success_audio_changed.connect(self._save_success_audio)
        self.review_page.success_audio_changed.connect(self._save_success_audio)
        recognition_view.event_logging_changed.connect(self._save_event_logging)
        recognition_view.uncertain_audio_changed.connect(self._save_uncertain_audio)
        self._record_target = None
        self._last_deleted = None
        self.templates.finished.connect(self._on_template_result, Qt.ConnectionType.QueuedConnection)
        self.templates.busy_changed.connect(self._on_template_busy)
        self.calibration_page.import_requested.connect(self._import_templates)
        self.calibration_page.quality_requested.connect(self._check_template_quality)
        self.calibration_page.label_changed.connect(self._save_oracle_label)
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

    def _make_live_classifier(self):
        recognition = self.settings.recognition
        return OracleClassifier(
            self.templates.manager, self.settings.audio.internal_sample_rate,
            recognition.template_aggregation, recognition.top_n,
            BandpassSettings(recognition.bandpass_low_hz, recognition.bandpass_high_hz)
            if recognition.bandpass_enabled else None,
            waveform_weight=recognition.waveform_weight, spectrum_weight=recognition.spectrum_weight,
        )

    def _update_live_pause(self):
        message = ("Calibration中 · 認識を一時停止" if self._record_target is not None or self.templates.busy
                   else "Replay処理中 · 認識を一時停止" if self.operations.busy else "")
        self.recognizer.set_paused(message)

    @Slot(bool)
    def _on_template_busy(self, busy):
        self.calibration_page.set_busy(busy)
        self._update_live_pause()

    @Slot(object)
    def _on_live_update(self, update):
        if self._closing or update.generation != self.recognizer.generation:
            return
        self.live_page.set_review_available(self._capture_live and self.recognizer.latest_cue is not None)
        view = self.live_page.dashboard.set_result(update.result.snapshot, update.message)
        if update.result.notices:
            self.live_page.message_label.setText("\n".join(update.result.notices))
            self.live_page.message_label.show()
        if hasattr(self, "overlay"):
            self.overlay.set_result(view)

    @Slot(int)
    def _select_live_round(self, round_index):
        self.recognizer.select_round(round_index)
        self._clear_live_view(round_index)

    def _clear_live_view(self, round_index=None, message=""):
        self.live_page.set_review_available(False)
        self.live_page.dashboard.clear(round_index, message)
        if hasattr(self, "overlay"):
            self.overlay.set_result(present(self.live_page.dashboard.result, message))

    def _sync_overlay_controls(self, *unused):
        button = self.live_page.dashboard.overlay_button
        button.blockSignals(True)
        button.setChecked(self.settings.overlay.enabled)
        button.blockSignals(False)
        self.overlay_dialog.sync(self.settings.overlay, self.overlay.drag_mode)

    @Slot(object)
    def _update_overlay(self, values):
        proposal = replace(self.settings.overlay, **values)
        self.overlay.apply_settings(proposal)
        self.settings.overlay = replace(self.overlay.settings)
        self._sync_overlay_controls()
        self._save_config()

    @Slot(int, int)
    def _save_overlay_position(self, x, y):
        self.settings.overlay.x, self.settings.overlay.y = x, y
        self._sync_overlay_controls()
        self._save_config()

    @Slot(object)
    def _save_map_positions(self, positions):
        self.settings.oracle_map_positions = copy_positions(positions)
        self.live_page.dashboard.oracle_map.set_positions(positions)
        self.overlay.set_positions(positions)
        self._save_config()

    @Slot()
    def _show_overlay_settings(self):
        self._sync_overlay_controls()
        self.overlay_dialog.show()
        self.overlay_dialog.raise_()
        self.overlay_dialog.activateWindow()

    def _save_config(self) -> bool:
        try:
            self.config_manager.save(self.settings)
            return True
        except (OSError, ConfigValidationError):
            logger.exception("Could not save audio selection")
            self.warning_label.setText("設定を保存できません。保存先を確認してください。")
            self.warning_label.show()
            return False

    @Slot(bool)
    def _save_success_audio(self,enabled):
        self.settings.logging.success_audio=enabled
        self.recognizer.set_logging(self.settings.logging)
        if self.operations.recorder is not None:
            self.operations.recorder.settings.success_audio=enabled
        for checkbox in (self.replay_page.recognition.success_audio_checkbox,self.review_page.success_checkbox):
            checkbox.blockSignals(True);checkbox.setChecked(enabled);checkbox.blockSignals(False)
        self._save_config()

    @Slot(bool)
    def _save_event_logging(self, enabled: bool) -> None:
        self.settings.logging.event_logs = enabled
        self.recognizer.set_logging(self.settings.logging)
        if self.operations.recorder is not None:
            self.operations.recorder.settings.event_logs = enabled
            self.operations.recorder.events.enabled = enabled
        self._save_config()

    @Slot(bool)
    def _save_uncertain_audio(self, enabled: bool) -> None:
        self.settings.logging.uncertain_audio = enabled
        self.recognizer.set_logging(self.settings.logging)
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
        self._last_audio_loss = (0, 0)
        self._clear_live_view(self.live_page.dashboard.round_index)
        self._save_selection(device)
        self.live_page.set_status(CaptureStatus("STARTING", "音声入力を開始しています。", device))
        self.controller.start(device)

    @Slot()
    def _stop_capture(self) -> None:
        self._capture_live = False
        self.recognizer.stop()
        self._clear_live_view(self.live_page.dashboard.round_index)
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
            was_live = self._capture_live
            self._capture_live = event.state == "LIVE"
            if event.state == "LIVE" and not was_live:
                self._last_audio_loss = (0, 0)
                ring = self.controller.service.buffer
                if ring is not None:
                    self.recognizer.attach(ring, self.live_page.dashboard.round_index)
                    self._update_live_pause()
            elif was_live and event.state in ("DEVICE_LOST", "RECONNECTING"):
                self.recognizer.invalidate("DEVICE_LOST")
                self._clear_live_view(self.live_page.dashboard.round_index, "音声の接続が切れました。")
            elif event.state in ("STOPPED", "ERROR"):
                self.recognizer.stop()
                self._clear_live_view(self.live_page.dashboard.round_index)
            self.live_page.set_status(event)
            self.calibration_page.set_live(event.state == "LIVE")
            self.statusBar().showMessage(event.state)
            if event.state == "LIVE" and event.device is not None:
                self.settings.audio.backend = event.device.backend
                self._save_selection(event.device)
        elif isinstance(event, RecordingEvent):
            self._on_recording(event)
        elif isinstance(event, AudioLevel):
            loss = (event.dropped_frames, event.overflow_count)
            if self._capture_live and any(current > previous for current, previous in zip(loss, self._last_audio_loss)):
                self.recognizer.invalidate("AUDIO_GAP")
                self._clear_live_view(self.live_page.dashboard.round_index, "音声が欠落しました。")
            self._last_audio_loss = loss
            self.live_page.set_level(event)
        ring = self.controller.service.buffer
        self.live_page.set_buffer_available(ring is not None and ring.size_frames > 0)

    @Slot(bool)
    def _on_operation_busy(self, busy: bool) -> None:
        if self._closing:
            return
        self.replay_page.set_busy(busy)
        self.replay_page.timeline.set_playing(self.operations.playing)
        self.evaluation_page.set_busy(busy)
        self.review_page.set_busy(busy)
        self.replay_page.recognition.event_logs_checkbox.setEnabled(not busy)
        self.replay_page.recognition.uncertain_audio_checkbox.setEnabled(not busy)
        self.live_page.set_operation_busy(busy)
        self._update_live_pause()

    def _review_live(self,incorrect=False):
        cue=self.recognizer.latest_cue
        if cue is None:
            self.live_page.message_label.setText("保存できる直近の認識音がありません。");self.live_page.message_label.show();return
        page=self.replay_page
        page.source=ClipSource(cue.clip)
        page.path_label.setText("Liveの認識イベント（固定コピー）")
        if self.operations.analyze(page.source):
            page.begin_analysis()
            page.review_widget.set_context({"description":f"元のLive判定：{cue.detection.status.value} / {cue.detection.reason}",
                "source":asdict(cue.detection.context),"original_reason":cue.detection.reason,
                "original_detection":asdict(cue.detection),
                "original_ranking":[{"oracle":r.oracle.value,"combined":r.score,"waveform":r.waveform_score,"spectrum":r.spectrum_score} for r in cue.classification.ranking]},incorrect)
            if incorrect:page.review_toggle.setChecked(True)
            self.tabs.setCurrentIndex(1)

    @Slot()
    def _save_review_case(self):
        page=self.replay_page
        if page.result is None:return
        try:annotation=page.review_widget.annotation()
        except ValueError as error:
            page.message_label.setText(str(error));return
        trace=page.timeline.selected_trace
        bounds=(trace.start_frame,trace.end_frame) if trace and page.review_widget.scope_combo.currentData()=="selected" else None
        observed={"context":page.review_widget.context,"processed_checksum":page.result.checksum,
            "conditions":{"sample_rate":self.operations.analyzer.sample_rate,"recognition":asdict(self.settings.recognition)}}
        if page.timeline.result:
            observed["profile"]=page.timeline.result.profile
        if trace:
            observed["selected_reason"]=trace.detection.reason
            observed["ranking"]=[{"oracle":r.oracle.value,"combined":r.score,"waveform":r.waveform_score,"spectrum":r.spectrum_score} for r in trace.classification.ranking]
        self.operations.save_review_case(ClipSource(page.result.original),self.data_root/"review"/"cases",annotation,observed,bounds)

    @Slot()
    def _open_logs(self):
        folder=QFileDialog.getExistingDirectory(self,"ログの保存フォルダー",str(self.data_root/"logs"))
        if folder:self.operations.read_logs(Path(folder))

    @Slot()
    def _replay_log_audio(self):
        page=self.review_page
        event=page.selected_event
        if event and self.operations.analyze_log_audio(page.catalog.root,event.audio_relative,event.payload.get("audio_native_checksum")):
            replay=self.replay_page;replay.source=None;replay.begin_analysis()
            replay.path_label.setText(f"保存ログ：{event.audio_relative}")
            p=event.payload
            replay.review_widget.set_context({"description":f"元の判定：{p.get("confidence_level")} / {p.get("reason")}",
                "original_log":p},bool(p.get("problem_reason")))
            self.tabs.setCurrentIndex(1)

    @Slot()
    def _evaluate_review_cases(self):
        self.operations.snapshot_review_cases(self.data_root/"review"/"cases")

    @Slot()
    def _open_dataset(self):
        path,_=QFileDialog.getOpenFileName(self,"Datasetを開く",str(self.data_root),"JSON (*.json)")
        if path:
            self.evaluation_page.set_dataset(Path(path))

    @Slot()
    def _open_baseline(self):
        path,_=QFileDialog.getOpenFileName(self,"比較元レポートを開く",str(self.data_root),"JSON (*.json)")
        if path:
            self.evaluation_page.set_baseline(Path(path))

    @Slot()
    def _evaluate_dataset(self):
        page=self.evaluation_page
        if page.dataset_path and self.operations.evaluate_dataset(page.dataset_path,page.baseline_path):
            page.begin()

    @Slot()
    def _export_evaluation(self):
        page=self.evaluation_page
        if page.report:
            folder=QFileDialog.getExistingDirectory(self,"レポートの保存先",str(self.data_root))
            if folder:
                self.operations.export_evaluation(page.report,Path(folder)/f"evaluation-{uuid4().hex}")

    @Slot()
    def _open_wave(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "WAV を開く", str(self.data_root), "WAV (*.wav *.WAV)")
        if path:
            self.replay_page.set_wave_path(Path(path))

    @Slot()
    def _analyze_timeline(self):
        page=self.replay_page
        if page.source is not None and self.operations.analyze_timeline(page.source):
            page.begin_analysis()

    @Slot(int)
    def _select_timeline_cue(self, row):
        page=self.replay_page
        trace=page.timeline.selected_trace
        if trace is not None:
            page.recognition.set_result(trace.classification)
            page.recognition.set_detection(trace.detection)
            page.recognition.setTitle("選択した音の候補 · 複合スコア")

    @Slot()
    def _listen_timeline_cue(self):
        page=self.replay_page
        trace=page.timeline.selected_trace
        if trace is not None and page.result is not None:
            self.operations.listen(cue_clip(page.result.original, trace))

    @Slot()
    def _analyze_wave(self) -> None:
        if self.replay_page.source is not None:
            if self.operations.analyze(self.replay_page.source):
                self.replay_page.begin_analysis()

    @Slot(object)
    def _on_sequence_progress(self, snapshot) -> None:
        if not self._closing:
            self.replay_page.sequence.set_result(snapshot)

    @Slot()
    def _analyze_sequence(self) -> None:
        page = self.replay_page
        if page.source is not None and self.operations.analyze_sequence(page.source, page.sequence.round_index):
            page.begin_analysis()

    @Slot()
    def _analyze_live_sequence(self) -> None:
        source = self._live_source()
        if source is not None and self.operations.analyze_sequence(source, self.replay_page.sequence.round_index, live=True):
            self.replay_page.begin_analysis()
            self.tabs.setCurrentIndex(1)

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
        if event.kind in ("logs","case_save","case_dataset"):
            if event.error or event.cancelled:
                target=self.replay_page.message_label if event.kind=="case_save" else self.review_page.message_label
                target.setText(event.error or "処理を中止しました。")
            elif event.log_catalog is not None:
                self.review_page.set_catalog(event.log_catalog)
            elif event.kind=="case_save" and event.path:
                self.replay_page.message_label.setText(f"正解付き音声を保存しました：{event.path.parent}")
                self.review_page.message_label.setText("手動記録を一括評価できます。")
            elif event.kind=="case_dataset" and event.path:
                self.evaluation_page.set_dataset(event.path)
                self.evaluation_page.set_baseline(None)
                self.tabs.setCurrentIndex(3)
                self._evaluate_dataset()
            return
        if event.kind in ("dataset","report_export"):
            page=self.evaluation_page
            if event.error or event.cancelled:
                page.message_label.setText(event.error or "処理を中止しました。")
            elif event.report is not None:
                page.set_report(event.report)
            elif event.path is not None:
                page.message_label.setText(f"レポートとCSVを保存しました：{event.path.parent}")
            return
        if (event.error or event.cancelled) and event.kind in ("sequence", "live_sequence"):
            self.replay_page.sequence.clear()
        if event.error:
            if event.kind == "dump":
                self.live_page.message_label.setText(event.error)
            else:
                self.replay_page.show_error(event.error)
        elif event.cancelled:
            self.replay_page.show_error("処理を中止しました。")
        elif event.analysis is not None:
            if event.kind=="log_audio":
                self.replay_page.source=ClipSource(event.analysis.original)
            self.replay_page.set_result(event.analysis, live=event.kind in ("live", "live_sequence"))
            self.replay_page.recognition.set_result(event.classification)
            self.replay_page.recognition.set_detection(event.detection)
            if event.timeline is not None:
                self.replay_page.timeline.set_result(event.timeline, event.analysis.original.sample_rate)
            if event.sequence is not None:
                self.replay_page.recognition.setTitle("最後の音の候補 · 複合スコア")
                self.replay_page.sequence.set_result(event.sequence.snapshot)
            if event.persistence is not None and event.persistence.notices:
                self.replay_page.recognition.append_notices(event.persistence.notices)
        elif event.kind == "listen":
            self.replay_page.message_label.setText("選択音の再生を終えました。")
        elif event.path is not None:
            message = f"保存しました：{event.path}"
            if event.kind == "dump":
                self.live_page.message_label.setText(message)
            else:
                self.replay_page.message_label.setText(message)
            self.statusBar().showMessage(message)

    @Slot()
    def _check_template_quality(self):
        sample = self.calibration_page.sample
        if sample and self.templates.submit(TemplateTask(
            "quality", self.calibration_page.oracle, sample_id=sample.metadata.sample_id,
        )):
            self.calibration_page.begin_quality()

    @Slot(str, str)
    def _save_oracle_label(self, oracle, label):
        self.settings.oracle_labels[oracle] = label
        labels = self.settings.oracle_labels
        self.calibration_page.set_labels(labels)
        self.live_page.dashboard.set_labels(labels)
        self.overlay.set_labels(labels)
        sequence = self.replay_page.sequence
        sequence.labels = labels
        if sequence.result:
            sequence.set_result(sequence.result)
        recognition = self.replay_page.recognition
        current, detection = recognition.result, recognition.detection
        recognition.labels = labels
        recognition.set_result(current)
        recognition.set_detection(detection)
        saved = self._save_config()
        self.calibration_page.message_label.setText("表示名を保存しました。" if saved else
                                                    "表示名を画面に反映しました。設定は保存できませんでした。")

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
        self._update_live_pause()
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
            self._update_live_pause()

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
        if event.quality is not None:
            self.calibration_page.set_quality(event.quality)
        elif event.kind == "quality":
            self.calibration_page.set_quality_error(event.message)
        if event.deleted is not None:
            self._last_deleted = event.deleted
        elif event.kind == "restore" and event.selected_id:
            self._last_deleted = None
        self.calibration_page.set_undo(self._last_deleted is not None)
        if event.message:
            self.calibration_page.message_label.setText(event.message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.overlay.hide()
        self.overlay_dialog.hide()
        audio_closed = self.controller.shutdown(timeout=0)
        operations_closed = self.operations.shutdown(timeout=0)
        templates_closed = self.templates.shutdown(timeout=0)
        live_closed = self.recognizer.shutdown(timeout=0)
        if audio_closed and operations_closed and templates_closed and live_closed:
            self._close_timer.stop()
            event.accept()
        else:
            self.live_page.set_status(CaptureStatus("STOPPING", "音声取得を終了しています。"))
            self._close_timer.start()
            event.ignore()
