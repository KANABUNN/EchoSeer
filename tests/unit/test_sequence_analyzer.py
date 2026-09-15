"""Unknown positions, sliced event classification, persistence and finite source clocks."""
import json
from dataclasses import replace
import numpy as np
import pytest
from audio.sources import ClipSource
from audio.event import EventContext
from audio.data import AudioClip,AudioDataError
from config.schema import LoggingSettings, RecognitionSettings
from detector.classifier import OracleClassifier
from detector.events import EventWindow
from detector.onset_matcher import TemplateOnsetMatcher, build_optional_onset_matcher
from encounter.vog_oracles import OracleId
from logging_ext.recognition_logger import RecognitionRecorder
from replay.analyzer import Analyzer
from replay.sequence_analyzer import ReplaySequenceAnalyzer
from tests.unit.test_confidence import ranking
from tests.unit.test_event_detector import audio
from tests.unit.test_onset_matcher import pattern
from templates.manager import TemplateManager


class StubClassifier(OracleClassifier):
    def __init__(self,raw=None,manager=None,templates=None):
        super().__init__(manager=manager,sample_rate=8000,templates=templates)
        self.raw=raw or ranking()
        self.frame_counts=[]
    def classify_preprocessed(self,event,cancel=None):
        self.frame_counts.append(event.frame_count)
        return self.raw


class QueueClassifier(StubClassifier):
    def __init__(self, rows):
        super().__init__()
        self.rows = iter(rows)

    def classify_preprocessed(self, event, cancel=None):
        self.frame_counts.append(event.frame_count)
        return next(self.rows)


def two_pass_clip():
    return audio([(.3,0),(.2,.2),(.5,0),(.2,.2),(.5,0),(.2,.2),
                  (2.6,0),(.2,.2),(.5,0),(.2,.2),(.5,0),(.2,.2),(.3,0)])


def analyze(classifier,clip=None,recorder=None,context=None):
    clip=clip or two_pass_clip()
    analyzer=Analyzer(8000)
    analysis=analyzer.analyze(ClipSource(clip))
    return ReplaySequenceAnalyzer(classifier,recorder=recorder,analyzer=analyzer).analyze(analysis,source_context=context)


def scheduled_windows(specs):
    cue = audio([(.2, .2)])
    result = []
    for spec in specs:
        seconds, matched, *remainder = spec
        reason = remainder[0] if remainder else ""
        onset = round(seconds * 8000)
        end = onset + cue.frame_count
        result.append(EventWindow(
            cue, onset, end, onset, end, reason=reason, matched=matched,
        ))
    return tuple(result)


def analyze_scheduled(monkeypatch, classifier, specs, recognition=None, live=False):
    import replay.sequence_analyzer as module

    marker = object()
    windows = scheduled_windows(specs)

    def detect(self, original, cancel=None, onset_matcher=None):
        assert onset_matcher is marker
        yield from windows

    monkeypatch.setattr(
        module, "build_optional_onset_matcher", lambda *args, **kwargs: marker,
    )
    monkeypatch.setattr(module.RmsEventDetector, "detect", detect)
    frames = windows[-1].end_frame + 800
    clip = AudioClip(np.zeros((frames, 1), dtype=np.float32), 8000)
    analyzer = Analyzer(8000)
    analysis = analyzer.analyze(ClipSource(clip))
    context = (
        EventContext("live", clip.duration_seconds, "scheduled", 0, frames)
        if live else None
    )
    return ReplaySequenceAnalyzer(
        classifier, recognition=recognition, analyzer=analyzer,
    ).analyze(analysis, source_context=context)


def test_sequence_replay_falls_back_without_templates_and_uses_manager_matcher(
        tmp_path, monkeypatch):
    import replay.sequence_analyzer as module

    captured = []
    fed = []

    def build(*args, **kwargs):
        matcher = build_optional_onset_matcher(*args, **kwargs)
        captured.append(matcher)
        if matcher is not None:
            original = matcher.feed

            def feed(samples, cancel=None):
                fed.append(len(samples))
                return original(samples, cancel)

            matcher.feed = feed
        return matcher

    monkeypatch.setattr(module, "build_optional_onset_matcher", build)
    assert len(analyze(StubClassifier()).traces) == 6
    assert captured.pop() is None

    manager = TemplateManager(tmp_path / "templates", sample_rate=8000)
    manager.add("L1", pattern(101))
    manager.add("R3", pattern(103))
    assert len(analyze(StubClassifier(manager=manager)).traces) == 6
    matcher = captured.pop()
    assert isinstance(matcher, TemplateOnsetMatcher)
    assert sum(fed) > 0


