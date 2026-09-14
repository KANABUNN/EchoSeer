"""Continuous worker publishes two-pass results on Live without switching to Replay."""
from threading import Event
import json
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from audio.device_manager import DeviceManager
from config.schema import AppConfig, LoggingSettings, SequenceSettings
from logging_ext.recognition_logger import RecognitionRecorder
from ui.audio_controller import AudioController
from ui.live_controller import LiveController, LiveUpdate
from ui.main_window import MainWindow
from detector.live_sequence import LiveResult
from encounter.sequence import SequenceEngine, SequenceState
from tests.fakes_audio import AudioFactory, FakeModule
from tests.integration.test_live_ui import qt_app, pump_until
from tests.unit.test_streaming_live import round_audio, quiet, tone
from tests.verification_helpers import RowsClassifier, rows_for


def make_live(tmp_path, status="CONFIRMED", sequence=None):
    factory = AudioFactory()
    factory.no_data = True
    root = tmp_path / "data"
    settings = AppConfig()
    if sequence:
        settings.sequence = sequence
    recorder = RecognitionRecorder(root / "logs", LoggingSettings(uncertain_audio=False))
    recognizer = LiveController(lambda: RowsClassifier(rows_for(status)),
                                sequence=settings.sequence, recorder=recorder)
    window = MainWindow(root, settings=settings, recognizer=recognizer,
                        controller=AudioController(manager=DeviceManager(FakeModule, factory)))
    window.show()
    pump_until(lambda: window.live_page.state_label.text() == "STOPPED" and not window.templates.busy)
    window.live_page.start_button.click()
    pump_until(lambda: window.live_page.state_label.text() == "LIVE" and
               window.live_page.dashboard.result.state == SequenceState.ARMED)
    return window, factory


def close_live(window):
    assert window.recognizer.shutdown(2)
    assert window.controller.shutdown(2)
    assert window.operations.shutdown(2)
    assert window.templates.shutdown(2)
    window.close()
    QApplication.processEvents()
    assert not window.isVisible()


@pytest.mark.gui
@pytest.mark.parametrize("status", ["CONFIRMED", "INFERRED", "MISMATCH", "CHECK"])
def test_live_results_keep_raw_passes_map_and_main_thread(tmp_path, qt_app, status):
    window, factory = make_live(tmp_path, status)
    dashboard = window.live_page.dashboard
    threads = []
    original = dashboard.set_result
    def record(*args, **kwargs):
        threads.append(QThread.currentThread())
        return original(*args, **kwargs)
    dashboard.set_result = record
    try:
        window.controller.service.buffer.write(round_audio())
        pump_until(lambda: dashboard.result.verification is not None)
        assert window.tabs.currentIndex() == 0
        assert dashboard.result.verification.status.value == status
        assert "3/3" in dashboard.count_label.text()
        assert "Confidence" in dashboard.confidence_label.text()
        assert status in dashboard.state_label.text()
        assert dashboard.pass_table.item(0, 0).text().startswith("L1")
        assert "候補" in dashboard.pass_table.item(0, 0).toolTip()
        if status == "CONFIRMED":
            assert "確定順" in dashboard.final_label.text()
            assert [item.value for item in dashboard.oracle_map.sequence] == ["L1", "L2", "L3"]
        else:
            assert "確定順" not in dashboard.final_label.text()
            assert dashboard.result.final_sequence is None
        if status == "INFERRED":
            assert "推定順（要確認）" in dashboard.final_label.text()
            assert dashboard.pass_table.item(0, 2).text().startswith("unknown")
        if status == "MISMATCH":
            assert "不一致位置：3" in dashboard.reason_label.text()
        assert threads and all(thread == qt_app.thread() for thread in threads)
        records = [json.loads(line) for line in window.recognizer.recorder.events.path.read_text(encoding="utf-8").splitlines()]
        summary = next(row for row in records if row["event_type"] == "sequence")
        assert summary["source"]["source"] == "live"
        assert summary["source"]["checksum_scope"] == "ordered_cue_checksums"
        assert summary["confirmed"] == (status == "CONFIRMED")
    finally:
        close_live(window)


