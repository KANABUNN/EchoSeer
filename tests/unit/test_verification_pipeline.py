"""Verification data, raw candidate history and confirmed versus suggested persistence."""
import json
from audio.sources import ClipSource
from config.schema import LoggingSettings
from logging_ext.recognition_logger import RecognitionRecorder
from replay.analyzer import Analyzer
from replay.sequence_analyzer import ReplaySequenceAnalyzer
from tests.verification_helpers import RowsClassifier,rows_for
from tests.unit.test_sequence_analyzer import two_pass_clip


def run(status,tmp_path):
    recorder=RecognitionRecorder(tmp_path/"logs",LoggingSettings(uncertain_audio=False))
    analyzer=Analyzer(8000)
    pipeline=ReplaySequenceAnalyzer(RowsClassifier(rows_for(status)),recorder=recorder,analyzer=analyzer)
    result=pipeline.analyze(analyzer.analyze(ClipSource(two_pass_clip())))
    records=[json.loads(line) for line in recorder.events.path.read_text(encoding="utf-8").splitlines()]
    return result,records


def test_exact_matches_have_confirmed_sequence_in_jsonl(tmp_path):
    result,records=run("CONFIRMED",tmp_path)
    summary=records[-1]
    assert result.snapshot.state=="CONFIRMED" and summary["confirmed"]
    assert summary["final_sequence"]==["L1","L2","L3"] and summary["suggested_sequence"] is None
    assert summary["verification"]["status"]=="CONFIRMED"
    assert len(summary["verification"]["history"]["pass1"])==3
    assert [record["index"] for record in records[:-1]]==[1,2,3,1,2,3]
    assert [item["to"] for item in summary["transitions"]][-2:]==["VERIFY","CONFIRMED"]


def test_inference_logs_top_n_and_original_error_with_no_confirmed_sequence(tmp_path):
    result,records=run("INFERRED",tmp_path)
    summary=records[-1]
    assert not summary["confirmed"] and summary["final_sequence"] is None
    assert summary["suggested_sequence"]==["L1","L2","L3"]
    comparison=summary["verification"]
    assert comparison["status"]=="INFERRED" and comparison["mismatch_indices"]==[3]
    assert comparison["correction_indices"]==[3]
    assert summary["pass1"][2]["oracle"] is None and summary["pass1"][2]["best_candidate"]=="L2"
    assert comparison["history"]["pass1"][2]["candidates"][0]["oracle"]=="L2"
    assert comparison["history"]["pass1"][2]["candidates"][1]["oracle"]=="L3"
    assert comparison["reconstruction"]["sequence"]==["L1","L2","L3"]
    assert result.snapshot.pass1[2].classification.best_candidate.value=="L2"


def test_ambiguous_candidate_proposals_are_diagnostic_and_not_suggested(tmp_path):
    result,records=run("CHECK",tmp_path)
    summary=records[-1]
    assert not summary["confirmed"] and summary["suggested_sequence"] is None
    assert summary["verification"]["reconstruction"]["margin"]==0
    assert summary["verification"]["reconstruction"]["runner_up"] is not None
    assert result.snapshot.suggested_sequence is None


def test_valid_second_live_pass_is_not_suppressed_by_long_duplicate_cooldown():
    from audio.event import EventContext
    from config.schema import RecognitionSettings
    clip=two_pass_clip()
    analyzer=Analyzer(8000)
    settings=RecognitionSettings(duplicate_cooldown=10)
    pipeline=ReplaySequenceAnalyzer(RowsClassifier(rows_for("CONFIRMED")),
                                   recognition=settings,analyzer=analyzer)
    source=EventContext("live",100,"capture",0,clip.frame_count)
    result=pipeline.analyze(analyzer.analyze(ClipSource(clip)),source_context=source)
    assert result.snapshot.confirmed and result.snapshot.final_sequence is not None
    assert len(result.snapshot.pass1)==len(result.snapshot.pass2)==3
    assert all(not trace.detection.duplicate for trace in result.traces)
