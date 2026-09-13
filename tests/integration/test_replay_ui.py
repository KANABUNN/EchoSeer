"""Replay buttons, repeatability, explicit export and responsive worker shutdown."""

from pathlib import Path
from threading import Event
import time
import numpy as np
import pytest
from scipy.io import wavfile

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QApplication

from audio.data import AudioClip
from audio.device_manager import DeviceManager
from audio.operations import check_cancel
from audio.sources import WaveFileSource
from audio.waveio import read_wav, write_wav
from replay.analyzer import Analyzer
from tests.fakes_audio import AudioFactory, FakeModule
from tests.integration.test_live_ui import qt_app, make_window, cleanup, pump_until
from ui.audio_controller import AudioController
from ui.main_window import MainWindow
from ui.operation_controller import OperationController


def make_wav(path: Path) -> Path:
    values = np.sin(2 * np.pi * 440 * np.arange(4410) / 44100).astype(np.float32)
    samples = np.stack((values * 0.2 + 0.1, values * 0.4 + 0.2), axis=1)
    return write_wav(path, AudioClip(samples, 44100))


def analyze_page(window: MainWindow) -> None:
    page = window.replay_page
    page.analyze_button.click()
    pump_until(lambda: not window.operations.busy and page.result is not None)


@pytest.mark.gui
def test_open_repeated_analyze_and_both_export_formats_on_main_thread(qt_app, tmp_path: Path, monkeypatch) -> None:
    path = make_wav(tmp_path / "オラクル sample.wav")
    original_bytes = path.read_bytes()
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    page = window.replay_page
    threads = []
    original_set_result = page.set_result
    def record(result, live=False):
        threads.append(QThread.currentThread())
        original_set_result(result, live)
    page.set_result = record
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), "WAV"))
    try:
        window.tabs.setCurrentIndex(1)
        assert not page.analyze_button.isEnabled() and not page.save_button.isEnabled()
        page.open_button.click()
        assert page.source.path == path and page.analyze_button.isEnabled()
        first_samples = checksum = None
        for iteration in range(3):
            analyze_page(window)
            result = page.result
            assert result.original.sample_rate == 44100 and result.original.channels == 2
            assert result.processed.sample_rate == 48000 and result.processed.frame_count == 4800
            if iteration == 0:
                first_samples, checksum = result.processed.samples.copy(), result.checksum
            else:
                np.testing.assert_array_equal(result.processed.samples, first_samples)
                assert result.checksum == checksum and "同じサンプル列" in page.message_label.text()
        for index, encoding in enumerate(("float32", "pcm16")):
            destination = tmp_path / f"export-{encoding}.wav"
            monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, p=destination: (str(p.with_suffix("")), "WAV"))
            page.encoding_combo.setCurrentIndex(index)
            page.save_button.click()
            pump_until(lambda: destination.exists() and not window.operations.busy)
            saved = read_wav(destination)
            assert saved.sample_rate == 48000 and saved.channels == 1
            if encoding == "float32":
                np.testing.assert_array_equal(saved.samples, first_samples)
            else:
                np.testing.assert_allclose(saved.samples, first_samples, atol=1 / 32768)
        assert path.read_bytes() == original_bytes
        assert threads and all(thread == qt_app.thread() for thread in threads)
        assert not factory.streams
    finally:
        cleanup(window, factory)
        assert window.operations.shutdown(2)


