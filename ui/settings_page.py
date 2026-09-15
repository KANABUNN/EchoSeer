"""Editable settings draft; only changed fields are merged into the latest config."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QPushButton, QCheckBox, QDoubleSpinBox, QSpinBox, QComboBox, QKeySequenceEdit,
)

from config.schema import AppConfig
from config.hotkeys import ACTIONS, ACTION_LABELS


class PreciseSpinBox(QDoubleSpinBox):
    def textFromValue(self, value):
        return format(value, ".6f").rstrip("0").rstrip(".") or "0"


class SettingsPage(QWidget):
    apply_requested = Signal()
    overlay_requested = Signal()

    def __init__(self, settings):
        super().__init__()
        self.controls, self.changed = {}, set()
        self._syncing = False
        self._idle = True
        layout = QVBoxLayout(self)
        self.message_label = QLabel("変更後に「保存して適用」を押してください。適用はStop後に行います。")
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        basic = self._group(layout, "音声とRound")
        self._number(basic, "audio.buffer_duration", "保存バッファ（秒）", 5, 30)
        self._check(basic, "audio.auto_reconnect", "切断時に同じデバイスへ自動再接続")
        self._number(basic, "audio.reconnect_interval", "再接続の間隔（秒）", .25, 60)
        self._check(basic, "sequence.auto_advance", "確定後、待機時間と無音を確認して次のRoundへ")
        self._number(basic, "sequence.lockout_duration", "確定後の待機時間（秒）", .01, 120)
        self._number(basic, "sequence.silence_duration", "次のRoundに必要な無音（秒）", .01, 120)

        logs = self._group(layout, "使用記録")
        self._check(logs, "logging.event_logs", "判定をログに保存")
        self._check(logs, "logging.uncertain_audio", "unknown・不一致の音声を保存")
        self._check(logs, "logging.success_audio", "採用した音声も保存（測定用・容量が増えます）")

        hotkeys = self._group(layout, "Hotkey")
        self._check(hotkeys, "hotkeys.enabled", "Global Hotkeyを有効にする（初期状態は無効）")
        note = QLabel("Ctrl・Alt・Shift＋英字・数字・Fキーを指定します。空欄の操作は無効です。編集中は割り当てを一時解除します。")
        note.setWordWrap(True)
        hotkeys.addRow(note)
        for action in ACTIONS:
            editor = QKeySequenceEdit()
            editor.setMaximumSequenceLength(1)
            editor.setClearButtonEnabled(True)
            editor.setAccessibleName(ACTION_LABELS[action] + "のHotkey")
            self._add(hotkeys, "hotkeys." + action, ACTION_LABELS[action], editor)
        self.hotkey_status = QLabel()
        self.hotkey_status.setTextFormat(Qt.TextFormat.PlainText)
        self.hotkey_status.setWordWrap(True)
        hotkeys.addRow(self.hotkey_status)

        self.advanced_button = QPushButton("認識・提示間隔の調整を表示")
        self.advanced_button.setCheckable(True)
        layout.addWidget(self.advanced_button)
        self.advanced = QWidget()
        advanced_layout = QVBoxLayout(self.advanced)
        recognition = self._group(advanced_layout, "認識条件")
        recognition_note = QLabel(
            "解析候補の音量は入力の背景レベルへ自動追従します。比較直前の正規化は"
            "解析用コピーだけに適用し、録音・保存・再生用の元音声は変更しません。"
        )
        recognition_note.setTextFormat(Qt.TextFormat.PlainText)
        recognition_note.setWordWrap(True)
        recognition.addRow(recognition_note)
        self._number(recognition, "audio.internal_sample_rate", "内部サンプルレート（Hz）", 8000, 192000, integer=True)
        labels = {
            "waveform_weight": "波形の重み", "spectrum_weight": "スペクトルの重み",
            "detection_threshold": "基準検出しきい値（入力音量へ自動追従）", "confidence_threshold": "採用スコア",
            "high_confidence_threshold": "高信頼スコア", "low_score_threshold": "低スコアの境界",
            "margin_threshold": "採用に必要な候補差", "high_margin_threshold": "高信頼に必要な候補差",
            "duplicate_cooldown": "重複を抑制する時間（秒）",
            "bandpass_low_hz": "帯域の下限（Hz）", "bandpass_high_hz": "帯域の上限（Hz）",
            "top_n": "平均に使うサンプル数",
        }
        for name, label in labels.items():
            bounds = (1, 20) if name == "top_n" else (
                (1, 95999) if name.startswith("bandpass_") else ((0, 10) if name == "duplicate_cooldown" else (0, 1))
            )
            self._number(recognition, "recognition." + name, label, *bounds, integer=name == "top_n")
        self._check(recognition, "recognition.bandpass_enabled", "帯域を制限する")
        combo = QComboBox()
        combo.addItem("最も一致したサンプル", "best")
        combo.addItem("上位サンプルの平均", "top_n_mean")
        self._add(recognition, "recognition.template_aggregation", "サンプルの集約", combo)
        sequence = self._group(advanced_layout, "提示と推定")
        for name, label, low, high, integer in (
            ("pass_gap", "2回の提示の最小間隔（秒）", .01, 120, False),
            ("event_timeout", "次の音を待つ時間（秒）", .01, 120, False),
            ("candidate_top_n", "推定に使う候補数", 1, 7, True),
            ("max_corrections", "補正の上限", 0, 7, True),
            ("inference_margin", "推定に必要な信頼度差", 0, 1, False),
            ("reconstruction_margin", "補正案に必要な差", 0, 1, False),
        ):
            self._number(sequence, "sequence." + name, label, low, high, integer=integer)
        self.advanced.hide()
        layout.addWidget(self.advanced)
        self.advanced_button.toggled.connect(self.advanced.setVisible)
        overlay_button = QPushButton("Overlayの大きさ・位置・表示を調整")
        overlay_button.clicked.connect(self.overlay_requested)
        layout.addWidget(overlay_button)
        row = QHBoxLayout()
        self.apply_button = QPushButton("保存して適用")
        self.apply_button.setObjectName("primaryButton")
        self.revert_button = QPushButton("変更を取り消す")
        self.defaults_button = QPushButton("設定項目を初期値に戻す")
        for widget in (self.apply_button, self.revert_button, self.defaults_button):
            row.addWidget(widget)
        layout.addLayout(row)
        layout.addStretch()
        self.apply_button.clicked.connect(self.apply_requested)
        self.revert_button.clicked.connect(lambda: self.load(self._current))
        self.defaults_button.clicked.connect(self._defaults)
        for key, other in (("waveform_weight", "spectrum_weight"), ("spectrum_weight", "waveform_weight")):
            self.controls["recognition." + key].valueChanged.connect(
                lambda value, other=other: self._pair_weight(other, value)
            )
        self.load(settings)

    @property
    def dirty(self):
        return bool(self.changed)

    def _group(self, layout, title):
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addWidget(group)
        return form

    def _add(self, form, path, label, widget):
        widget.setAccessibleName(label)
        self.controls[path] = widget
        form.addRow(widget) if isinstance(widget, QCheckBox) else form.addRow(label, widget)
        if isinstance(widget, QCheckBox):
            signal = widget.toggled
        elif isinstance(widget, QKeySequenceEdit):
            signal = widget.keySequenceChanged
        elif isinstance(widget, QComboBox):
            signal = widget.currentIndexChanged
        else:
            signal = widget.valueChanged
        signal.connect(lambda *args, path=path: self._edit(path))

    def _check(self, form, path, label):
        self._add(form, path, label, QCheckBox(label))

    def _number(self, form, path, label, low, high, integer=False):
        widget = QSpinBox() if integer else PreciseSpinBox()
        if not integer:
            widget.setDecimals(6)
            widget.setSingleStep(.01 if high <= 1 else .1)
        widget.setRange(low, high)
        self._add(form, path, label, widget)

    def _value(self, widget):
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QKeySequenceEdit):
            return widget.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if isinstance(widget, QComboBox):
            return widget.currentData()
        return widget.value()

    def _set(self, widget, value):
        if isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, QKeySequenceEdit):
            widget.setKeySequence(QKeySequence(value))
        elif isinstance(widget, QComboBox):
            widget.setCurrentIndex(widget.findData(value))
        else:
            widget.setValue(value)

    def _edit(self, path):
        if self._syncing:
            return
        widget = self.controls[path]
        if self._value(widget) == self._baseline[path]:
            self.changed.discard(path)
        else:
            self.changed.add(path)
        self.show_message("未保存の変更があります。Stop後に保存して適用してください。" if self.dirty else "保存した設定を表示しています。")
        self._buttons()

    def _pair_weight(self, other, value):
        if self._syncing:
            return
        widget = self.controls["recognition." + other]
        widget.blockSignals(True)
        widget.setValue(1 - value)
        widget.blockSignals(False)
        self._edit("recognition." + other)

    def load(self, settings):
        self._current = AppConfig.from_dict(settings.to_dict())
        self._syncing = True
        self._baseline = {}
        for path, widget in self.controls.items():
            group, key = path.split(".")
            self._set(widget, getattr(getattr(settings, group), key))
            self._baseline[path] = self._value(widget)
        self._syncing = False
        self.changed.clear()
        self.show_message("保存した設定を表示しています。適用はStop後に行います。")
        self._buttons()

    def sync_current(self, settings):
        if not self.dirty:
            self.load(settings)
        else:
            self._current = AppConfig.from_dict(settings.to_dict())

    def proposal(self, current):
        payload = current.to_dict()
        for path in self.changed:
            group, key = path.split(".")
            payload[group][key] = self._value(self.controls[path])
        return AppConfig.from_dict(payload)

    def _defaults(self):
        defaults = AppConfig()
        for path, widget in self.controls.items():
            group, key = path.split(".")
            self._set(widget, getattr(getattr(defaults, group), key))
            self._edit(path)

    def set_idle(self, idle):
        self._idle = idle
        self._buttons()
        self.apply_button.setToolTip("" if idle else "Stopして、Replay・Calibrationの処理が終わるまで待ってください。")

    def _buttons(self):
        self.apply_button.setEnabled(self._idle and self.dirty)
        self.revert_button.setEnabled(self.dirty)

    def show_message(self, message):
        self.message_label.setText(message)
