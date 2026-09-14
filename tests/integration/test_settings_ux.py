"""Settings merge/apply/rollback, hotkey focus ownership, and clear round controls."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from audio.data import AudioClip
from audio.device_manager import DeviceManager
from audio.waveio import write_wav, read_wav
from config.schema import AppConfig
from config.hotkeys import BASE_ID
from ui.audio_controller import AudioController
from ui.main_window import MainWindow
from ui.hotkey_controller import _Filter
from encounter.vog_oracles import OracleId
from tests.fakes_audio import AudioFactory, FakeModule
from tests.fakes_hotkeys import FakeHotkeyBackend
from tests.integration.test_live_ui import qt_app, pump_until, cleanup


def window_for(tmp_path):
    factory = AudioFactory()
    factory.no_data = True
    backend = FakeHotkeyBackend()
    controller = AudioController(manager=DeviceManager(FakeModule, factory))
    window = MainWindow(tmp_path / "data", controller=controller, hotkey_backend=backend)
    window.show()
    pump_until(lambda: window.live_page._state == "STOPPED" and not window.templates.busy)
    return window, factory, backend


@pytest.mark.gui
def test_settings_preserve_latest_overlay_labels_and_unchanged_float_precision(qt_app, tmp_path):
    window, factory, backend = window_for(tmp_path)
    try:
        page = window.settings_page
        assert not window.settings.hotkeys.enabled and not backend.calls
        window.settings.recognition.duplicate_cooldown = .4500001234
        page.load(window.settings)
        page.controls["audio.buffer_duration"].setValue(30)
        window._update_overlay({"width": 880, "font_size": 36})
        window._save_oracle_label("L1", "左の一番手前")
        assert page.dirty
        assert window._apply_settings()
        assert window.settings.audio.buffer_duration == 30
        assert window.settings.overlay.width == 880
        assert window.settings.oracle_labels["L1"] == "左の一番手前"
        assert window.settings.recognition.duplicate_cooldown == .4500001234
        assert not backend.calls and not page.dirty
        saved = AppConfig.from_dict(json.loads(window.config_manager.path.read_text(encoding="utf-8")))
        assert saved.to_dict() == window.settings.to_dict()
        page.controls["sequence.pass_gap"].setValue(4)
        page.revert_button.click()
        assert not page.dirty and page.controls["sequence.pass_gap"].value() == 2
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_invalid_thresholds_leave_saved_and_runtime_settings_unchanged(qt_app, tmp_path):
    window, factory, backend = window_for(tmp_path)
    try:
        window._save_config()
        before = window.config_manager.path.read_bytes()
        page = window.settings_page
        page.controls["recognition.confidence_threshold"].setValue(.4)
        assert not window._apply_settings()
        assert "低スコア" in page.message_label.text()
        assert window.settings.recognition.confidence_threshold == .82
        assert window.config_manager.path.read_bytes() == before
        assert page.dirty and not backend.entries
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_save_failure_restores_old_hotkeys_and_keeps_dirty_draft(qt_app, tmp_path, monkeypatch):
    window, factory, backend = window_for(tmp_path)
    try:
        page = window.settings_page
        page.controls["hotkeys.enabled"].setChecked(True)
        assert window._apply_settings()
        original = dict(backend.entries)
        before = window.config_manager.path.read_bytes()
        page.controls["audio.buffer_duration"].setValue(20)
        page.controls["hotkeys.reset"].setKeySequence(QKeySequence("Ctrl+Alt+R"))
        def fail_save(*args):
            raise OSError("simulated-disk-full")
        monkeypatch.setattr(window.config_manager, "save", fail_save)
        assert not window._apply_settings()
        assert "元の設定" in page.message_label.text()
        assert backend.entries == original
        assert window.settings.audio.buffer_duration == 10
        assert window.settings.hotkeys.reset == "Ctrl+Alt+F8"
        assert window.config_manager.path.read_bytes() == before and page.dirty
    finally:
        cleanup(window, factory)
    assert not backend.entries


@pytest.mark.gui
def test_hotkey_conflict_rolls_back_without_saving_candidate(qt_app, tmp_path):
    window, factory, backend = window_for(tmp_path)
    try:
        page = window.settings_page
        page.controls["hotkeys.enabled"].setChecked(True)
        assert window._apply_settings()
        before = window.config_manager.path.read_bytes()
        original = dict(backend.entries)
        backend.reserved.add((3, ord("R")))
        page.controls["hotkeys.reset"].setKeySequence(QKeySequence("Ctrl+Alt+R"))
        assert not window._apply_settings()
        assert "重複" in page.message_label.text()
        assert backend.entries == original and window.config_manager.path.read_bytes() == before
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_hotkeys_suspend_for_editor_ignore_stale_messages_and_dispatch_actions(qt_app, tmp_path):
    window, factory, backend = window_for(tmp_path)
    actions = []
    try:
        page = window.settings_page
        page.controls["hotkeys.enabled"].setChecked(True)
        assert window._apply_settings()
        editor = page.controls["hotkeys.next_round"]
        window.hotkeys._focus_changed(None, editor)
        assert window.hotkeys.suspended and not backend.entries
        window.hotkeys._focus_changed(editor, window.live_page.start_button)
        assert not window.hotkeys.suspended and len(backend.entries) == 4
        window.hotkeys.activated.connect(actions.append)
        msg = wintypes.MSG()
        msg.message, msg.wParam, msg.lParam = 0x0312, BASE_ID + 2, (0x78 << 16) | 3
        native = _Filter(window.hotkeys)
        assert native.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg)) == (True, 0)
        assert actions == ["next_round"] and window.live_page.dashboard.round_index == 2
        msg.lParam = (ord("N") << 16) | 3
        assert native.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg)) == (False, 0)
        assert actions == ["next_round"]
        window.hotkeys.activated.emit("toggle_capture")
        pump_until(lambda: window.live_page._state == "LIVE")
        assert not window._settings_idle()
        candidate = AppConfig.from_dict(window.settings.to_dict())
        candidate.audio.buffer_duration = 30
        assert not window._apply_settings_config(candidate)
        window.hotkeys.activated.emit("reset")
        assert window.live_page.dashboard.round_index == 1
        window.hotkeys.activated.emit("toggle_capture")
        pump_until(lambda: window.live_page._state == "STOPPED")
    finally:
        cleanup(window, factory)
    assert not backend.entries and window.hotkeys.closed


@pytest.mark.gui
def test_round_retry_reset_auto_policy_and_overlay_presets(qt_app, tmp_path):
    window, factory, backend = window_for(tmp_path)
    try:
        dashboard = window.live_page.dashboard
        window._select_live_round(4)
        generation = window.recognizer.generation
        dashboard.retry_button.click()
        assert dashboard.round_index == 4 and window.recognizer.generation > generation
        dashboard.previous_button.click()
        assert dashboard.round_index == 3
        dashboard.reset_button.click()
        assert dashboard.round_index == 1
        dashboard.auto_checkbox.setChecked(False)
        assert not window.settings.sequence.auto_advance
        assert not window.recognizer.sequence.auto_advance
        assert not window.operations.sequence_settings.auto_advance
        for label, width in (("小", 500), ("標準", 660), ("大", 900)):
            window.overlay_dialog.preset_buttons[label].click()
            assert window.settings.overlay.width == width
        assert "未登録" in window.live_page.readiness_label.text()
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_internal_rate_change_reuses_native_templates_and_records_new_conditions(qt_app, tmp_path):
    t = np.arange(24000) / 48000
    wave = tmp_path / "tone.wav"
    write_wav(wave, AudioClip((.2 * np.sin(2 * np.pi * 523 * t)).astype(np.float32), 48000))
    window, factory, backend = window_for(tmp_path)
    try:
        sample = window.templates.manager.import_wav(OracleId.L1, wave)
        before = {p: p.read_bytes() for p in sample.directory.rglob("*") if p.is_file()}
        page = window.settings_page
        page.controls["audio.internal_sample_rate"].setValue(96000)
        page.controls["recognition.waveform_weight"].setValue(.7)
        assert page.controls["recognition.spectrum_weight"].value() == pytest.approx(.3)
        assert window._apply_settings()
        assert window.operations.analyzer.sample_rate == 96000
        assert window.templates.manager.analyzer.sample_rate == 96000
        assert window.operations.classifier.sample_rate == 96000
        actual = window.operations.classifier.classify(read_wav(wave))
        assert actual.best_candidate == OracleId.L1
        assert actual.best_score == pytest.approx(1, abs=1e-5)
        assert window.recognizer.recognition.waveform_weight == .7
        assert window.operations.recorder.conditions["sample_rate"] == 96000
        assert window.operations.recorder.conditions["sequence"]["auto_advance"] is True
        assert all(path.read_bytes() == data for path, data in before.items())
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_terminal_audio_error_clears_live_and_overlay_result(qt_app, tmp_path):
    from audio.service import CaptureStatus
    from encounter.sequence import SequenceEngine
    from tests.unit.test_pass_comparator import passes
    from ui.presentation import present
    window, factory, backend = window_for(tmp_path)
    try:
        window.live_page.start_button.click()
        pump_until(lambda: window.live_page._state == "LIVE")
        engine = SequenceEngine()
        engine.arm(0, 1)
        first, second = passes(list(OracleId)[:3])
        for entry in (*first, *second):
            engine.add(entry)
        confirmed = engine.verify()
        window.live_page.dashboard.set_result(confirmed)
        window.overlay.set_result(present(confirmed))
        generation = window.recognizer.generation
        window._on_audio_event(CaptureStatus("CLOSED", "音声取得でエラーが発生しました。アプリを再起動してください。"))
        assert not window._capture_live
        assert window.recognizer.generation > generation
        assert not window.live_page.dashboard.result.confirmed
        assert not window.live_page.dashboard.result.pass1
        assert not window.overlay.presentation.sequence
        assert "再起動" in window.live_page.message_label.text()
        assert not window.live_page.start_button.isEnabled()
    finally:
        cleanup(window, factory)
