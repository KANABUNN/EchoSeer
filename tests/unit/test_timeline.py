"""Replay all events, native clocks, immutable slices and repeatable conditions."""
from dataclasses import replace
from threading import Event
import numpy as np
import pytest
from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio.sources import ClipSource
from config.schema import RecognitionSettings
from detector.classifier import TemplateWaveform
from encounter.vog_oracles import OracleId
from replay.analyzer import Analyzer
from replay.timeline import ReplayTimelineAnalyzer, cue_clip
from tests.unit.test_sequence_analyzer import StubClassifier, two_pass_clip
from tests.unit.test_confidence import ranking


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
    assert first.traces[0].onset_frame == 2400
    slice = cue_clip(source.original, first.traces[0])
    np.testing.assert_array_equal(slice.samples, source.original.samples[first.traces[0].start_frame:first.traces[0].end_frame])
    assert not slice.samples.flags.writeable


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
