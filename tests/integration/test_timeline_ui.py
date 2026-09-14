"""Timeline row selection, safe replay retry and worker-owned playback."""
from threading import Event
import time
import pytest
from PySide6.QtCore import QThread
from audio.sources import ClipSource
from audio.waveio import write_wav
from replay.analyzer import Analyzer
from tests.fakes_audio import AudioFactory, FakeModule
from tests.integration.test_live_ui import qt_app, pump_until, cleanup
from tests.unit.test_sequence_analyzer import two_pass_clip
from tests.verification_helpers import RowsClassifier, rows_for
from audio.device_manager import DeviceManager
from ui.audio_controller import AudioController
from ui.operation_controller import OperationController
from ui.main_window import MainWindow


def window_for(tmp_path):
    factory=AudioFactory()
    operations=OperationController(analyzer=Analyzer(8000),classifier=RowsClassifier(rows_for("MISMATCH")))
    window=MainWindow(tmp_path/"data",controller=AudioController(manager=DeviceManager(FakeModule,factory)),operations=operations)
    window.show()
    pump_until(lambda:window.live_page.state_label.text()=="STOPPED")
    path=write_wav(tmp_path/"round.wav",two_pass_clip())
    window.replay_page.set_wave_path(path)
    return window,factory,path


@pytest.mark.gui
def test_timeline_selection_and_repeated_analysis_are_main_thread_and_same_profile(qt_app,tmp_path):
    window,factory,path=window_for(tmp_path)
    page=window.replay_page
    threads=[]
    old=page.timeline.set_result
    def observe(*args,**kwargs):
        threads.append(QThread.currentThread()); old(*args,**kwargs)
    page.timeline.set_result=observe
    try:
        page.timeline.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.timeline.result is not None)
        first=page.timeline.result
        assert page.timeline.table.rowCount()==6 and page.timeline.table.item(0,0).text()=="00:00.30"
        page.timeline.table.setCurrentCell(5,0)
        assert page.recognition.result.best_candidate.value=="R3"
        assert page.recognition.detection.oracle.value=="R3"
        assert page.timeline.selected_trace.end_frame <= page.result.original.frame_count
        page.timeline.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.timeline.result is not None)
        assert page.timeline.result==first and "同じサンプル列" in page.message_label.text()
        assert threads and all(thread==qt_app.thread() for thread in threads)
    finally:
        cleanup(window,factory)


@pytest.mark.gui
def test_bad_reanalysis_clears_timeline_and_selection(qt_app,tmp_path):
    window,factory,path=window_for(tmp_path)
    page=window.replay_page
    try:
        page.timeline.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.timeline.result is not None)
        path.write_bytes(b"bad")
        page.timeline.analyze_button.click()
        assert page.timeline.result is None and page.result is None
        pump_until(lambda:not window.operations.busy)
        assert page.timeline.table.rowCount()==0 and not page.timeline.listen_button.isEnabled()
        assert page.recognition.result is None
    finally:
        cleanup(window,factory)


@pytest.mark.gui
def test_selected_playback_and_stop_are_responsive_and_use_native_slice(qt_app,tmp_path):
    window,factory,path=window_for(tmp_path)
    page=window.replay_page
    entered=Event(); seen=[]
    class Player:
        def play(self,clip,volume,stop):
            seen.append((clip,volume)); entered.set()
            while not stop.wait(.01):
                pass
    window.operations.player=Player()
    try:
        page.timeline.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.timeline.result is not None)
        page.timeline.table.setCurrentCell(1,0)
        start=time.monotonic(); page.timeline.listen_button.click()
        assert time.monotonic()-start < .1
        pump_until(entered.is_set)
        assert seen[0][0].sample_rate==8000 and seen[0][1]==.2
        assert seen[0][0].frame_count==page.timeline.selected_trace.end_frame-page.timeline.selected_trace.start_frame
        page.timeline.stop_button.click()
        pump_until(lambda:not window.operations.busy)
        assert page.timeline.result is not None
    finally:
        cleanup(window,factory)
