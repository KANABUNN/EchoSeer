"""Only contained audio paths, finite log reading and newest event retention."""
import json
from threading import Event
import pytest
from audio.data import AudioDataError
from audio.operations import OperationCancelled
from audio.waveio import write_wav
from review.logs import load_logs,audio_path
from tests.unit.test_event_detector import audio


def record(timestamp=10,path="audio/clip.wav"):
    return {"schema_version":1,"event_type":"oracle","timestamp":timestamp,"oracle":"L2",
            "confidence_level":"HIGH","audio_path":path}


def write_log(root,name,records):
    root.mkdir(parents=True,exist_ok=True)
    p=root/name;p.write_text("\n".join(json.dumps(row) for row in records)+"\n",encoding="utf-8")
    return p


def test_normal_audio_and_missing_or_traversing_paths_are_data_not_actions(tmp_path):
    root=tmp_path/"logs";(root/"audio").mkdir(parents=True);write_wav(root/"audio"/"clip.wav",audio([(.2,.1)]))
    write_log(root,"session.jsonl",[record(1),record(2,"../outside.wav"),record(3,"missing.wav")])
    catalog=load_logs(root)
    assert len(catalog.events)==3 and catalog.events[-1].audio_relative=="audio/clip.wav"
    assert catalog.events[0].audio_relative is None and catalog.notices
    assert audio_path(root,"audio/clip.wav").is_relative_to(root)
    with pytest.raises(AudioDataError):audio_path(root,"../outside.wav")


def test_latest_rows_are_kept_across_files_not_overwritten_by_old_rows(tmp_path,monkeypatch):
    import review.logs as module
    monkeypatch.setattr(module,"MAX_ROWS",2)
    write_log(tmp_path,"z-new.jsonl",[record(20,None),record(30,None),record(40,None)])
    write_log(tmp_path,"a-old.jsonl",[record(1,None),record(2,None)])
    catalog=load_logs(tmp_path)
    assert [row.payload["timestamp"] for row in catalog.events]==[40,30]


def test_corrupt_nonfinite_and_oversized_lines_are_reported(tmp_path,monkeypatch):
    import review.logs as module
    root=tmp_path/"logs";path=write_log(root,"session.jsonl",[record(1,None)])
    with path.open("a",encoding="utf-8") as stream:stream.write('not JSON\n{"event_type":"oracle","timestamp":NaN}\n')
    catalog=load_logs(root);assert len(catalog.events)==1 and catalog.notices
    monkeypatch.setattr(module,"MAX_LINE",20)
    assert load_logs(root).notices


def test_cancel_and_total_size_limit_are_bounded(tmp_path,monkeypatch):
    import review.logs as module
    write_log(tmp_path,"a.jsonl",[record(1,None)])
    cancel=Event();cancel.set()
    with pytest.raises(OperationCancelled):load_logs(tmp_path,cancel)
    monkeypatch.setattr(module,"MAX_BYTES",10)
    assert not load_logs(tmp_path).events and load_logs(tmp_path).notices
