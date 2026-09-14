"""GUI-only sample creation, per-Oracle readiness and current native quality checks."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
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
    quality_requested = Signal()
    label_changed = Signal(str, str)

    def __init__(self, labels: dict[str, str]) -> None:
        super().__init__()
        self.setMinimumWidth(600)
        self.setStyleSheet("QPushButton { padding: 5px 10px; } QComboBox { padding: 4px; }")
        self._labels = labels
        self._catalog = TemplateCatalog(())
        self._busy = self._recording = self._live = self._playing = False
        self._undo = False
        layout = QVBoxLayout(self)
        layout.setSpacing(7)
        title = QLabel("Oracle サンプル")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)
        help_label = QLabel("Oracle ごとに複数の WAV を登録できます。録音は Live で音声入力を開始してから行ってください。")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.readiness_label = QLabel()
        self.readiness_label.setTextFormat(Qt.TextFormat.PlainText)
        self.readiness_label.setWordWrap(True)
        self.readiness_label.setAccessibleName("7種類のOracleサンプルの登録状況")
        layout.addWidget(self.readiness_label)
        overview = QGridLayout()
        self.oracle_buttons = {}
        for index, oracle in enumerate(OracleId):
            button = QPushButton()
            button.setToolTip(oracle.value + " · " + labels[oracle.value])
            button.clicked.connect(lambda checked=False, index=index: self.oracle_combo.setCurrentIndex(index))
            self.oracle_buttons[oracle] = button
            overview.addWidget(button, index // 4, index % 4)
        layout.addLayout(overview)
        row = QHBoxLayout()
        self.oracle_combo = QComboBox()
        self.oracle_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.oracle_combo.setMinimumContentsLength(18)
        for oracle in OracleId:
            self.oracle_combo.addItem(f"{oracle.value} · {labels[oracle.value]} (0)", oracle)
        row.addWidget(self.oracle_combo, 1)
        self.refresh_button = QPushButton("再読込")
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("表示名"))
        self.label_edit = QLineEdit()
        self.label_edit.setMaxLength(40)
        self.label_edit.setAccessibleName("Oracleの表示名")
        self.label_save_button = QPushButton("表示名を保存")
        row.addWidget(self.label_edit, 1)
        row.addWidget(self.label_save_button)
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
        self.sample_list.setMinimumHeight(100)
        self.sample_list.setMaximumHeight(130)
        self.sample_list.setAccessibleName("登録済み Oracle サンプル")
        layout.addWidget(self.sample_list)
        row = QHBoxLayout()
        self.quality_button = QPushButton("Quality Check")
        self.play_button = QPushButton("Listen")
        self.stop_play_button = QPushButton("再生を停止")
        self.delete_button = QPushButton("Delete")
        self.restore_button = QPushButton("削除を戻す")
        for widget in (self.play_button, self.stop_play_button, self.quality_button, self.delete_button, self.restore_button):
            row.addWidget(widget)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.quality_label = QLabel()
        self.quality_label.setTextFormat(Qt.TextFormat.PlainText)
        self.quality_label.setWordWrap(True)
        self.quality_label.setAccessibleName("選択した保存音声の品質確認結果")
        self.quality_label.hide()
        layout.addWidget(self.quality_label)
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
        self.details_button = QPushButton("登録情報を表示")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self.details_label.setVisible)
        layout.addWidget(self.details_button)
        self.details_label.hide()
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
        self.label_save_button.clicked.connect(self._save_label)
        self.sample_list.currentRowChanged.connect(self._selection)
        self.volume_slider.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        for button, signal in (
            (self.import_button, self.import_requested), (self.record_button, self.record_requested),
            (self.cancel_button, self.cancel_requested), (self.play_button, self.play_requested),
            (self.stop_play_button, self.stop_play_requested), (self.delete_button, self.delete_requested),
            (self.restore_button, self.restore_requested), (self.refresh_button, self.refresh_requested),
            (self.quality_button, self.quality_requested),
        ):
            button.clicked.connect(signal.emit)
        self.set_labels(labels)
        self._update_controls()

    @property
    def oracle(self) -> OracleId:
        return OracleId(self.oracle_combo.currentData())

    @property
    def sample(self) -> TemplateSample | None:
        item = self.sample_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _save_label(self):
        label = self.label_edit.text().strip()
        if not label:
            self.message_label.setText("表示名を入力してください。")
            return
        self.label_changed.emit(self.oracle.value, label)

    def set_labels(self, labels):
        self._labels = dict(labels)
        self.oracle_combo.blockSignals(True)
        for index, oracle in enumerate(OracleId):
            count = sum(sample.metadata.oracle == oracle.value for sample in self._catalog.samples)
            label = self._labels[oracle.value]
            self.oracle_combo.setItemText(index, f"{oracle.value} · {label} ({count})")
            short = label[:8] + ("…" if len(label) > 8 else "")
            button = self.oracle_buttons[oracle]
            button.setText(f"{oracle.value} · {short} / {count}件")
            button.setToolTip(f"{oracle.value} · {label} / {count}件")
            button.setStyleSheet("color: #81d6b5;" if count else "color: #b9c4d5;")
        self.oracle_combo.blockSignals(False)
        missing = [oracle.value for oracle in OracleId if not any(s.metadata.oracle == oracle.value for s in self._catalog.samples)]
        self.readiness_label.setText(f"登録：{7-len(missing)}/7種類 · {len(self._catalog.samples)}サンプル"
                                    + (" / 未登録：" + ", ".join(missing) if missing else " / 7種類を比較できます"))
        self.label_edit.setText(self._labels[self.oracle.value])

    def begin_quality(self):
        self.quality_label.setText("品質を確認しています…")
        self.quality_label.setStyleSheet("")
        self.quality_label.show()

    def set_quality(self, report):
        if self.sample is None or self.sample.metadata.sample_id != report.sample_id:
            return
        status = "要確認" if report.notices or report.clipping else "警告なし"
        text = (f"品質確認：{status}\n"
                f"{report.sample_rate:,} Hz / {report.channels} ch / {report.duration_seconds:.3f} 秒\n"
                f"Peak: {report.peak:.6f} / RMS: {report.rms:.6f} / "
                f"クリッピング: {'あり' if report.clipping else 'なし'}\n")
        text += "\n".join(report.notices) if report.notices else "保存音声を再読込して確認しました。Oracleの正誤は未判定です。"
        self.quality_label.setText(text)
        self.quality_label.setStyleSheet("color: #eac16f;" if report.notices or report.clipping else "color: #81d6b5;")
        self.quality_label.show()

    def set_quality_error(self, message):
        self.quality_label.setText("品質確認：失敗\n" + message)
        self.quality_label.setStyleSheet("color: #f29aaa;")
        self.quality_label.show()

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
        self.set_labels(self._labels)
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
        self.quality_label.clear()
        self.quality_label.hide()
        self.label_edit.setText(self._labels[self.oracle.value])
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
                       self.import_button, self.refresh_button, self.volume_slider,
                       self.label_edit, self.label_save_button, self.details_button):
            widget.setEnabled(idle)
        self.record_button.setEnabled(idle and self._live)
        self.cancel_button.setEnabled(self._recording)
        self.play_button.setEnabled(idle and self.sample is not None)
        self.delete_button.setEnabled(idle and self.sample is not None)
        self.quality_button.setEnabled(idle and self.sample is not None)
        self.restore_button.setEnabled(idle and self._undo)
        self.stop_play_button.setEnabled(self._playing)
