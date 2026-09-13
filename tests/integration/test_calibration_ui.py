"""User operations, immutable Oracle target, persistence and asynchronous shutdown."""
from pathlib import Path
from threading import Event
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QFileDialog

from audio.device_manager import DeviceManager
from audio.operations import check_cancel
from audio.playback import AudioPlayer
from audio.waveio import write_wav
from config.schema import AppConfig
from encounter.vog_oracles import OracleId
from templates.manager import TemplateManager
from tests.fakes_audio import AudioFactory, FakeModule
from tests.integration.test_live_ui import qt_app, pump_until, cleanup
from tests.unit.test_templates import tone
from ui.audio_controller import AudioController
from ui.main_window import MainWindow
from ui.template_controller import TemplateController


def window_at(root, factory, settings=None, manager=None):
    templates = TemplateController(root / "templates", manager=manager, player=AudioPlayer(FakeModule, factory))
    window = MainWindow(root, settings=settings,
                        controller=AudioController(manager=DeviceManager(FakeModule, factory)),
                        templates=templates)
    window.show()
    try:
        pump_until(lambda: not templates.busy and window.live_page.state_label.text() == "STOPPED")
    except BaseException:
        cleanup(window, factory)
        raise
    window.tabs.setCurrentIndex(2)
    return window


@pytest.mark.gui
def test_import_metadata_play_delete_undo_and_app_restart(qt_app, tmp_path, monkeypatch):
    factory = AudioFactory()
    root = tmp_path / "data"
    settings = AppConfig()
    settings.oracle_labels["L2"] = "CUSTOM 左奥"
    a, b = write_wav(tmp_path / "A.wav", tone()), write_wav(tmp_path / "B.wav", tone())
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(a), str(b)], "WAV"))
    window = window_at(root, factory, settings)
    page = window.calibration_page
    threads = []
    real = page.set_catalog
    def catalog(*args):
        threads.append(QThread.currentThread())
        real(*args)
    page.set_catalog = catalog
    try:
        assert not page.record_button.isEnabled()
        page.oracle_combo.setCurrentIndex(1)
        assert page.oracle == OracleId.L2 and "CUSTOM" in page.oracle_combo.currentText()
        page.import_button.click()
        assert not page.oracle_combo.isEnabled()
        pump_until(lambda: not window.templates.busy and page.sample_list.count() == 2)
        assert "Peak:" in page.details_label.text() and "44,100 Hz / 2 ch" in page.details_label.text()
        assert page.sample.metadata.oracle == "L2"
        page.play_button.click()
        assert page.stop_play_button.isEnabled()
        pump_until(lambda: not window.templates.busy)
        assert factory.streams[-1].closed
        sample_id = page.sample.metadata.sample_id
        page.delete_button.click()
        pump_until(lambda: not window.templates.busy and page.sample_list.count() == 1)
        assert page.restore_button.isEnabled()
        page.restore_button.click()
        pump_until(lambda: not window.templates.busy and page.sample_list.count() == 2)
        assert page.sample.metadata.sample_id == sample_id
        assert not page.restore_button.isEnabled()
        assert threads and all(t == qt_app.thread() for t in threads)
    finally:
        cleanup(window, factory)
    restarted = window_at(root, factory, settings)
    try:
        restarted.calibration_page.oracle_combo.setCurrentIndex(1)
        assert restarted.calibration_page.sample_list.count() == 2
    finally:
        cleanup(restarted, factory)


@pytest.mark.gui
def test_batch_import_partial_success_and_silence_retry(qt_app, tmp_path, monkeypatch):
    factory = AudioFactory()
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"broken")
    good = write_wav(tmp_path / "good.wav", tone())
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(bad), str(good)], "WAV"))
    window = window_at(tmp_path / "data", factory)
    try:
        page = window.calibration_page
        page.import_button.click()
        pump_until(lambda: not window.templates.busy and page.sample_list.count() == 1)
        assert "1 件" in page.message_label.text() and "bad.wav" in page.message_label.text()
        assert page.import_button.isEnabled()
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_live_record_and_cancel_does_not_change_oracle(qt_app, tmp_path):
    factory = AudioFactory()
    factory.tone = True
    window = window_at(tmp_path / "data", factory)
    page = window.calibration_page
    try:
        window.live_page.start_button.click()
        pump_until(lambda: page.record_button.isEnabled())
        page.oracle_combo.setCurrentIndex(3)
        page.duration_spin.setValue(0.1)
        page.record_button.click()
        assert not page.oracle_combo.isEnabled() and page.cancel_button.isEnabled()
        pump_until(lambda: page.sample_list.count() == 1 and not window.templates.busy)
        assert page.sample.metadata.oracle == "MID" and page.sample.metadata.source == "recorded"
        assert page.sample.metadata.original_frames == 4800 and page.progress_bar.value() == 1000
        page.duration_spin.setValue(5)
        page.record_button.click()
        page.cancel_button.click()
        pump_until(lambda: page.record_button.isEnabled())
        assert page.sample_list.count() == 1 and "中止" in page.message_label.text()
        assert window.live_page.state_label.text() == "LIVE"
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_close_during_import_is_responsive_and_keeps_existing(qt_app, tmp_path, monkeypatch):
    entered = Event()
    class SlowManager(TemplateManager):
        def import_wav(self, oracle, path, cancel=None):
            entered.set()
            while not cancel.wait(0.01):
                pass
            check_cancel(cancel)
    root = tmp_path / "data"
    manager = SlowManager(root / "templates")
    saved = manager.add("L1", tone())
    factory = AudioFactory()
    window = window_at(root, factory, manager=manager)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a: ([str(tmp_path / "slow.wav")], "WAV"))
    try:
        window.calibration_page.import_button.click()
        pump_until(entered.is_set)
        before = time.monotonic()
        window.close()
        assert time.monotonic() - before < 0.1
        pump_until(lambda: not window.isVisible() and not window.templates.is_alive)
        assert not window.controller.service.is_alive and saved.path.exists()
        assert len(TemplateManager(manager.root).catalog().samples) == 1
    finally:
        cleanup(window, factory)
