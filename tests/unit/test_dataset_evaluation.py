"""Label schema, denominator semantics, unbiased timestamp matching and safe reports."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
import numpy as np
import pytest

from encounter.vog_oracles import OracleId
from audio.data import AudioDataError,AudioClip
from audio.operations import OperationCancelled
from audio.waveio import write_wav
from evaluation.dataset import load_dataset,read_json
from evaluation.metrics import Prediction,score_case
from evaluation.runner import compare_reports,evaluate,export_report
from tests.unit.test_confidence import ranking
from tests.unit.test_event_detector import audio
from tests.unit.test_sequence_analyzer import StubClassifier,two_pass_clip
from tests.verification_helpers import RowsClassifier,rows_for


def manifest(tmp_path,cases=None,clip=None):
    write_wav(tmp_path/"audio.wav",clip or audio([(.3,0),(.2,.2),(.2,0)]))
    cases=cases or [{"id":"first","file":"audio.wav","mode":"clip","expected":["L1"]}]
    p=tmp_path/"dataset.json"; p.write_text(json.dumps({"schema_version":1,"cases":cases}),encoding="utf-8")
    return p


@pytest.mark.parametrize("change",[
    {"schema_version":True},{"unknown":1},{"cases":[]},
    {"cases":[{"file":"../escape.wav","expected":["L1"]}]},
    {"cases":[{"file":"audio.wav","expected":["BAD"]}]},
    {"cases":[{"file":"audio.wav","mode":"clip","expected":["L1","L2"]}]},
    {"cases":[{"file":"audio.wav","mode":"unknown","expected":[]}]},
    {"cases":[{"file":"audio.wav","expected":["L1"],"timestamps":[{"start":True,"end":1}]}]},
    {"cases":[{"file":"audio.wav","expected":["L1"],"timestamps":[{"start":.2,"end":.1}]}]},
    {"cases":[{"file":"audio.wav","expected":["L1"],"timestamps":[]}]},
    {"cases":[{"file":"audio.wav","mode":"round","expected":["L1","L1","L3"]}]},
    {"cases":[{"file":"audio.wav","mode":"round","round":True,"expected":["L1","L2","L3"]}]},
    {"cases":[{"id":"same","file":"audio.wav","expected":[]},{"id":"same","file":"audio.wav","expected":[]}]},
])
def test_invalid_truth_is_rejected_before_analysis(tmp_path,change):
    p=manifest(tmp_path); value=read_json(p); value.update(change)
    p.write_text(json.dumps(value),encoding="utf-8")
    with pytest.raises(AudioDataError):load_dataset(p)


@pytest.mark.parametrize("content",['{"a":1,"a":2}','{"a":NaN}','not JSON'])
def test_json_ambiguity_and_nonfinite_values_fail(tmp_path,content):
    p=tmp_path/"bad.json";p.write_text(content,encoding="utf-8")
    with pytest.raises(AudioDataError):read_json(p)


def test_unknown_missing_and_extra_are_distinct():
    scored=score_case(("L1","L2","L3"),(Prediction("L1",0,.1),Prediction(None,1,1.1)),
                      ((0,.1),(1,1.1),(2,2.1)),.05)
    c=scored["counts"]
    assert c["correct"]==1 and c["rejected"]==1 and c["missed"]==1 and c["false_positives"]==0
    assert scored["confusion_matrix"]["L2"]["UNKNOWN"]==1
    assert scored["confusion_matrix"]["L3"]["MISSING"]==1


def test_timestamp_pairing_does_not_use_predicted_oracle_to_hide_error():
    scored=score_case(("L1","L2"),(Prediction("L2",.3,.5),Prediction("L1",1,1.2)),
                      ((.3,.5),(1,1.2)),.05)
    assert scored["counts"]["incorrect"]==2 and scored["counts"]["correct"]==0
    outside=score_case(("L1",),(Prediction("L1",4,4.2),),((.3,.5),),.05)
    assert outside["counts"]["missed"]==1 and outside["counts"]["false_positives"]==1


def test_order_alignment_retains_rejected_and_extra_unknown():
    scored=score_case(("L1","L2"),(Prediction("L1",0,.1),Prediction(None,1,1.1),Prediction("L2",2,2.1)))
    assert scored["counts"]["correct"]==2 and scored["counts"]["extra_unknown"]==1
    assert scored["counts"]["rejected_predictions"]==1


def test_weighted_metrics_and_fixed_confusion_axes(tmp_path):
    cases=[{"id":"correct","file":"audio.wav","mode":"clip","expected":["L1"]},
           {"id":"incorrect","file":"audio.wav","mode":"clip","expected":["L2"]},
           {"id":"background","file":"audio.wav","mode":"clip","expected":[]}]
    path=manifest(tmp_path,cases)
    before=(tmp_path/"audio.wav").read_bytes()
    result=evaluate(load_dataset(path),StubClassifier(ranking(oracle=OracleId.L1)))
    m=result["metrics"]
    assert (m["total_events"],m["correct"],m["incorrect"],m["false_positives"])==(2,1,1,1)
    assert m["accuracy"]==.5 and m["precision"]==pytest.approx(1/3)
    assert m["false_positive_rate"]==1 and m["false_positives_per_minute"] is None
    assert result["confusion_matrix"]["NO_ORACLE"]["L1"]==1
    assert result["per_oracle"]["L2"]["accuracy"]==0
    assert result["per_oracle"]["R3"]["accuracy"] is None
    assert (tmp_path/"audio.wav").read_bytes()==before


def test_empty_denominators_are_unavailable_not_zero(tmp_path):
    path=manifest(tmp_path,[{"file":"audio.wav","mode":"events","expected":["L1"]}],audio([(.8,0)]))
    result=evaluate(load_dataset(path),StubClassifier(ranking(oracle=OracleId.L1)))
    m=result["metrics"]
    assert m["missed"]==1 and m["accuracy"]==0
    assert m["precision"] is None and m["rejection_rate"] is None and m["false_positive_rate"] is None


def test_fully_timed_recording_has_extra_accepted_per_minute(tmp_path):
    clip=audio([(.3,0),(.2,.2),(.3,0),(.2,.2),(.2,0)])
    path=manifest(tmp_path,[{"file":"audio.wav","expected":["L1"],"timestamps":[{"start":.3,"end":.5}]}],clip)
    result=evaluate(load_dataset(path),StubClassifier(ranking(oracle=OracleId.L1)))
    m=result["metrics"]
    assert m["correct"]==1 and m["false_positives"]==1
    assert m["false_positives_per_minute"]==pytest.approx(50)
    assert m["false_positive_rate"] is None


@pytest.mark.parametrize("status,accuracy",[("CONFIRMED",1),("INFERRED",0)])
def test_round_truth_scores_both_raw_passes_and_exact_confirmation(tmp_path,status,accuracy):
    path=manifest(tmp_path,[{"file":"audio.wav","mode":"round","expected":["L1","L2","L3"]}],two_pass_clip())
    dataset=load_dataset(path)
    assert dataset.cases[0].round_index==1
    report=evaluate(dataset,RowsClassifier(rows_for(status)))
    assert report["metrics"]["total_events"]==6
    assert report["metrics"]["final_sequence_accuracy"]==accuracy
    assert report["cases"][0]["round_verification"]["confirmed"]==bool(accuracy)


def test_baseline_requires_same_wav_truth_and_has_numeric_deltas(tmp_path):
    dataset=load_dataset(manifest(tmp_path))
    old=evaluate(dataset,StubClassifier(ranking(.83,.82)))
    current=evaluate(dataset,StubClassifier(ranking(oracle=OracleId.L1)))
    compared=compare_reports(current,old)
    assert compared["deltas"]["accuracy"]==1 and compared["deltas"]["rejection_rate"]==-1
    changed=deepcopy(old);changed["dataset_fingerprint"]="different"
    with pytest.raises(AudioDataError):compare_reports(current,changed)
    with pytest.raises(AudioDataError):compare_reports(current,[])


def test_report_export_is_complete_and_does_not_replace_existing(tmp_path):
    report=evaluate(load_dataset(manifest(tmp_path)),StubClassifier(ranking(oracle=OracleId.L1)))
    destination=tmp_path/"report"; path=export_report(report,destination)
    assert read_json(path)["dataset_fingerprint"]==report["dataset_fingerprint"]
    csv=(destination/"confusion-matrix.csv").read_text(encoding="utf-8-sig")
    assert "NO_ORACLE" in csv and "UNKNOWN" in csv and "MISSING" in csv
    before=path.read_bytes()
    with pytest.raises(AudioDataError):export_report(report,destination)
    assert path.read_bytes()==before


def test_cancel_export_and_mid_export_failure_leave_no_partial_bundle(tmp_path,monkeypatch):
    report=evaluate(load_dataset(manifest(tmp_path)),StubClassifier(ranking(oracle=OracleId.L1)))
    cancel=Event();cancel.set()
    with pytest.raises(OperationCancelled):export_report(report,tmp_path/"cancelled",cancel)
    import evaluation.runner as runner
    def fail(*args):raise OSError("disk failure")
    monkeypatch.setattr(runner.csv,"writer",fail)
    with pytest.raises(OSError):export_report(report,tmp_path/"failed")
    assert not (tmp_path/"failed").exists() and not list(tmp_path.glob(".evaluation-*"))


def test_mutating_wav_during_batch_fails_instead_of_publishing_metrics(tmp_path):
    dataset=load_dataset(manifest(tmp_path))
    class Changing(StubClassifier):
        def classify_preprocessed(self,event,cancel=None):
            dataset.cases[0].path.write_bytes(b"changed")
            return super().classify_preprocessed(event,cancel)
    with pytest.raises(AudioDataError,match="WAV"):evaluate(dataset,Changing())


def test_case_duration_and_truth_outside_wav_fail(tmp_path):
    path=manifest(tmp_path,[{"file":"audio.wav","expected":["L1"],"timestamps":[{"start":1,"end":2}]}])
    with pytest.raises(AudioDataError,match="時刻"):evaluate(load_dataset(path),StubClassifier(ranking(oracle=OracleId.L1)))
    path=manifest(tmp_path,clip=AudioClip(np.zeros(8000*11),8000))
    with pytest.raises(AudioDataError,match="10秒"):evaluate(load_dataset(path),StubClassifier(ranking(oracle=OracleId.L1)))

@pytest.mark.parametrize("winerror",[5,32,33])
def test_report_publication_retries_transient_windows_locks(tmp_path,monkeypatch,winerror):
    report=evaluate(load_dataset(manifest(tmp_path)),StubClassifier(ranking(oracle=OracleId.L1)))
    real=Path.rename; calls=[]
    def rename(source,destination):
        calls.append(source)
        if len(calls)<3:
            error=OSError("temporary lock");error.winerror=winerror;raise error
        return real(source,destination)
    monkeypatch.setattr(Path,"rename",rename)
    path=export_report(report,tmp_path/"retried")
    assert len(calls)==3 and path.exists()
