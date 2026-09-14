"""Asynchronous sequence Replay/live snapshots, recovery and cancellation."""
from threading import Event
import pytest
from PySide6.QtCore import QThread
from audio.operations import check_cancel
from audio.ring_buffer import RingBuffer
from audio.sources import LiveSource
from audio.waveio import write_wav
from tests.fakes_audio import AudioFactory
from tests.integration.test_live_ui import qt_app,make_window,cleanup,pump_until
from tests.unit.test_sequence_analyzer import StubClassifier,two_pass_clip
from tests.unit.test_confidence import ranking


def sequence(window,path=None):
    if path:window.replay_page.set_wave_path(path)
    window.replay_page.sequence.analyze_button.click()
    pump_until(lambda:not window.operations.busy and window.replay_page.sequence.result is not None)
    return window.replay_page.sequence.result


@pytest.mark.gui
def test_sequence_progress_round_controls_and_failed_reanalysis(qt_app,tmp_path):
    factory=AudioFactory()
    window=make_window(tmp_path,factory)
    window.operations.classifier=StubClassifier()
    path=write_wav(tmp_path/"sequence.wav",two_pass_clip())
    view=window.replay_page.sequence
    threads=[]
    real=view.set_result
    def set_result(result):
        threads.append(QThread.currentThread())
        real(result)
    view.set_result=set_result
    try:
        result=sequence(window,path)
        assert result.state=="VERIFY" and not result.confirmed
        assert "照合待ち" in view.reason_label.text()
        assert all(view.table.item(row,index).text().startswith("L2") for row in range(2) for index in range(3))
        assert threads and all(t==qt_app.thread() for t in threads)
        assert window.replay_page.save_button.isEnabled()
        view.next_button.click()
        assert view.round_index==2 and view.result is None and "4 個" in view.state_label.text()
        view.reset_button.click()
        assert view.round_index==1
        sequence(window)
        path.write_bytes(b"broken")
        view.analyze_button.click()
        assert view.result is None
        pump_until(lambda:not window.operations.busy)
        assert view.result is None and not window.replay_page.save_button.isEnabled()
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_unknown_positions_remain_visible_with_logging_off(qt_app,tmp_path):
    from config.schema import AppConfig
    settings=AppConfig()
    settings.logging.event_logs=settings.logging.uncertain_audio=False
    factory=AudioFactory()
    window=make_window(tmp_path,factory,settings)
    window.operations.classifier=StubClassifier(ranking(.83,.82))
    try:
        result=sequence(window,write_wav(tmp_path/"sequence.wav",two_pass_clip()))
        view=window.replay_page.sequence
        assert result.state=="UNCERTAIN" and result.reason=="UNKNOWN_EVENT"
        assert all("unknown" in view.table.item(row,index).text() for row in range(2) for index in range(3))
        assert not (window.data_root/"logs").exists()
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_live_buffer_sequence_and_replayed_copy_have_identical_passes(qt_app,tmp_path,monkeypatch):
    factory=AudioFactory()
    window=make_window(tmp_path,factory)
    window.operations.classifier=StubClassifier()
    clip=two_pass_clip()
    ring=RingBuffer(clip.sample_rate,clip.channels)
    ring.write(clip.samples)
    monkeypatch.setattr(window,"_live_source",lambda:LiveSource(ring))
    try:
        window._analyze_live_sequence()
        pump_until(lambda:not window.operations.busy and window.replay_page.sequence.result is not None)
        live=window.replay_page.sequence.result
        assert live.state=="VERIFY" and live.pass1[0].detection.context.source=="live"
        replay=sequence(window)
        assert replay.state=="VERIFY" and replay.pass1[0].detection.context.source=="replay"
        assert [e.oracle for e in replay.pass1]==[e.oracle for e in live.pass1]
        assert [e.oracle for e in replay.pass2]==[e.oracle for e in live.pass2]
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_close_during_sequence_classification_keeps_capture_responsive(qt_app,tmp_path):
    entered=Event()
    class Slow(StubClassifier):
        def classify_preprocessed(self,event,cancel=None):
            entered.set()
            while not cancel.wait(.01):pass
            check_cancel(cancel)
    factory=AudioFactory()
    factory.tone=True
    window=make_window(tmp_path,factory)
    window.operations.classifier=Slow()
    try:
        window.live_page.start_button.click()
        pump_until(lambda:window.live_page.replay_button.isEnabled())
        window.replay_page.set_wave_path(write_wav(tmp_path/"sequence.wav",two_pass_clip()))
        window.replay_page.sequence.analyze_button.click()
        pump_until(entered.is_set)
        assert not window.replay_page.sequence.reset_button.isEnabled()
        before=window.controller.service.buffer.size_frames
        pump_until(lambda:window.controller.service.buffer.size_frames>before)
        window.close()
        pump_until(lambda:not window.isVisible() and not window.operations.is_alive)
        assert not window.controller.service.is_alive and not window.templates.is_alive
    finally:cleanup(window,factory)
