"""Qt main-thread updates, settings persistence and responsive shutdown."""

import json
import os
from pathlib import Path
from threading import Event
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from audio.device_manager import DeviceManager
from config.schema import AppConfig, DeviceIdentity
from ui.audio_controller import AudioController
from ui.main_window import MainWindow
from tests.fakes_audio import AudioFactory, FakeModule


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.processEvents()


def pump_until(predicate, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "Qt condition did not become true"


def make_window(tmp_path: Path, factory: AudioFactory, settings: AppConfig | None = None):
    controller = AudioController(manager=DeviceManager(FakeModule, factory))
    window = MainWindow(tmp_path / "user-data", settings=settings, controller=controller)
    window.show()
    pump_until(lambda: window.live_page.state_label.text() == "STOPPED")
    return window


def cleanup(window: MainWindow, factory: AudioFactory) -> None:
    if factory.open_gate:
        factory.open_gate.set()
    assert window.controller.shutdown(2)
    window.close()
    QApplication.processEvents()
    assert not window.controller.service.is_alive


@pytest.mark.gui
def test_live_buttons_update_on_main_thread_and_save_device_identity(qt_app, tmp_path: Path) -> None:
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    page = window.live_page
    threads = []
    original = page.set_level
    def record_thread(level):
        threads.append(QThread.currentThread())
        original(level)
    page.set_level = record_thread
    try:
        assert not factory.streams
        for _ in range(3):
            assert page.start_button.isEnabled()
            page.start_button.click()
            pump_until(lambda: page.state_label.text() == "LIVE" and page.level_meter.value() > 0)
            assert not page.device_combo.isEnabled()
            page.stop_button.click()
            pump_until(lambda: page.state_label.text() == "STOPPED")
            assert page.level_meter.value() == 0
        page.backend_combo.setCurrentIndex(1)
        assert page.selected_device.name == "VoiceMeeter Output"
        page.start_button.click()
        pump_until(lambda: page.state_label.text() == "LIVE")
        assert "44,100 Hz" in page.format_label.text()
        saved = json.loads((window.data_root / "config.json").read_text(encoding="utf-8"))
        assert saved["audio"]["backend"] == "input_device"
        assert saved["audio"]["device"]["loopback"] is False
        assert saved["audio"]["device"]["native_sample_rate"] == 44100
        assert not {"index", "device_index"} & saved["audio"]["device"].keys()
        assert threads and all(thread == qt_app.thread() for thread in threads)
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_missing_saved_device_requires_selection_without_fallback(qt_app, tmp_path: Path) -> None:
    factory = AudioFactory()
    settings = AppConfig()
    settings.audio.device = DeviceIdentity(device_name="Disconnected headphones")
    window = make_window(tmp_path, factory, settings)
    try:
        assert window.live_page.selected_device is None
        assert not window.live_page.start_button.isEnabled()
        assert "保存された音声デバイス" in window.live_page.message_label.text()
        assert not factory.streams
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_open_error_is_displayed_and_retry_works(qt_app, tmp_path: Path) -> None:
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    try:
        factory.fail_open = True
        window.live_page.start_button.click()
        pump_until(lambda: window.live_page.state_label.text() == "ERROR")
        assert "Device unavailable" in window.live_page.message_label.text()
        assert window.live_page.start_button.isEnabled()
        factory.fail_open = False
        window.live_page.start_button.click()
        pump_until(lambda: window.live_page.state_label.text() == "LIVE")
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_close_during_pending_open_does_not_block_the_gui(qt_app, tmp_path: Path) -> None:
    factory = AudioFactory()
    factory.open_gate = Event()
    window = make_window(tmp_path, factory)
    try:
        window.live_page.start_button.click()
        pump_until(factory.open_entered.is_set)
        before = time.monotonic()
        window.close()
        assert time.monotonic() - before < 0.1
        assert window.isVisible()
        factory.open_gate.set()
        pump_until(lambda: not window.isVisible())
        assert not window.controller.service.is_alive
        assert all(stream.closed for stream in factory.streams)
        assert all(stream.thread is None for stream in factory.streams)
    finally:
        cleanup(window, factory)


@pytest.mark.gui
def test_small_window_keeps_capture_controls_readable_by_scrolling(qt_app, tmp_path: Path) -> None:
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    try:
        window.resize(760, 600)
        qt_app.processEvents()
        page = window.live_page
        assert window.live_scroll.verticalScrollBar().maximum() > 0
        assert page.start_button.height() >= page.start_button.sizeHint().height()
        assert page.device_combo.height() >= page.device_combo.sizeHint().height()
        assert page.height() >= 540
    finally:
        cleanup(window, factory)
