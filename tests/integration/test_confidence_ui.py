"""Confidence/unknown output, persisted toggles, storage failures and live/replay isolation."""
import json
import pytest
from audio.ring_buffer import RingBuffer
from audio.sources import LiveSource
from audio.waveio import write_wav
from config.manager import ConfigManager
from config.schema import AppConfig
from detector.classifier import OracleClassifier
from tests.fakes_audio import AudioFactory
from tests.integration.test_live_ui import qt_app,make_window,cleanup,pump_until
from tests.unit.test_classifier import oracle_wave
from tests.unit.test_confidence import ranking


class FixedClassifier(OracleClassifier):
    def __init__(self,result):
        self.result=result
    def classify_preprocessed(self,event,cancel=None):
        return self.result


def analyze(window,path=None):
    page=window.replay_page
    if path:page.set_wave_path(path)
    page.analyze_button.click()
    pump_until(lambda:not window.operations.busy and page.recognition.detection is not None)
    return page.recognition.detection


@pytest.mark.gui
def test_unknown_keeps_debug_candidates_and_replay_decision_is_repeatable(qt_app,tmp_path):
    factory=AudioFactory()
    window=make_window(tmp_path,factory)
    window.operations.classifier=FixedClassifier(ranking(.83,.82))
    query=write_wav(tmp_path/"query.wav",oracle_wave(1))
    try:
        first=analyze(window,query)
        view=window.replay_page.recognition
        assert first.oracle is None and first.status=="LOW"
        assert "unknown" in view.decision_label.text() and "LOW" in view.decision_label.text()
        assert "L2" in view.best_label.text() and ".830000" in view.best_label.text()
        assert window.replay_page.save_button.isEnabled()
        assert analyze(window)==first
        lines=[json.loads(x) for x in window.operations.recorder.events.path.read_text(encoding="utf-8").splitlines()]
        assert len(lines)==2 and all(x["oracle"] is None for x in lines)
        query.write_bytes(b"broken")
        window.replay_page.analyze_button.click()
        assert view.detection is None and "—" in view.decision_label.text()
        pump_until(lambda:not window.operations.busy)
        assert view.detection is None and not window.replay_page.save_button.isEnabled()
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_settings_and_independent_save_toggles_are_applied_and_persisted(qt_app,tmp_path):
    factory=AudioFactory()
    settings=AppConfig()
    settings.recognition.confidence_threshold=.70
    settings.recognition.high_confidence_threshold=.80
    settings.recognition.margin_threshold=.05
    settings.recognition.high_margin_threshold=.10
    window=make_window(tmp_path,factory,settings)
    window.operations.classifier=FixedClassifier(ranking(.83,.72))
    query=write_wav(tmp_path/"query.wav",oracle_wave(1))
    try:
        view=window.replay_page.recognition
        view.event_logs_checkbox.click();view.uncertain_audio_checkbox.click()
        result=analyze(window,query)
        assert result.oracle.value=="L2" and result.status=="HIGH"
        assert "L2" in view.decision_label.text()
        assert not (window.data_root/"logs").exists()
        loaded=ConfigManager(window.data_root/"config.json").load()
        assert not loaded.logging.event_logs and not loaded.logging.uncertain_audio
        window.operations.classifier=FixedClassifier(ranking(.83,.82))
        view.uncertain_audio_checkbox.click()
        assert not analyze(window).accepted
        assert len(list((window.data_root/"logs/audio").rglob("*.wav")))==1
        assert not window.operations.recorder.events.path.exists()
        view.uncertain_audio_checkbox.click();view.event_logs_checkbox.click()
        analyze(window)
        assert window.operations.recorder.events.path.exists()
        assert len(list((window.data_root/"logs/audio").rglob("*.wav")))==1
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_log_write_failure_keeps_analysis_and_visible_unknown(qt_app,tmp_path,monkeypatch):
    factory=AudioFactory()
    window=make_window(tmp_path,factory)
    window.operations.classifier=FixedClassifier(ranking(.83,.82))
    query=write_wav(tmp_path/"query.wav",oracle_wave(1))
    def fail(event):raise OSError("disk unavailable")
    monkeypatch.setattr(window.operations.recorder.events,"write",fail)
    try:
        result=analyze(window,query)
        assert result.oracle is None and window.replay_page.result is not None
        assert window.replay_page.save_button.isEnabled()
        assert "認識ログ" in window.replay_page.recognition.warning_label.text()
        assert "unknown" in window.replay_page.recognition.decision_label.text()
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_frozen_live_buffer_duplicate_replay_and_new_stream(qt_app,tmp_path,monkeypatch):
    factory=AudioFactory()
    window=make_window(tmp_path,factory)
    window.operations.classifier=FixedClassifier(ranking())
    ring=RingBuffer(48000,1)
    ring.write(oracle_wave(1).samples)
    monkeypatch.setattr(window, "_live_source", lambda: LiveSource(ring))
    def live():
        window._analyze_live()
        pump_until(lambda:not window.operations.busy and window.replay_page.recognition.detection is not None)
        return window.replay_page.recognition.detection
    try:
        assert live().accepted
        duplicate=live()
        assert duplicate.duplicate and duplicate.oracle is None
        assert "重複" in window.replay_page.recognition.decision_label.text()
        assert analyze(window).accepted
        assert live().duplicate
        ring.clear();ring.write(oracle_wave(1).samples)
        assert live().accepted
    finally:cleanup(window,factory)