def test_matcher_mode_ignores_rms_low_but_keeps_matched_low_position(monkeypatch):
    classifier = QueueClassifier((
        ranking(oracle=OracleId.L1),
        ranking(.83, .82, OracleId.R3),
        ranking(.83, .82, OracleId.R3),
        ranking(oracle=OracleId.L2),
    ))
    result = analyze_scheduled(
        monkeypatch, classifier,
        ((.4, False), (.9, False), (1.4, True), (1.9, False)),
    )

    assert len(result.traces) == 4
    assert result.snapshot.ignored_events == 1
    assert [item.oracle for item in result.snapshot.pass1] == [
        OracleId.L1, None, OracleId.L2,
    ]
    assert result.snapshot.pass1[1].detection.status == "LOW"


def test_matcher_mode_armed_ignores_unmatched_low_but_starts_from_matched_low(
        monkeypatch):
    classifier = QueueClassifier((
        ranking(.83, .82, OracleId.R3),
        ranking(.83, .82, OracleId.R3),
    ))
    result = analyze_scheduled(
        monkeypatch, classifier,
        ((.4, False), (.9, True)),
    )

    assert [trace.detection.status for trace in result.traces] == ["LOW", "LOW"]
    assert result.snapshot.ignored_events == 1
    assert [item.oracle for item in result.snapshot.pass1] == [None]
    assert not result.snapshot.pass2


def test_replay_restarts_false_singleton_without_duplicate_poisoning(monkeypatch):
    order = (
        OracleId.L3,
        OracleId.L1, OracleId.L2, OracleId.L3,
        OracleId.L1, OracleId.L2, OracleId.L3,
    )
    classifier = QueueClassifier(tuple(ranking(oracle=item) for item in order))
    settings = RecognitionSettings(duplicate_cooldown=10)
    result = analyze_scheduled(
        monkeypatch, classifier,
        (
            (.4, False),
            (2.8, True), (3.4, True), (4.0, True),
            (6.2, True), (6.8, True), (7.4, True),
        ),
        settings, live=True,
    )

    assert result.snapshot.confirmed
    assert result.snapshot.ignored_events == 1
    assert [item.oracle for item in result.snapshot.pass1] == [
        OracleId.L1, OracleId.L2, OracleId.L3,
    ]
    assert [item.oracle for item in result.snapshot.pass2] == [
        OracleId.L1, OracleId.L2, OracleId.L3,
    ]
    assert all(not trace.detection.duplicate for trace in result.traces[1:])
    assert any(
        transition.reason == "PASS_1_RESTARTED"
        for transition in result.snapshot.transitions
    )


@pytest.mark.parametrize(
    "reason", ["CLIPPED_EVENT", "SHORT_EVENT", "EVENT_LIMIT"],
)
def test_replay_structural_window_does_not_poison_same_oracle_duplicate_history(
        monkeypatch, reason):
    classifier = QueueClassifier((
        ranking(oracle=OracleId.L2),
        ranking(oracle=OracleId.L1),
        ranking(oracle=OracleId.L1),
    ))
    result = analyze_scheduled(
        monkeypatch, classifier,
        ((.4, True), (.9, True, reason), (1.4, True)),
        RecognitionSettings(duplicate_cooldown=10), live=True,
    )

    structural, following = result.traces[1:]
    assert structural.detection.reason == reason
    assert structural.detection.status == "REJECTED"
    assert structural.detection.oracle is None
    assert structural.onset_matched
    assert following.detection.oracle == OracleId.L1
    assert not following.detection.duplicate
    assert [item.oracle for item in result.snapshot.pass1] == [
        OracleId.L2, None, OracleId.L1,
    ]


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
    assert all(e["onset_detection"] == {
        "matched": False, "candidate": None, "score": None, "margin": None,
    } for e in cues)
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
