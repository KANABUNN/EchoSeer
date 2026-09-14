"""GUI sample creation, native quality, seven-Oracle readiness and display-name persistence."""
from threading import Event
import json
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QFileDialog, QApplication

from config.schema import AppConfig
from audio.operations import check_cancel
from audio.waveio import write_wav
from encounter.vog_oracles import OracleId
from templates.manager import TemplateManager
from ui.template_controller import TemplateController
from tests.fakes_audio import AudioFactory
from tests.integration.test_calibration_ui import tone, window_at
from tests.integration.test_live_ui import qt_app, pump_until
from tests.integration.test_live_recognition_ui import close_live
from tests.unit.test_pass_comparator import event


@pytest.mark.gui
def test_import_quality_and_selection_reset_without_folder_operations(qt_app, tmp_path, monkeypatch):
    source = write_wav(tmp_path / "source.wav", tone())
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(source), str(source)], "WAV"))
    factory = AudioFactory()
    window = window_at(tmp_path / "data", factory)
    page = window.calibration_page
    threads = []
    original = page.set_quality
    def record(report):
        threads.append(QThread.currentThread())
        original(report)
    page.set_quality = record
    try:
        page.oracle_buttons[OracleId.R1].click()
        page.import_button.click()
        pump_until(lambda: not window.templates.busy and page.sample_list.count() == 2)
        assert "1/7" in page.readiness_label.text() and "2件" in page.oracle_buttons[OracleId.R1].text()
        page.quality_button.click()
        assert not page.quality_button.isEnabled()
        pump_until(lambda: not window.templates.busy and "品質確認：警告なし" in page.quality_label.text())
        assert "44,100 Hz / 2 ch" in page.quality_label.text()
        assert "Peak:" in page.quality_label.text() and "Oracleの正誤は未判定" in page.quality_label.text()
        assert threads and all(thread == qt_app.thread() for thread in threads)
        page.sample_list.setCurrentRow(0 if page.sample_list.currentRow() else 1)
        assert page.quality_label.isHidden()
        page.details_button.click()
        assert not page.details_label.isHidden() and "RMS:" in page.details_label.text()
    finally:
        close_live(window)


@pytest.mark.gui
def test_each_oracle_can_be_registered_and_checked_from_gui(qt_app, tmp_path, monkeypatch):
    source = write_wav(tmp_path / "source.wav", tone())
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(source)], "WAV"))
    factory = AudioFactory()
    window = window_at(tmp_path / "data", factory)
    page = window.calibration_page
    try:
        for oracle in OracleId:
            page.oracle_buttons[oracle].click()
            page.import_button.click()
            pump_until(lambda: not window.templates.busy and page.sample_list.count() == 1)
            page.quality_button.click()
            pump_until(lambda: not window.templates.busy and "品質確認：警告なし" in page.quality_label.text())
            assert page.sample.metadata.oracle == oracle.value
        assert "7/7種類" in page.readiness_label.text() and "7サンプル" in page.readiness_label.text()
        page.delete_button.click()
        pump_until(lambda: not window.templates.busy and "6/7" in page.readiness_label.text())
        page.restore_button.click()
        pump_until(lambda: not window.templates.busy and "7/7" in page.readiness_label.text())
    finally:
        close_live(window)


@pytest.mark.gui
def test_display_name_save_preserves_detection_and_stable_ids(qt_app, tmp_path):
    factory = AudioFactory()
    root = tmp_path / "data"
    window = window_at(root, factory)
    page = window.calibration_page
    try:
        page.oracle_buttons[OracleId.L2].click()
        detection_entry = event(OracleId.L2)
        recognition = window.replay_page.recognition
        recognition.set_result(detection_entry.classification)
        recognition.set_detection(detection_entry.detection)
        page.label_edit.setText("CUSTOM 左奥")
        page.label_save_button.click()
        assert page.oracle == OracleId.L2
        assert window.live_page.dashboard.oracle_map.labels["L2"] == "CUSTOM 左奥"
        assert window.overlay.labels["L2"] == "CUSTOM 左奥"
        assert recognition.detection == detection_entry.detection
        assert "CUSTOM 左奥" in recognition.decision_label.text()
        assert "保存しました" in page.message_label.text()
        page.label_edit.setText("   ")
        page.label_save_button.click()
        assert window.settings.oracle_labels["L2"] == "CUSTOM 左奥"
        assert "入力してください" in page.message_label.text()
        saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
        assert saved["oracle_labels"]["L2"] == "CUSTOM 左奥"
    finally:
        close_live(window)
    restarted = window_at(root, factory, settings=AppConfig.from_dict(saved))
    try:
        restarted.calibration_page.oracle_buttons[OracleId.L2].click()
        assert restarted.calibration_page.label_edit.text() == "CUSTOM 左奥"
    finally:
        close_live(restarted)


@pytest.mark.gui
def test_corrupt_sample_quality_fails_then_refresh_updates_readiness(qt_app, tmp_path):
    factory = AudioFactory()
    manager = TemplateManager(tmp_path / "data/templates")
    sample = manager.add("L1", tone())
    saved_other = manager.add("R2", tone())
    window = window_at(tmp_path / "data", factory, manager=manager)
    page = window.calibration_page
    try:
        (sample.directory / "original.wav").write_bytes(b"damaged")
        page.quality_button.click()
        pump_until(lambda: not window.templates.busy and "品質確認：失敗" in page.quality_label.text())
        assert "警告なし" not in page.quality_label.text()
        page.refresh_button.click()
        pump_until(lambda: not window.templates.busy and page.sample_list.count() == 0)
        assert not page.quality_button.isEnabled() and "1/7" in page.readiness_label.text()
        assert saved_other.path.exists()
    finally:
        close_live(window)


@pytest.mark.gui
def test_close_during_quality_cancels_all_workers(qt_app, tmp_path):
    entered = Event()
    class SlowManager(TemplateManager):
        def check_quality(self, oracle, sample_id, cancel=None):
            entered.set()
            while not cancel.wait(.01):
                pass
            check_cancel(cancel)
    factory = AudioFactory()
    manager = SlowManager(tmp_path / "data/templates")
    sample = manager.add("L1", tone())
    window = window_at(tmp_path / "data", factory, manager=manager)
    try:
        window.calibration_page.quality_button.click()
        pump_until(entered.is_set)
        start = time.monotonic()
        window.close()
        assert time.monotonic() - start < .1
        pump_until(lambda: not window.isVisible() and not window.templates.is_alive)
        assert not window.recognizer.is_alive and not window.controller.service.is_alive
        assert not window.operations.is_alive and sample.path.exists()
    finally:
        close_live(window)


@pytest.mark.gui
def test_live_record_sample_then_quality_check_needs_no_file_edit(qt_app, tmp_path):
    factory = AudioFactory()
    factory.tone = True
    window = window_at(tmp_path / "data", factory)
    page = window.calibration_page
    try:
        window.live_page.start_button.click()
        pump_until(lambda: page.record_button.isEnabled())
        page.oracle_buttons[OracleId.MID].click()
        page.duration_spin.setValue(.1)
        page.record_button.click()
        pump_until(lambda: not window.templates.busy and page.sample_list.count() == 1)
        assert page.sample.metadata.source == "recorded" and page.sample.metadata.oracle == "MID"
        page.quality_button.click()
        pump_until(lambda: not window.templates.busy and "品質確認：" in page.quality_label.text())
        assert "48,000 Hz / 2 ch" in page.quality_label.text()
        assert page.progress_bar.value() == 1000 and "1/7" in page.readiness_label.text()
    finally:
        close_live(window)
