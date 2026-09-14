"""Opt-in accepted audio and bounded, once-per-cue pass-problem evidence."""
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest
from audio.sources import ClipSource
from audio.waveio import read_wav
from config.schema import LoggingSettings
from detector.confidence import ConfidenceEngine
from logging_ext.evidence import CueEvidence,CueEvidenceCache
from logging_ext.recognition_logger import RecognitionRecorder
from replay.analyzer import Analyzer
from templates.manager import audio_checksum
from tests.unit.test_event_detector import audio
from tests.unit.test_confidence import ranking


def cue(index=1,pass_number=1):
    clip=audio([(.3,.2)])
    raw=ranking()
    decision=ConfidenceEngine().evaluate(raw)
    return CueEvidence(clip,"checksum",decision,raw,1,pass_number,index)


def test_accepted_audio_defaults_off_and_opt_in_preserves_native(tmp_path):
    item=cue()
    recorder=RecognitionRecorder(tmp_path,LoggingSettings(uncertain_audio=False))
    assert recorder.record(item.detection,item.classification,item.clip,item.checksum).audio_path is None
    recorder.settings.success_audio=True
    saved=recorder.record(item.detection,item.classification,item.clip,item.checksum)
    assert saved.audio_path is not None
    payload=json.loads(recorder.events.path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["audio_native_checksum"]==audio_checksum(read_wav(saved.audio_path))
    assert payload["audio_save_reason"]=="accepted_example"
    assert payload["confidence_level"]=="HIGH"


def test_accepted_capture_does_not_enable_low_capture_when_uncertain_off(tmp_path):
    item=cue();raw=ranking(.83,.82);decision=ConfidenceEngine().evaluate(raw)
    recorder=RecognitionRecorder(tmp_path,LoggingSettings(event_logs=False,uncertain_audio=False,success_audio=True))
    assert recorder.record(decision,raw,item.clip,"checksum").audio_path is None
    assert not list(tmp_path.rglob("*.wav"))


def test_problem_keeps_high_decision_and_saves_audio_evidence(tmp_path):
    item=cue(2)
    recorder=RecognitionRecorder(tmp_path)
    saved=recorder.record(item.detection,item.classification,item.clip,item.checksum,
                          sequence={"round":1,"pass":2,"index":2},problem_reason="PASS_MISMATCH")
    p=json.loads(recorder.events.path.read_text(encoding="utf-8").splitlines()[-1])
    assert saved.audio_path and p["event_type"]=="audio_evidence"
    assert p["oracle"]=="L2" and p["confidence_level"]=="HIGH" and p["index"]==2


def test_cache_budget_and_clear_are_bounded_owned_references():
    first=cue();cache=CueEvidenceCache(first.clip.samples.nbytes*2)
    for i in range(20):
        cache.add(cue(i))
        assert cache.retained_bytes<=cache.max_bytes
    assert cache.latest.index==19 and cache.evicted==18
    assert not cache.latest.clip.samples.flags.writeable
    cache.clear();assert cache.latest is None and cache.retained_bytes==0


def test_conflict_saves_only_affected_indices_once_even_after_repeated_fault(tmp_path):
    cache=CueEvidenceCache()
    for pass_number in (1,2):
        for index in (1,2,3):cache.add(cue(index,pass_number))
    snapshot=SimpleNamespace(confirmed=False,verification=SimpleNamespace(mismatch_indices=(2,),status=SimpleNamespace(value="MISMATCH")),
                             state=SimpleNamespace(value="UNCERTAIN"),reason="CONFLICT")
    recorder=RecognitionRecorder(tmp_path)
    assert not cache.save_problem(snapshot,recorder)
    assert len(list(tmp_path.rglob("*.wav")))==2
    cache.save_problem(snapshot,recorder)
    assert len(list(tmp_path.rglob("*.wav")))==2
    records=[json.loads(row) for row in recorder.events.path.read_text(encoding="utf-8").splitlines()]
    assert all(row["event_type"]=="audio_evidence" and row["index"]==2 for row in records)


def test_both_audio_switches_off_do_not_write_problem_wavs(tmp_path):
    cache=CueEvidenceCache();cache.add(cue())
    recorder=RecognitionRecorder(tmp_path,LoggingSettings(event_logs=False,uncertain_audio=False))
    snapshot=SimpleNamespace(confirmed=False)
    assert not cache.save_problem(snapshot,recorder)
    assert not list(tmp_path.rglob("*"))
