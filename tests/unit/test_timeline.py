"""Replay all events, native clocks, immutable slices and repeatable conditions."""
from dataclasses import replace
import json
from threading import Event
import numpy as np
import pytest
from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio.sources import ClipSource
from config.schema import LoggingSettings, RecognitionSettings
from detector.classifier import TemplateWaveform
from detector.events import EventWindow
from detector.onset_matcher import TemplateOnsetMatcher, build_optional_onset_matcher
from encounter.vog_oracles import OracleId
from logging_ext.recognition_logger import RecognitionRecorder
from replay.analyzer import Analyzer
from replay.timeline import ReplayTimelineAnalyzer, cue_clip
from tests.unit.test_sequence_analyzer import QueueClassifier, StubClassifier, two_pass_clip
from tests.unit.test_confidence import ranking
from tests.unit.test_onset_matcher import classifier as onset_classifier


def analyze(classifier, clip=None, settings=None):
    source = Analyzer(8000).analyze(ClipSource(clip or two_pass_clip()))
    return source, ReplayTimelineAnalyzer(classifier, settings).analyze(source)


def test_full_timeline_continues_after_unknown_and_fsm_length():
    clip = two_pass_clip()
    doubled = AudioClip(np.concatenate([clip.samples, clip.samples]), 8000)
    source, result = analyze(StubClassifier(ranking(.83, .82)), doubled)
    assert len(result.traces) == 12
    assert all(trace.detection.oracle is None for trace in result.traces)
    assert result.traces[6].onset_frame > clip.frame_count
    assert all(cue_clip(source.original, trace).duration_seconds < 1 for trace in result.traces)


def test_repeated_audio_and_conditions_produce_same_events_and_hash():
    source, first = analyze(StubClassifier())
    _, second = analyze(StubClassifier())
    assert first == second and first.profile_hash == second.profile_hash
    assert first.profile["engine"] == "adaptive-rms-template-onset-waveform-spectrum-v3"
    assert first.traces[0].onset_frame == 2400
    slice = cue_clip(source.original, first.traces[0])
    np.testing.assert_array_equal(slice.samples, source.original.samples[first.traces[0].start_frame:first.traces[0].end_frame])
    assert not slice.samples.flags.writeable


def test_timeline_event_log_records_coarse_onset_scores(tmp_path, monkeypatch):
    import replay.timeline as module

    cue = AudioClip(np.full((1600, 1), .2, dtype=np.float32), 8000)
    window = EventWindow(
        cue, 800, 2400, 800, 2400, matched=True,
        match_oracle="L2", match_score=.931, match_margin=.42,
    )

    def detect(self, original, cancel=None, onset_matcher=None):
        yield window

    monkeypatch.setattr(module.RmsEventDetector, "detect", detect)
    recorder = RecognitionRecorder(
        tmp_path/"logs", LoggingSettings(uncertain_audio=False),
    )
    analysis = Analyzer(8000).analyze(ClipSource(
        AudioClip(np.zeros((3200, 1), dtype=np.float32), 8000),
    ))
    result = ReplayTimelineAnalyzer(
        StubClassifier(), analyzer=Analyzer(8000), recorder=recorder,
    ).analyze(analysis)
    payload = json.loads(
        recorder.events.path.read_text(encoding="utf-8").splitlines()[0]
    )

    assert payload["onset_detection"] == {
        "matched": True, "candidate": "L2",
        "score": .931, "margin": .42,
    }
    assert result.traces[0].onset_match_oracle == "L2"
    assert result.traces[0].onset_match_score == pytest.approx(.931)
    assert result.traces[0].onset_match_margin == pytest.approx(.42)


def test_timeline_replay_falls_back_without_templates_and_uses_in_memory_matcher(
        monkeypatch):
    import replay.timeline as module

    captured = []

    def build(*args, **kwargs):
        matcher = build_optional_onset_matcher(*args, **kwargs)
        captured.append(matcher)
        return matcher

    monkeypatch.setattr(module, "build_optional_onset_matcher", build)
    assert len(analyze(StubClassifier())[1].traces) == 6
    assert captured.pop() is None

    source, _ = onset_classifier()
    classifier = StubClassifier(templates=source.templates)
    assert len(analyze(classifier)[1].traces) == 6
    matcher = captured.pop()
    assert isinstance(matcher, TemplateOnsetMatcher)
    assert matcher.retained_frames == 0
    with pytest.raises(AudioDataError):
        matcher.feed(np.zeros((1, 2), dtype=np.float32))