@pytest.mark.gui
def test_live_round_reset_and_stale_generation_do_not_restore_old_confirmation(tmp_path, qt_app):
    window, factory = make_live(tmp_path)
    dashboard = window.live_page.dashboard
    try:
        window.controller.service.buffer.write(round_audio())
        pump_until(lambda: dashboard.result.confirmed)
        old = dashboard.result
        old_generation = window.recognizer.generation
        dashboard.next_button.click()
        pump_until(lambda: dashboard.result.round_index == 2 and dashboard.result.state == SequenceState.ARMED)
        assert not dashboard.result.pass1 and not dashboard.oracle_map.sequence
        window._on_live_update(LiveUpdate(old_generation, LiveResult(old)))
        assert dashboard.round_index == 2 and not dashboard.result.confirmed
        dashboard.reset_button.click()
        pump_until(lambda: dashboard.result.round_index == 1 and dashboard.result.state == SequenceState.ARMED)
        window.live_page.stop_button.click()
        pump_until(lambda: window.live_page.state_label.text() == "STOPPED")
        assert dashboard.result.state == SequenceState.IDLE and not dashboard.result.pass1
        assert not dashboard.oracle_map.sequence
    finally:
        close_live(window)


@pytest.mark.gui
@pytest.mark.parametrize("reset_stream", [False, True])
def test_live_ring_loss_invalidates_confirmation(tmp_path, qt_app, reset_stream):
    window, factory = make_live(tmp_path)
    dashboard = window.live_page.dashboard
    try:
        ring = window.controller.service.buffer
        ring.write(round_audio())
        pump_until(lambda: dashboard.result.confirmed)
        if reset_stream:
            ring.clear()
            ring.write(quiet(.5))
        else:
            ring.write(quiet(20))
        pump_until(lambda: dashboard.result.state == SequenceState.UNCERTAIN)
        assert not dashboard.result.confirmed and dashboard.result.final_sequence is None
        assert dashboard.result.verification.status.value == "CHECK"
        assert dashboard.result.reason == ("SOURCE_CHANGED" if reset_stream else "AUDIO_GAP")
        assert "確定順" not in dashboard.final_label.text()
        dashboard.reset_button.click()
        pump_until(lambda: dashboard.result.state == SequenceState.ARMED)
        assert not dashboard.result.pass1
    finally:
        close_live(window)


@pytest.mark.gui
def test_live_pause_discards_old_audio_and_resumes_with_empty_passes(tmp_path, qt_app):
    window, factory = make_live(tmp_path)
    dashboard = window.live_page.dashboard
    try:
        ring = window.controller.service.buffer
        ring.write(round_audio())
        pump_until(lambda: dashboard.result.confirmed)
        window.recognizer.set_paused("Calibration中")
        pump_until(lambda: dashboard.message == "Calibration中")
        ring.write(round_audio())
        window.recognizer.set_paused()
        pump_until(lambda: dashboard.result.state == SequenceState.ARMED and not dashboard.message)
        assert not dashboard.result.pass1 and not dashboard.oracle_map.sequence
        window.recognizer.set_logging(LoggingSettings(event_logs=False, uncertain_audio=False))
        ring.write(round_audio())
        pump_until(lambda: dashboard.result.confirmed)
        assert not window.recognizer.recorder.settings.event_logs
    finally:
        close_live(window)


@pytest.mark.gui
def test_live_automatic_round_transition_is_preserved_on_stop(tmp_path, qt_app):
    window, factory = make_live(tmp_path, sequence=SequenceSettings(lockout_duration=1, silence_duration=.5))
    dashboard = window.live_page.dashboard
    try:
        ring = window.controller.service.buffer
        ring.write(round_audio())
        pump_until(lambda: dashboard.result.confirmed)
        ring.write(quiet(1.5))
        pump_until(lambda: dashboard.result.round_index == 2 and dashboard.result.state == SequenceState.ARMED)
        window.live_page.stop_button.click()
        pump_until(lambda: dashboard.result.state == SequenceState.IDLE)
        assert dashboard.round_index == 2
        assert not dashboard.result.pass1 and not dashboard.result.confirmed
    finally:
        close_live(window)
