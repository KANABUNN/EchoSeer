"""Independent persistence toggles, exact native windows and recoverable storage errors."""
import json
from threading import Event
import numpy as np
import pytest
from audio.data import AudioClip
from audio.operations import OperationCancelled
from audio.waveio import read_wav
from config.schema import LoggingSettings
from detector.confidence import ConfidenceEngine,EventContext
from logging_ext.recognition_logger import RecognitionRecorder,uncertain_window
from tests.unit.test_confidence import ranking


def clip(seconds=.2):
    rate=8000
    values=np.arange(round(rate*seconds),dtype=np.float32)/round(rate*seconds)*.2
    return AudioClip(np.stack((values,values*.3+.01),axis=1),rate)


def record(recorder,raw=None,original=None,cancel=None,context=None):
    raw=raw or ranking(.83,.82)
    return recorder.record(ConfidenceEngine().evaluate(raw,context),raw,original or clip(),"test-hash",cancel)


@pytest.mark.parametrize("events,audio",[(False,False),(True,False),(False,True),(True,True)])
def test_logging_toggles_are_independent(tmp_path,events,audio):
    recorder=RecognitionRecorder(tmp_path/"logs",LoggingSettings(event_logs=events,uncertain_audio=audio))
    original=clip()
    result=record(recorder,original=original)
    assert bool(result.event_path)==events and bool(result.audio_path)==audio and not result.notices
    if not events and not audio:assert not (tmp_path/"logs").exists()
    if audio:
        saved=read_wav(result.audio_path)
        assert saved.sample_rate==original.sample_rate and saved.channels==2
        np.testing.assert_array_equal(saved.samples,original.samples)
    if events:
        data=json.loads(result.event_path.read_text(encoding="utf-8"))
        assert data["oracle"] is None and data["confidence_level"]=="LOW"
        assert data["best_candidate"]=="L2" and data["second_candidate"]=="L1"
        assert data["confidence"]==.83 and data["second_score"]==.82
        assert len(data["ranking"])==7 and data["checksum"]=="test-hash"
        assert bool(data["audio_path"])==audio
        assert data["timestamp"]>1_000_000_000 and data["event_time"]==0


def test_accepted_and_silent_events_do_not_save_uncertain_audio(tmp_path):
    from detector.classifier import ClassificationResult
    recorder=RecognitionRecorder(tmp_path/"logs")
    assert not record(recorder,ranking()).audio_path
    assert not record(recorder,ClassificationResult("SILENT")).audio_path
    assert len(recorder.events.path.read_text(encoding="utf-8").splitlines())==2


def test_duplicate_is_logged_without_forced_oracle_and_saves_audio(tmp_path):
    recorder=RecognitionRecorder(tmp_path/"logs")
    engine=ConfidenceEngine()
    raw=ranking()
    engine.evaluate(raw,EventContext("live",10,"capture"))
    decision=engine.evaluate(raw,EventContext("live",10.1,"capture"))
    result=recorder.record(decision,raw,clip(),"hash")
    data=json.loads(result.event_path.read_text(encoding="utf-8"))
    assert data["duplicate"] and data["oracle"] is None and data["confidence_level"]=="HIGH"
    assert data["source"]["stream_id"]=="capture" and data["event_time"]==10.1
    assert result.audio_path.exists()


def test_long_uncertain_window_is_bounded_and_keeps_native_samples():
    values=np.zeros((8000*8,2),np.float32)
    values[8000*5:8000*5+800]=(.8,.3)
    original=AudioClip(values,8000)
    window,start,end=uncertain_window(original)
    assert window.duration_seconds==3 and start<=8000*5<end and end-start==window.frame_count
    np.testing.assert_array_equal(window.samples,original.samples[start:end])


def test_audio_byte_limit_is_applied(tmp_path,monkeypatch):
    import logging_ext.recognition_logger as module
    monkeypatch.setattr(module,"MAX_AUDIO_BYTES",1024)
    original=clip(4)
    result=record(RecognitionRecorder(tmp_path/"logs"),original=original)
    saved=read_wav(result.audio_path)
    assert saved.samples.nbytes<=1024
    event=json.loads(result.event_path.read_text(encoding="utf-8"))
    start,end=event["audio_window"]["start_frame"],event["audio_window"]["end_frame"]
    np.testing.assert_array_equal(saved.samples,original.samples[start:end])


def test_rejected_candidate_saves_audio_but_empty_rejection_does_not(tmp_path):
    result=record(RecognitionRecorder(tmp_path/"logs"),ranking(.1,.09))
    assert result.audio_path and json.loads(result.event_path.read_text(encoding="utf-8"))["confidence_level"]=="REJECTED"


def test_wall_clock_changes_do_not_change_event_time(tmp_path,monkeypatch):
    import logging_ext.recognition_logger as module
    monkeypatch.setattr(module.time,"time",lambda:1_000_000_000.)
    result=record(RecognitionRecorder(tmp_path/"logs"),context=EventContext("live",10,"capture"))
    data=json.loads(result.event_path.read_text(encoding="utf-8"))
    assert data["timestamp"]==1_000_000_000. and data["event_time"]==10
    assert data["monotonic_time"]!=data["timestamp"]


@pytest.mark.parametrize("failure",["audio","events","directory"])
def test_storage_failure_returns_notice_and_preserves_decision(tmp_path,monkeypatch,failure):
    import logging_ext.recognition_logger as module
    recorder=RecognitionRecorder(tmp_path/"logs")
    def fail(*args,**kwargs):raise OSError("disk unavailable")
    if failure=="audio":monkeypatch.setattr(module,"write_wav",fail)
    elif failure=="events":monkeypatch.setattr(recorder.events,"write",fail)
    else:
        (tmp_path/"logs").write_text("existing unrelated data",encoding="utf-8")
    result=record(recorder)
    assert result.notices
    if failure=="audio":
        assert result.event_path and result.audio_path is None
        assert json.loads(result.event_path.read_text(encoding="utf-8"))["oracle"] is None
    elif failure=="events":assert result.audio_path and result.event_path is None
    else:assert (tmp_path/"logs").read_text(encoding="utf-8")=="existing unrelated data"


def test_cancellation_creates_no_partial_files(tmp_path):
    cancelled=Event();cancelled.set()
    with pytest.raises(OperationCancelled):record(RecognitionRecorder(tmp_path/"logs"),cancel=cancelled)
    assert not (tmp_path/"logs").exists()


def test_repeated_events_have_distinct_audio_files_and_valid_json_lines(tmp_path):
    recorder=RecognitionRecorder(tmp_path/"logs")
    first,second=record(recorder),record(recorder)
    assert first.audio_path!=second.audio_path
    lines=[json.loads(line) for line in recorder.events.path.read_text(encoding="utf-8").splitlines()]
    assert len(lines)==2 and lines[0]["event_id"]!=lines[1]["event_id"]

def test_invalid_ranking_still_has_a_valid_rejection_log(tmp_path):
    from dataclasses import replace
    raw=ranking()
    raw=replace(raw,ranking=(replace(raw.ranking[0],score=float("nan")),*raw.ranking[1:]))
    result=record(RecognitionRecorder(tmp_path/"logs"),raw)
    data=json.loads(result.event_path.read_text(encoding="utf-8"))
    assert data["reason"]=="INVALID_RANKING" and data["oracle"] is None
    assert data["ranking"]==[] and data["confidence"] is None and not result.audio_path
