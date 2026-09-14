"""Ranking in Replay/Live snapshots, failed reanalysis and cancellable close."""
from threading import Event
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QFileDialog

from audio.operations import check_cancel
from audio.waveio import write_wav
from config.schema import AppConfig
from detector.classifier import OracleClassifier
from templates.manager import TemplateManager
from tests.fakes_audio import AudioFactory
from tests.integration.test_live_ui import qt_app,make_window,cleanup,pump_until
from tests.unit.test_classifier import oracle_wave


@pytest.mark.gui
def test_replay_displays_best_second_all_scores_custom_labels_and_refresh(qt_app,tmp_path,monkeypatch):
    root=tmp_path/"user-data"
    manager=TemplateManager(root/"templates")
    for i,oracle in enumerate(("L1","L2","L3","MID","R1","R2","R3")):
        manager.add(oracle,oracle_wave(i))
    query=write_wav(tmp_path/"query.wav",oracle_wave(3))
    factory=AudioFactory()
    settings=AppConfig()
    settings.oracle_labels["MID"]="中央 CUSTOM"
    window=make_window(tmp_path,factory,settings)
    page=window.replay_page
    threads=[]
    real=page.recognition.set_result
    def record(result):
        threads.append(QThread.currentThread())
        real(result)
    page.recognition.set_result=record
    try:
        page.set_wave_path(query)
        page.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.recognition.result is not None)
        result=page.recognition.result
        assert result.oracle.value=="MID" and "CUSTOM" in page.recognition.best_label.text()
        assert result.second_candidate.value in page.recognition.second_label.text()
        assert "1.000000" in page.recognition.best_label.text()
        assert page.recognition.table.rowCount()==7 and "7 サンプル" in page.recognition.status_label.text()
        assert threads and all(t==qt_app.thread() for t in threads)
        manager.add("R1",oracle_wave(3))
        page.analyze_button.click()
        assert page.recognition.result is None and "—" in page.recognition.best_label.text()
        pump_until(lambda:not window.operations.busy and page.recognition.result is not None)
        assert page.recognition.result.status=="TIED"
        assert "確定しません" in page.recognition.status_label.text()
        query.write_bytes(b"broken")
        page.analyze_button.click()
        pump_until(lambda:not window.operations.busy and "RIFF" in page.message_label.text())
        assert page.recognition.result is None and page.result is None
    finally:
        cleanup(window,factory)


@pytest.mark.gui
def test_live_snapshot_and_saved_wav_have_identical_ranking(qt_app,tmp_path):
    factory=AudioFactory()
    factory.tone=True
    root=tmp_path/"user-data"
    manager=TemplateManager(root/"templates")
    # Fake callback supplies a deterministic 440 Hz waveform.
    from tests.unit.test_templates import tone
    manager.add("L1",tone(.1,48000,1,dc=0))
    window=make_window(tmp_path,factory)
    try:
        window.live_page.start_button.click()
        pump_until(lambda:window.live_page.replay_button.isEnabled())
        window.live_page.stop_button.click()
        pump_until(lambda:window.live_page.state_label.text()=="STOPPED")
        window.live_page.replay_button.click()
        page=window.replay_page
        pump_until(lambda:not window.operations.busy and page.recognition.result is not None)
        live=page.recognition.result
        assert live.oracle is not None
        raw=write_wav(tmp_path/"raw.wav",page.result.original)
        page.set_wave_path(raw)
        page.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.recognition.result is not None)
        assert page.recognition.result==live
    finally:
        cleanup(window,factory)


@pytest.mark.gui
def test_missing_templates_are_visible_while_audio_export_still_works(qt_app,tmp_path):
    factory=AudioFactory()
    window=make_window(tmp_path,factory)
    try:
        page=window.replay_page
        page.set_wave_path(write_wav(tmp_path/"no-bank.wav",oracle_wave(1)))
        page.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.recognition.result is not None)
        assert page.recognition.result.status=="NO_TEMPLATES" and page.save_button.isEnabled()
        assert "Calibration" in page.recognition.warning_label.text()
        assert all(page.recognition.table.item(i,3).text()=="0" for i in range(7))
    finally:
        cleanup(window,factory)


@pytest.mark.gui
def test_close_during_classification_keeps_capture_responsive_and_frees_workers(qt_app,tmp_path):
    factory=AudioFactory()
    entered=Event()
    class SlowClassifier(OracleClassifier):
        def classify_preprocessed(self,event,cancel=None):
            entered.set()
            while not cancel.wait(.01): pass
            check_cancel(cancel)
    window=make_window(tmp_path,factory)
    window.operations.classifier=SlowClassifier()
    try:
        window.live_page.start_button.click()
        pump_until(lambda:window.live_page.replay_button.isEnabled())
        window.live_page.replay_button.click()
        pump_until(entered.is_set)
        before=window.controller.service.buffer.size_frames
        pump_until(lambda:window.controller.service.buffer.size_frames>before)
        started=time.monotonic()
        window.close()
        assert time.monotonic()-started<.1
        pump_until(lambda:not window.isVisible() and not window.operations.is_alive)
        assert not window.controller.service.is_alive and not window.templates.is_alive
    finally:
        cleanup(window,factory)