def test_changed_recognition_is_explicitly_a_different_condition():
    _, first = analyze(StubClassifier())
    _, changed = analyze(StubClassifier(), settings=RecognitionSettings(confidence_threshold=.99, high_confidence_threshold=.99))
    assert first.profile_hash != changed.profile_hash
    assert changed.profile["recognition"]["confidence_threshold"] == .99


def test_event_cut_at_eof_remains_rejected_despite_high_score():
    clip = two_pass_clip()
    _, result = analyze(StubClassifier(), AudioClip(clip.samples[:-3200], 8000))
    assert result.traces[-1].detection.reason == "CLIPPED_EVENT"
    assert result.traces[-1].detection.oracle is None


@pytest.mark.parametrize(
    "reason", ["CLIPPED_EVENT", "SHORT_EVENT", "EVENT_LIMIT"],
)
def test_timeline_structural_window_does_not_poison_same_oracle_duplicate_history(
        monkeypatch, reason):
    import replay.timeline as module

    cue = AudioClip(np.full((1600, 1), .2, dtype=np.float32), 8000)
    windows = (
        EventWindow(
            cue, 800, 2400, 800, 2400,
            reason=reason, matched=True,
        ),
        EventWindow(cue, 4800, 6400, 4800, 6400, matched=True),
    )

    def detect(self, original, cancel=None, onset_matcher=None):
        yield from windows

    monkeypatch.setattr(module.RmsEventDetector, "detect", detect)
    actual_confidence = module.ConfidenceEngine
    instances = []

    class TrackingConfidence(actual_confidence):
        def __init__(self, settings=None):
            super().__init__(settings)
            self.timestamps = []
            instances.append(self)

        def evaluate(self, raw, context=None):
            self.timestamps.append(context.timestamp)
            return super().evaluate(raw, context)

    monkeypatch.setattr(module, "ConfidenceEngine", TrackingConfidence)
    classifier = QueueClassifier((
        ranking(oracle=OracleId.L1),
        ranking(oracle=OracleId.L1),
    ))
    original = AudioClip(np.zeros((7200, 1), dtype=np.float32), 8000)
    _, result = analyze(
        classifier, original,
        RecognitionSettings(duplicate_cooldown=10),
    )

    structural, following = result.traces
    assert structural.detection.reason == reason
    assert structural.detection.status == "REJECTED"
    assert structural.detection.oracle is None
    assert structural.onset_matched
    assert following.detection.oracle == OracleId.L1
    assert not following.detection.duplicate
    assert len(instances) == 2
    assert instances[0].timestamps == [pytest.approx(.6)]
    assert instances[1].timestamps == [pytest.approx(.1)]


def test_template_changes_during_analysis_are_not_comparable():
    class Changing(StubClassifier):
        def classify_preprocessed(self, event, cancel=None):
            self.templates = (TemplateWaveform(OracleId.L1, "new", event),)
            return super().classify_preprocessed(event, cancel)
    with pytest.raises(AudioDataError, match="テンプレート"):
        analyze(Changing())


def test_timeline_cancel_and_duration_limit():
    analyzer=Analyzer(8000)
    source=analyzer.analyze(ClipSource(two_pass_clip()))
    cancelled=Event(); cancelled.set()
    with pytest.raises(OperationCancelled):
        ReplayTimelineAnalyzer(StubClassifier()).analyze(source,cancelled)
    long=analyzer.analyze(ClipSource(AudioClip(np.zeros(8000*121),8000)))
    with pytest.raises(AudioDataError,match="120"):
        ReplayTimelineAnalyzer(StubClassifier()).analyze(long)


def test_invalid_selection_does_not_return_another_sound():
    source, result=analyze(StubClassifier())
    trace=replace(result.traces[0],end_frame=source.original.frame_count+1)
    with pytest.raises(AudioDataError):
        cue_clip(source.original,trace)
