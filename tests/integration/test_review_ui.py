"""Manual correction, bounded live transfer, log checksum and corpus evaluation."""
import json
import numpy as np
import pytest
from PySide6.QtWidgets import QFileDialog
from audio.sources import ClipSource
from audio.waveio import write_wav
from config.schema import AppConfig
from evaluation.dataset import read_json
from replay.analyzer import Analyzer
from tests.fakes_audio import AudioFactory
from tests.integration.test_live_ui import qt_app,pump_until,cleanup,make_window
from tests.integration.test_timeline_ui import window_for
from tests.unit.test_sequence_analyzer import two_pass_clip,StubClassifier
from tests.unit.test_event_detector import audio
from tests.unit.test_review_logs import record,write_log
from templates.manager import audio_checksum


@pytest.mark.gui
def test_manual_incorrect_package_then_corpus_evaluation(qt_app,tmp_path):
    window,factory,path=window_for(tmp_path);page=window.replay_page
    try:
        page.timeline.analyze_button.click();pump_until(lambda:not window.operations.busy and page.timeline.result is not None)
        page.timeline.table.setCurrentCell(1,0)
        page.review_widget.expected_edit.setText("R3")
        page.review_widget.category_combo.setCurrentIndex(page.review_widget.category_combo.findData("incorrect"))
        page.review_widget.source_combo.setCurrentIndex(page.review_widget.source_combo.findData("synthetic"))
        original=page.result.original.samples.copy()
        page.review_widget.save_button.click();pump_until(lambda:not window.operations.busy)
        manifests=list((window.data_root/"review"/"cases").glob("*/manifest.json"))
        assert len(manifests)==1
        row=read_json(manifests[0])["cases"][0]
        assert row["expected"]==["R3"] and row["category"]=="incorrect"
        np.testing.assert_array_equal(page.result.original.samples,original)
        window.review_page.evaluate_button.click()
        pump_until(lambda:not window.operations.busy and window.evaluation_page.report is not None)
        assert window.evaluation_page.report["metrics"]["incorrect"]==1
        assert window.tabs.currentIndex()==3
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_blank_truth_does_not_save_or_copy_recognition(qt_app,tmp_path):
    window,factory,path=window_for(tmp_path);page=window.replay_page
    try:
        page.analyze_button.click();pump_until(lambda:not window.operations.busy and page.result is not None)
        for value in ("", ",,", "  ,  "):
            page.review_widget.expected_edit.setText(value)
            page.review_widget.save_button.click()
            assert not list((window.data_root/"review"/"cases").glob("*/manifest.json"))
            assert "正解" in page.message_label.text()
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_log_audio_replay_is_owned_and_mismatching_checksum_is_visible(qt_app,tmp_path,monkeypatch):
    root=tmp_path/"recorded";(root/"audio").mkdir(parents=True)
    clip=audio([(.2,0),(.3,.2),(.2,0)])
    write_wav(root/"audio"/"clip.wav",clip)
    p=record();p["audio_native_checksum"]=audio_checksum(clip)
    write_log(root,"session.jsonl",[p])
    monkeypatch.setattr(QFileDialog,"getExistingDirectory",lambda *args:str(root))
    window,factory,path=window_for(tmp_path)
    try:
        window.review_page.open_button.click();pump_until(lambda:not window.operations.busy and window.review_page.catalog is not None)
        window.review_page.replay_button.click();pump_until(lambda:not window.operations.busy and window.replay_page.result is not None)
        assert window.replay_page.review_widget.context["original_log"]["oracle"]=="L2"
        copied=window.replay_page.result.original.samples.copy()
        write_wav(root/"audio"/"clip.wav",audio([(.7,0)]))
        np.testing.assert_array_equal(window.replay_page.result.original.samples,copied)
        window.review_page.replay_button.click();pump_until(lambda:not window.operations.busy)
        assert window.replay_page.result is None and "checksum" in window.replay_page.message_label.text()
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_success_audio_setting_is_mirrored_saved_and_defaults_off(qt_app,tmp_path):
    factory=AudioFactory();window=make_window(tmp_path,factory)
    try:
        assert not window.review_page.success_checkbox.isChecked()
        window.review_page.success_checkbox.setChecked(True)
        assert window.replay_page.recognition.success_audio_checkbox.isChecked()
        settings=AppConfig.from_dict(read_json(window.data_root/"config.json"))
        assert settings.logging.success_audio and window.operations.recorder.settings.success_audio
        window.replay_page.recognition.success_audio_checkbox.setChecked(False)
        assert not window.review_page.success_checkbox.isChecked()
    finally:cleanup(window,factory)
