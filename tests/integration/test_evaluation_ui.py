"""Evaluation and report export on one cancellable worker, with capture independent."""
import json
from threading import Event
import time
import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QFileDialog
from encounter.vog_oracles import OracleId
from tests.unit.test_confidence import ranking
from audio.data import AudioDataError
from audio.operations import check_cancel
from audio.device_manager import DeviceManager
from replay.analyzer import Analyzer
from tests.fakes_audio import AudioFactory,FakeModule
from tests.integration.test_live_ui import qt_app,pump_until,cleanup
from tests.unit.test_sequence_analyzer import StubClassifier
from tests.unit.test_dataset_evaluation import manifest
from ui.audio_controller import AudioController
from ui.operation_controller import OperationController
from ui.main_window import MainWindow


def setup(tmp_path,classifier=None):
    factory=AudioFactory()
    operations=OperationController(analyzer=Analyzer(8000),classifier=classifier or StubClassifier(ranking(oracle=OracleId.L1)))
    window=MainWindow(tmp_path/"data",controller=AudioController(manager=DeviceManager(FakeModule,factory)),operations=operations)
    window.show();pump_until(lambda:window.live_page.state_label.text()=="STOPPED")
    window.evaluation_page.set_dataset(manifest(tmp_path))
    return window,factory


@pytest.mark.gui
def test_evaluation_report_baseline_and_csv_are_main_thread(qt_app,tmp_path,monkeypatch):
    window,factory=setup(tmp_path)
    page=window.evaluation_page
    threads=[];old=page.set_report
    def observe(report):threads.append(QThread.currentThread());old(report)
    page.set_report=observe
    monkeypatch.setattr(QFileDialog,"getExistingDirectory",lambda *args:str(tmp_path))
    try:
        page.evaluate_button.click()
        pump_until(lambda:not window.operations.busy and page.report is not None)
        assert page.report["metrics"]["accuracy"]==1
        assert page.table.item(0,2).text()=="100.00%"
        page.export_button.click()
        pump_until(lambda:not window.operations.busy)
        exported=list(tmp_path.glob("evaluation-*/report.json"))
        assert len(exported)==1 and exported[0].with_name("confusion-matrix.csv").exists()
        page.set_baseline(exported[0]);page.evaluate_button.click()
        pump_until(lambda:not window.operations.busy and page.report is not None)
        assert page.report["comparison"]["deltas"]["accuracy"]==0
        assert threads and all(thread==qt_app.thread() for thread in threads)
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_bad_truth_does_not_leave_previous_report_exportable(qt_app,tmp_path):
    window,factory=setup(tmp_path);page=window.evaluation_page
    try:
        page.evaluate_button.click();pump_until(lambda:not window.operations.busy and page.report is not None)
        page.dataset_path.write_text('{"schema_version":1,"cases":[]}',encoding="utf-8")
        page.evaluate_button.click()
        assert page.report is None
        pump_until(lambda:not window.operations.busy)
        assert not page.export_button.isEnabled() and "Dataset" in page.message_label.text()
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_cancel_evaluation_keeps_capture_running_and_shutdown_responsive(qt_app,tmp_path):
    entered=Event()
    class Slow(StubClassifier):
        def classify_preprocessed(self,clip,cancel=None):
            entered.set()
            while True:
                check_cancel(cancel);time.sleep(.01)
    window,factory=setup(tmp_path,Slow());page=window.evaluation_page
    try:
        window.live_page.start_button.click();pump_until(lambda:window.live_page.state_label.text()=="LIVE")
        start=time.monotonic();page.evaluate_button.click()
        assert time.monotonic()-start<.1
        pump_until(entered.is_set)
        before=window.controller.service.buffer.snapshot_event(0)[4]
        pump_until(lambda:window.controller.service.buffer.snapshot_event(0)[4]>before)
        page.cancel_button.click();pump_until(lambda:not window.operations.busy)
        assert page.report is None and window.live_page.state_label.text()=="LIVE"
    finally:cleanup(window,factory)
