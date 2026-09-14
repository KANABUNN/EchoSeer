"""Overlay shares live evidence and returns to input transparency after adjustment."""
from dataclasses import replace
import json

import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from config.schema import OverlaySettings
from ui.oracle_map import node_rects
from ui.overlay import OverlayWindow
from ui.presentation import present
from tests.integration.test_live_ui import qt_app, make_window, cleanup
from tests.integration.test_live_recognition_ui import make_live, close_live
from tests.fakes_audio import AudioFactory
from tests.unit.test_streaming_live import round_audio
from tests.integration.test_live_ui import pump_until


@pytest.mark.gui
def test_overlay_controls_persist_and_adjustment_closes_safely(qt_app, tmp_path):
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    try:
        dashboard, overlay, dialog = window.live_page.dashboard, window.overlay, window.overlay_dialog
        assert not overlay.isVisible()
        dashboard.overlay_button.click()
        assert overlay.isVisible()
        assert overlay.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dashboard.overlay_settings_button.click()
        dialog.opacity_spin.setValue(.65)
        dialog.scale_spin.setValue(1.5)
        dialog.mode_combo.setCurrentIndex(1)
        assert window.settings.overlay.mode == "map"
        assert overlay.windowOpacity() == pytest.approx(.65, abs=.01)
        dialog.drag_checkbox.setChecked(True)
        assert overlay.drag_mode
        assert not overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
        dialog.map_edit_checkbox.setChecked(True)
        assert dashboard.oracle_map.editing
        dialog.close()
        assert not overlay.drag_mode and not dashboard.oracle_map.editing
        assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
        config = json.loads((window.data_root / "config.json").read_text(encoding="utf-8"))
        assert config["overlay"]["enabled"] and config["overlay"]["opacity"] == .65
        assert config["overlay"]["mode"] == "map" and config["overlay"]["scale"] == 1.5
        overlay.close()
        assert not dashboard.overlay_button.isChecked() and not window.settings.overlay.enabled
    finally:
        cleanup(window, factory)


@pytest.mark.gui
@pytest.mark.parametrize("status", ["CONFIRMED", "INFERRED", "MISMATCH", "CHECK"])
def test_overlay_uses_live_result_and_reset_clears_it(qt_app, tmp_path, status):
    window, factory = make_live(tmp_path, status)
    try:
        window.live_page.dashboard.overlay_button.click()
        window.controller.service.buffer.write(round_audio())
        pump_until(lambda: window.live_page.dashboard.result.verification is not None)
        view = window.overlay.presentation
        assert view.status == status
        assert ("確定順" in view.sequence_text) == (status == "CONFIRMED")
        assert ("推定順（要確認）" in view.sequence_text) == (status == "INFERRED")
        assert window.overlay.grab().isNull() is False
        window.live_page.dashboard.reset_button.click()
        assert not window.overlay.presentation.sequence
        assert "確定順" not in window.overlay.presentation.sequence_text
    finally:
        close_live(window)


@pytest.mark.gui
def test_map_position_drag_and_default_reset_are_saved(qt_app, tmp_path):
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    try:
        widget = window.live_page.dashboard.oracle_map
        window.live_page.dashboard.overlay_settings_button.click()
        window.overlay_dialog.map_edit_checkbox.setChecked(True)
        old = widget.positions["L2"].x
        rects = node_rects(QRectF(widget.rect()), widget.positions, min(1, widget.height() / 200))
        center = rects[next(oracle for oracle in rects if oracle.value == "L2")].center().toPoint()
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(widget, center + QPoint(35, 15))
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=center + QPoint(35, 15))
        assert widget.positions["L2"].x > old
        assert window.overlay.positions["L2"].x == widget.positions["L2"].x
        config = json.loads((window.data_root / "config.json").read_text(encoding="utf-8"))
        assert config["oracle_map_positions"]["L2"]["x"] == widget.positions["L2"].x
        window.overlay_dialog.map_reset_button.click()
        assert widget.positions["L2"].x == old
    finally:
        cleanup(window, factory)


@pytest.mark.gui
@pytest.mark.parametrize("mode", ["sequence", "map"])
def test_overlay_scale_and_offscreen_restore_fit_available_monitor(qt_app, mode):
    overlay = OverlayWindow(OverlaySettings(enabled=True, x=99999, y=-99999,
                            width=3840, height=2160, scale=4, font_size=160, mode=mode))
    try:
        assert any(screen.availableGeometry().contains(overlay.frameGeometry()) for screen in QApplication.screens())
        assert not overlay.grab().isNull()
        overlay.apply_settings(OverlaySettings(enabled=True, width=120, height=60, scale=.25, mode=mode))
        assert overlay.width() >= (320 if mode == "map" else 240)
        assert overlay.height() >= (320 if mode == "map" else 96)
    finally:
        overlay.close()


@pytest.mark.gui
def test_overlay_corners_remain_transparent_with_main_window_theme(qt_app, tmp_path):
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    try:
        window.live_page.dashboard.overlay_button.click()
        image = window.overlay.grab().toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        assert 0 < image.pixelColor(30, 40).alpha() < 255
    finally:
        cleanup(window, factory)
