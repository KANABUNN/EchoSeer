"""Phase 3 template management, with stable IDs and editable display labels."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget,
)
from audio.recorder import RecordingEvent
from encounter.vog_oracles import OracleId
from templates.manager import TemplateCatalog, TemplateSample


class CalibrationPage(QWidget):
    import_requested = Signal()
    record_requested = Signal()
    cancel_requested = Signal()
    play_requested = Signal()
    stop_play_requested = Signal()
    delete_requested = Signal()
    restore_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, labels: dict[str, str]) -> None:
        super().__init__()
        self.setMinimumWidth(600)
        self._labels = labels
        self._catalog = TemplateCatalog(())
        self._busy = self._recording = self._live = self._playing = False
        self._undo = False
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel("Oracle サンプル")
        title.setObjectName("title")
        layout.addWidget(title)
        help_label = QLabel("Oracle ごとに複数の WAV を登録できます。録音は Live で音声入力を開始してから行ってください。")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        row = QHBoxLayout()
        self.oracle_combo = QComboBox()
        for oracle in OracleId:
            self.oracle_combo.addItem(f"{oracle.value} · {labels[oracle.value]} (0)", oracle)
        row.addWidget(self.oracle_combo, 1)
        self.refresh_button = QPushButton("再読込")
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 10)
        self.duration_spin.setSingleStep(0.1)
        self.duration_spin.setValue(2)
        self.duration_spin.setSuffix(" 秒")
        self.duration_spin.setAccessibleName("サンプル録音時間")
        self.record_button = QPushButton("Record Sample")
        self.cancel_button = QPushButton("録音を中止")
        self.import_button = QPushButton("Import WAV")
        for widget in (self.duration_spin, self.record_button, self.cancel_button, self.import_button):
            row.addWidget(widget)
        layout.addLayout(row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.sample_list = QListWidget()
        self.sample_list.setMinimumHeight(130)
        self.sample_list.setMaximumHeight(160)
        self.sample_list.setAccessibleName("登録済み Oracle サンプル")
        layout.addWidget(self.sample_list)
        row = QHBoxLayout()
        self.play_button = QPushButton("Listen")
        self.stop_play_button = QPushButton("再生を停止")
        self.delete_button = QPushButton("Delete")
        self.restore_button = QPushButton("削除を戻す")
        for widget in (self.play_button, self.stop_play_button, self.delete_button, self.restore_button):
            row.addWidget(widget)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("再生音量"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(20)
        self.volume_label = QLabel("20%")
        row.addWidget(self.volume_slider, 1)
        row.addWidget(self.volume_label)
        layout.addLayout(row)
        self.details_label = QLabel("サンプルを選択してください。")
        self.details_label.setTextFormat(Qt.TextFormat.PlainText)
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details_label)
        self.message_label = QLabel("")
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        self.issues_label = QLabel("")
        self.issues_label.setTextFormat(Qt.TextFormat.PlainText)
        self.issues_label.setWordWrap(True)
        self.issues_label.setObjectName("warning")
        self.issues_label.hide()
        layout.addWidget(self.issues_label)
        layout.addStretch()
        self.oracle_combo.currentIndexChanged.connect(self._populate)
        self.sample_list.currentRowChanged.connect(self._selection)
        self.volume_slider.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        for button, signal in (
            (self.import_button, self.import_requested), (self.record_button, self.record_requested),
            (self.cancel_button, self.cancel_requested), (self.play_button, self.play_requested),
            (self.stop_play_button, self.stop_play_requested), (self.delete_button, self.delete_requested),
            (self.restore_button, self.restore_requested), (self.refresh_button, self.refresh_requested),
        ):
            button.clicked.connect(signal.emit)
        self._update_controls()

    @property
    def oracle(self) -> OracleId:
        return OracleId(self.oracle_combo.currentData())

    @property
    def sample(self) -> TemplateSample | None:
        item = self.sample_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def set_live(self, live: bool) -> None:
        self._live = live
        self._update_controls()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if not busy:
            self._playing = False
        self._update_controls()

    def set_playing(self) -> None:
        self._playing = True
        self.message_label.setText("Windows の既定出力で再生しています。")
        self._update_controls()

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self._update_controls()

    def show_recording(self, event: RecordingEvent) -> None:
        self.progress_bar.setValue(round(event.progress * 1000))
        if event.message:
            self.message_label.setText(event.message)

    def set_undo(self, available: bool) -> None:
        self._undo = available
        self._update_controls()

    def set_catalog(self, catalog: TemplateCatalog, selected_id: str = "") -> None:
        old_id = self.sample.metadata.sample_id if self.sample else ""
        self._catalog = catalog
        for index, oracle in enumerate(OracleId):
            count = sum(s.metadata.oracle == oracle.value for s in catalog.samples)
            self.oracle_combo.setItemText(index, f"{oracle.value} · {self._labels[oracle.value]} ({count})")
        self.issues_label.setText("\n".join(catalog.issues))
        self.issues_label.setVisible(bool(catalog.issues))
        self._populate(selected_id=selected_id or old_id)

    def _populate(self, index: int = 0, selected_id: str = "") -> None:
        self.sample_list.clear()
        selected_row = 0
        for sample in self._catalog.samples:
            m = sample.metadata
            if m.oracle != self.oracle.value:
                continue
            item = QListWidgetItem(f"{m.source_name} · {m.duration_seconds:.3f} 秒 · {m.created_at[:19]} UTC")
            item.setData(Qt.ItemDataRole.UserRole, sample)
            self.sample_list.addItem(item)
            if m.sample_id == selected_id:
                selected_row = self.sample_list.count() - 1
        self.sample_list.setCurrentRow(selected_row if self.sample_list.count() else -1)
        self._selection()

    def _selection(self, row: int = 0) -> None:
        sample = self.sample
        if sample is None:
            self.details_label.setText("サンプルを選択してください。")
        else:
            m = sample.metadata
            self.details_label.setText(
                f"ID: {m.sample_id}\n"
                f"入力: {m.original_sample_rate:,} Hz / {m.original_channels} ch / "
                f"{m.original_frames / m.original_sample_rate:.3f} 秒\n"
                f"Peak: {m.peak:.6f} / RMS: {m.rms:.6f} / "
                f"クリッピング: {'あり・要確認' if m.clipping else 'なし'}\n"
                f"保存: {m.sample_rate:,} Hz / mono / {m.frames:,} frames\n"
                f"登録: {m.source} / {m.created_at}\n"
                + ("\n".join(m.notices) if m.notices else "品質上の警告はありません。")
            )
        self._update_controls()

    def _update_controls(self) -> None:
        idle = not self._busy and not self._recording
        for widget in (self.oracle_combo, self.sample_list, self.duration_spin,
                       self.import_button, self.refresh_button, self.volume_slider):
            widget.setEnabled(idle)
        self.record_button.setEnabled(idle and self._live)
        self.cancel_button.setEnabled(self._recording)
        self.play_button.setEnabled(idle and self.sample is not None)
        self.delete_button.setEnabled(idle and self.sample is not None)
        self.restore_button.setEnabled(idle and self._undo)
        self.stop_play_button.setEnabled(self._playing)
