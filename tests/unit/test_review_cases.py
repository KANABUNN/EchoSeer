"""Native case packages keep manual truth independent and publish complete snapshots."""
from threading import Event
import json
import numpy as np
import pytest
from audio.data import AudioDataError
from audio.operations import OperationCancelled
from audio.sources import ClipSource
from audio.waveio import read_wav
from evaluation.dataset import load_dataset,read_json
from evaluation.runner import evaluate,compare_reports
from review.cases import ReviewCaseStore
from tests.unit.test_event_detector import audio
from tests.unit.test_sequence_analyzer import StubClassifier


def annotation(expected=("L3",),mode="clip"):
    return {"mode":mode,"expected":list(expected),"category":"incorrect","source_kind":"synthetic","notes":"manual"}


def test_manual_truth_is_not_observed_oracle_and_native_slice_is_exact(tmp_path):
    source=audio([(.2,0),(.3,.2),(.3,0)],44100,2)
    before=source.samples.copy()
    store=ReviewCaseStore(tmp_path/"review"/"cases")
    path=store.save(ClipSource(source),annotation(),{"oracle":"L2"},bounds=(200,10000))
    dataset=load_dataset(path)
    assert dataset.cases[0].expected==("L3",)
    clip=read_wav(dataset.cases[0].path)
    assert clip.sample_rate==44100 and clip.channels==2
    np.testing.assert_array_equal(clip.samples,before[200:10000])
    np.testing.assert_array_equal(source.samples,before)
    assert read_json(path.with_name("analysis.json"))["observed"]["oracle"]=="L2"
    assert evaluate(dataset,StubClassifier())["metrics"]["incorrect"]==1


def test_background_truth_and_repeated_corpus_snapshots_are_stable(tmp_path):
    store=ReviewCaseStore(tmp_path/"review"/"cases")
    source=ClipSource(audio([(.3,.1)]))
    store.save(source,annotation(("L2",)))
    store.save(source,annotation(()))
    first,second=store.snapshot_dataset(),store.snapshot_dataset()
    assert first!=second
    old=evaluate(load_dataset(first),StubClassifier())
    current=evaluate(load_dataset(second),StubClassifier())
    assert compare_reports(current,old)["deltas"]["accuracy"]==0
    assert len(current["cases"])==2
    assert all(case["source_kind"]=="synthetic" for case in current["cases"])


@pytest.mark.parametrize("truth",[{"mode":"clip","expected":["L1","L2"]},{"mode":"round","expected":["L1","L1","L3"]},{"mode":"clip"}])
def test_invalid_truth_does_not_publish_audio_package(tmp_path,truth):
    store=ReviewCaseStore(tmp_path/"cases")
    with pytest.raises(AudioDataError):store.save(ClipSource(audio([(.3,.1)])),truth)
    assert not list(store.root.iterdir())


def test_invalid_span_and_cancel_leave_source_and_existing_cases(tmp_path):
    store=ReviewCaseStore(tmp_path/"cases");source=ClipSource(audio([(.3,.1)]))
    saved=store.save(source,annotation()); before=saved.read_bytes()
    with pytest.raises(AudioDataError):store.save(source,annotation(),bounds=(-1,5))
    cancel=Event();cancel.set()
    with pytest.raises(OperationCancelled):store.save(source,annotation(),cancel=cancel)
    assert saved.read_bytes()==before and len(list(store.root.iterdir()))==1


def test_empty_corpus_and_case_limit_fail_instead_of_empty_metrics(tmp_path,monkeypatch):
    store=ReviewCaseStore(tmp_path/"review"/"cases")
    with pytest.raises(AudioDataError):store.snapshot_dataset()
    store.save(ClipSource(audio([(.3,.1)])),annotation())
    import review.cases as module
    monkeypatch.setattr(module,"MAX_CASES",0)
    with pytest.raises(AudioDataError):store.snapshot_dataset()


def test_publish_failure_cleans_only_pending_case(tmp_path,monkeypatch):
    import review.cases as module
    store=ReviewCaseStore(tmp_path/"cases");source=ClipSource(audio([(.3,.1)]))
    saved=store.save(source,annotation())
    def fail(*args):raise OSError("permanent lock")
    monkeypatch.setattr(module,"publish_directory",fail)
    with pytest.raises(OSError):store.save(source,annotation())
    assert saved.exists() and len(list(store.root.iterdir()))==1