@pytest.mark.gui
def test_stopped_live_buffer_can_dump_and_use_the_same_replay_analyzer(qt_app, tmp_path: Path, monkeypatch) -> None:
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    page = window.live_page
    destination = tmp_path / "native.wav"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), "WAV"))
    try:
        assert not page.replay_button.isEnabled() and not page.dump_button.isEnabled()
        page.start_button.click()
        pump_until(lambda: page.state_label.text() == "LIVE" and page.replay_button.isEnabled())
        page.stop_button.click()
        pump_until(lambda: page.state_label.text() == "STOPPED")
        assert page.dump_button.isEnabled()
        seconds = window.controller.service.buffer.duration_seconds
        assert f"{seconds:.1f} /" in page.buffer_label.text()
        assert page.level_meter.value() == 0
        page.replay_button.click()
        pump_until(lambda: not window.operations.busy and window.replay_page.result is not None)
        live = window.replay_page.result
        assert window.tabs.currentIndex() == 1
        assert live.original.sample_rate == 48000 and live.original.channels == 2
        page.dump_button.click()
        pump_until(lambda: destination.exists() and not window.operations.busy)
        np.testing.assert_array_equal(read_wav(destination).samples, live.original.samples)
        replay = Analyzer().analyze(WaveFileSource(destination))
        np.testing.assert_array_equal(replay.processed.samples, live.processed.samples)
        assert replay.checksum == live.checksum
        analyze_page(window)
        assert window.replay_page.result.checksum == live.checksum
        assert "同じサンプル列" in window.replay_page.message_label.text()
    finally:
        cleanup(window, factory)
        assert window.operations.shutdown(2)


@pytest.mark.gui
def test_bad_wav_is_visible_and_valid_wav_can_be_retried(qt_app, tmp_path: Path) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not WAV")
    good = make_wav(tmp_path / "good.wav")
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    try:
        page = window.replay_page
        page.set_wave_path(bad)
        page.analyze_button.click()
        pump_until(lambda: not window.operations.busy and "RIFF" in page.message_label.text())
        assert page.result is None and not page.save_button.isEnabled()
        page.set_wave_path(good)
        analyze_page(window)
        assert page.save_button.isEnabled()
    finally:
        cleanup(window, factory)
        assert window.operations.shutdown(2)


@pytest.mark.gui
def test_failed_reanalysis_clears_stale_export(qt_app, tmp_path: Path) -> None:
    path = make_wav(tmp_path / "changing.wav")
    factory = AudioFactory()
    window = make_window(tmp_path, factory)
    try:
        page = window.replay_page
        page.set_wave_path(path)
        analyze_page(window)
        path.write_bytes(b"truncated")
        page.analyze_button.click()
        assert page.result is None
        pump_until(lambda: not window.operations.busy and "RIFF" in page.message_label.text())
        assert page.result is None and not page.save_button.isEnabled()
    finally:
        cleanup(window, factory)
        assert window.operations.shutdown(2)


@pytest.mark.gui
def test_slow_analysis_keeps_capture_running_and_close_nonblocking(qt_app, tmp_path: Path) -> None:
    entered, gate = Event(), Event()
    class SlowAnalyzer(Analyzer):
        def analyze(self, source, cancel=None):
            entered.set()
            while not gate.wait(0.01):
                check_cancel(cancel)
            return super().analyze(source, cancel)
    factory = AudioFactory()
    controller = AudioController(manager=DeviceManager(FakeModule, factory))
    operations = OperationController(analyzer=SlowAnalyzer())
    window = MainWindow(tmp_path / "data", controller=controller, operations=operations)
    window.show()
    try:
        pump_until(lambda: window.live_page.state_label.text() == "STOPPED")
        window.live_page.start_button.click()
        pump_until(lambda: window.live_page.state_label.text() == "LIVE" and window.live_page.replay_button.isEnabled())
        window.replay_page.set_wave_path(make_wav(tmp_path / "slow.wav"))
        before = time.monotonic()
        window.replay_page.analyze_button.click()
        assert time.monotonic() - before < 0.1
        pump_until(entered.is_set)
        assert not operations.analyze(window.replay_page.source)
        before_frames = controller.service.buffer.size_frames
        pump_until(lambda: controller.service.buffer.size_frames > before_frames)
        assert window.live_page.state_label.text() == "LIVE"
        before = time.monotonic()
        window.close()
        assert time.monotonic() - before < 0.1
        pump_until(lambda: not window.isVisible() and not operations.is_alive)
        assert not controller.service.is_alive
    finally:
        gate.set()
        cleanup(window, factory)
        assert operations.shutdown(2)
