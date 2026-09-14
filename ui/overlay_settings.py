"""Overlay controls stay in the main app, reachable while overlay input passes through."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)


class OverlaySettingsDialog(QDialog):
    settings_changed = Signal(object)
    drag_requested = Signal(bool)
    map_edit_requested = Signal(bool)
    map_reset_requested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overlayとマップの設定")
        self.setMinimumWidth(430)
        self._syncing = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.enabled_checkbox = QCheckBox("ゲーム上にOverlayを表示")
        form.addRow(self.enabled_checkbox)
        self.click_through_checkbox = QCheckBox("通常時はクリック透過")
        self.click_through_checkbox.setToolTip("ONなら背後のゲームをそのまま操作できます。")
        form.addRow(self.click_through_checkbox)
        self.drag_checkbox = QCheckBox("位置をドラッグで調整（クリック透過OFF）")
        form.addRow(self.drag_checkbox)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("順序", "sequence")
        self.mode_combo.addItem("Oracle Map", "map")
        form.addRow("表示", self.mode_combo)
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(.05, 1)
        self.opacity_spin.setSingleStep(.05)
        form.addRow("不透明度", self.opacity_spin)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(.25, 4)
        self.scale_spin.setSingleStep(.1)
        form.addRow("倍率", self.scale_spin)
        self.width_spin, self.height_spin, self.font_spin = QSpinBox(), QSpinBox(), QSpinBox()
        self.width_spin.setRange(120, 3840)
        self.height_spin.setRange(60, 2160)
        self.font_spin.setRange(8, 160)
        form.addRow("基準幅", self.width_spin)
        form.addRow("基準高さ", self.height_spin)
        form.addRow("文字サイズ", self.font_spin)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.preset_buttons = {}
        for label, values in (
            ("小", {"width": 500, "height": 112, "font_size": 22, "scale": 1.0}),
            ("標準", {"width": 660, "height": 130, "font_size": 28, "scale": 1.0}),
            ("大", {"width": 900, "height": 165, "font_size": 36, "scale": 1.0}),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, values=values: self.settings_changed.emit(values))
            self.preset_buttons[label] = button
            row.addWidget(button)
        layout.addLayout(row)
        note = QLabel("位置調整が終わったらチェックを外してください。位置・表示設定は保存します。画面外の位置は起動時に補正します。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.map_edit_checkbox = QCheckBox("Live画面のOracle配置をドラッグで調整")
        layout.addWidget(self.map_edit_checkbox)
        self.map_reset_button = QPushButton("Oracle配置を初期値に戻す")
        layout.addWidget(self.map_reset_button)
        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_buttons.rejected.connect(self.close)
        layout.addWidget(close_buttons)
        self.enabled_checkbox.toggled.connect(lambda value: self._change("enabled", value))
        self.click_through_checkbox.toggled.connect(lambda value: self._change("click_through", value))
        self.mode_combo.currentIndexChanged.connect(lambda: self._change("mode", self.mode_combo.currentData()))
        for widget, key in ((self.opacity_spin, "opacity"), (self.scale_spin, "scale"),
                            (self.width_spin, "width"), (self.height_spin, "height"), (self.font_spin, "font_size")):
            widget.valueChanged.connect(lambda value, key=key: self._change(key, value))
        self.drag_checkbox.toggled.connect(lambda value: self.drag_requested.emit(value) if not self._syncing else None)
        self.map_edit_checkbox.toggled.connect(self.map_edit_requested)
        self.map_reset_button.clicked.connect(self.map_reset_requested)
        self.sync(settings)

    def _change(self, key, value):
        if not self._syncing:
            self.settings_changed.emit({key: value})

    def sync(self, settings, drag_mode=False):
        self._syncing = True
        self.enabled_checkbox.setChecked(settings.enabled)
        self.click_through_checkbox.setChecked(settings.click_through)
        self.drag_checkbox.setChecked(drag_mode)
        self.drag_checkbox.setEnabled(settings.enabled)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(settings.mode))
        for widget, value in ((self.opacity_spin, settings.opacity), (self.scale_spin, settings.scale),
                              (self.width_spin, settings.width), (self.height_spin, settings.height),
                              (self.font_spin, settings.font_size)):
            widget.setValue(value)
        self._syncing = False

    def closeEvent(self, event):
        self.drag_checkbox.setChecked(False)
        self.map_edit_checkbox.setChecked(False)
        super().closeEvent(event)
