"""Unknown positions, sliced event classification, persistence and finite source clocks."""
import json
from dataclasses import replace
import numpy as np
import pytest
from audio.sources import ClipSource
from audio.event import EventContext
from audio.data import AudioClip,AudioDataError
from config.schema import LoggingSettings
from detector.classifier import OracleClassifier
from logging_ext.recognition_logger import RecognitionRecorder
from replay.analyzer import Analyzer
from replay.sequence_analyzer import ReplaySequenceAnalyzer
from tests.unit.test_confidence import ranking
from tests.unit.test_event_detector import audio


class StubClassifier(OracleClassifier):
    def __init__(self,raw=None):
        super().__init__(sample_rate=8000)
        self.raw=raw or ranking()
        self.frame_counts=[]
    def classify_preprocessed(self,event,cancel=None):
        self.frame_counts.append(event.frame_count)
        return self.raw


def two_pass_clip():
    return audio([(.3,0),(.2,.2),(.5,0),(.2,.2),(.5,0),(.2,.2),
                  (2.6,0),(.2,.2),(.5,0),(.2,.2),(.5,0),(.2,.2),(.3,0)])


def analyze(classifier,clip=None,recorder=None,context=None):
    clip=clip or two_pass_clip()
    analyzer=Analyzer(8000)
    analysis=analyzer.analyze(ClipSource(clip))
    return ReplaySequenceAnalyzer(classifier,recorder=recorder,analyzer=analyzer).analyze(analysis,source_context=context)


def test_duplicate_candidates_are_classified_as_windows_and_do_not_confirm():
    classifier=StubClassifier()
    result=analyze(classifier)
    assert result.snapshot.state=="UNCERTAIN" and not result.snapshot.confirmed
    assert len(result.traces)==6 and len(result.snapshot.pass1)==len(result.snapshot.pass2)==3
    assert max(classifier.frame_counts)<8000
    assert all(count>0 for count in classifier.frame_counts)


def test_low_confidence_events_keep_six_unknown_positions():
    result=analyze(StubClassifier(ranking(.83,.82)))
    assert result.snapshot.state=="UNCERTAIN" and result.snapshot.verification.status=="CHECK"
    assert len(result.snapshot.pass1)==len(result.snapshot.pass2)==3
    assert all(e.oracle is None for e in (*result.snapshot.pass1,*result.snapshot.pass2))


def test_truncated_event_cannot_be_accepted_with_a_high_raw_score():
    original=two_pass_clip()
    original=AudioClip(original.samples[:-round(.4*8000)],8000)
    result=analyze(StubClassifier(),original)
    assert result.snapshot.state=="UNCERTAIN"
    assert result.traces[-1].detection.reason=="CLIPPED_EVENT"
    assert result.traces[-1].detection.oracle is None


def test_sequence_summary_and_each_event_are_saved_separately(tmp_path):
    recorder=RecognitionRecorder(tmp_path/"logs",LoggingSettings(uncertain_audio=False))
    result=analyze(StubClassifier(),recorder=recorder)
    data=[json.loads(line) for line in recorder.events.path.read_text(encoding="utf-8").splitlines()]
    cues=[line for line in data if line["event_type"]=="oracle"]
    summary=next(line for line in data if line["event_type"]=="sequence")
    assert [e["pass"] for e in cues]==[1,1,1,2,2,2]
    assert [e["index"] for e in cues]==[1,2,3,1,2,3]
    assert all(e["round"]==1 for e in cues)
    assert len(summary["pass1"])==len(summary["pass2"])==3
    assert summary["state"]=="UNCERTAIN" and not summary["confirmed"] and summary["final_sequence"] is None
    assert not result.notices


def test_live_snapshot_clock_and_frames_are_converted_without_touching_external_history():
    clip=two_pass_clip()
    context=EventContext("live",100,"capture",8000,8000+clip.frame_count)
    result=analyze(StubClassifier(),clip,context=context)
    assert result.snapshot.state=="UNCERTAIN"
    event=result.traces[0].detection
    assert event.context.source=="live" and event.context.stream_id=="capture"
    assert event.context.start_frame==8000+result.traces[0].start_frame
    assert event.timestamp==pytest.approx(100-clip.duration_seconds+.3)


def test_logging_off_creates_no_recognition_files(tmp_path):
    recorder=RecognitionRecorder(tmp_path/"logs",LoggingSettings(event_logs=False,uncertain_audio=False))
    assert analyze(StubClassifier(),recorder=recorder).snapshot.state=="UNCERTAIN"
    assert not (tmp_path/"logs").exists()


def test_summary_write_failure_does_not_lose_passes(tmp_path,monkeypatch):
    recorder=RecognitionRecorder(tmp_path/"logs",LoggingSettings(uncertain_audio=False))
    real=recorder.events.write
    def write(event):
        if event["event_type"]=="sequence":raise OSError("disk unavailable")
        real(event)
    monkeypatch.setattr(recorder.events,"write",write)
    result=analyze(StubClassifier(),recorder=recorder)
    assert result.snapshot.state=="UNCERTAIN" and len(result.snapshot.pass1)==3
    assert result.notices


def test_excess_duration_is_refused_before_classification():
    clip=AudioClip(np.zeros((8000*121,1),np.float32),8000)
    with pytest.raises(AudioDataError):analyze(StubClassifier(),clip)


def test_below_low_sound_events_do_not_consume_sequence_positions():
    result = analyze(StubClassifier(ranking(.2, .1)))
    assert len(result.traces) == 6
    assert not result.snapshot.pass1 and not result.snapshot.pass2
    assert result.snapshot.ignored_events == 6
    assert result.snapshot.reason == "NO_EVENTS"
