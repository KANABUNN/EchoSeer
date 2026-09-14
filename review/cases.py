"""Atomic native WAV + manual truth packages and reproducible corpus snapshots."""
import json
from pathlib import Path
from uuid import uuid4

from app.file_operations import publish_directory
from audio.data import AudioClip,AudioDataError
from audio.operations import check_cancel
from audio.waveio import write_wav
from evaluation.dataset import MAX_CASES,load_dataset,read_json
from replay.provenance import digest
from templates.manager import audio_checksum


class ReviewCaseStore:
    def __init__(self,root):
        self.root=Path(root).resolve()

    def save(self,source,annotation,observed=None,cancel=None,bounds=None):
        check_cancel(cancel)
        original=source.read(cancel)
        start,end=bounds or (0,original.frame_count)
        if type(start) is not int or type(end) is not int or not 0<=start<end<=original.frame_count:
            raise AudioDataError("保存する音声区間が不正です。")
        clip=AudioClip(original.samples[start:end],original.sample_rate)
        if clip.samples.nbytes>127*1024*1024:
            raise AudioDataError("保存音声が大きすぎます。短い区間を選んでください。")
        mode=annotation.get("mode","clip")
        if not 0<clip.duration_seconds<=(10.001 if mode=="clip" else 120):
            raise AudioDataError("clipは10秒、events/roundは120秒以内を選んでください。")
        case_id=uuid4().hex
        self.root.mkdir(parents=True,exist_ok=True)
        staging=self.root/f".case-{case_id}"
        destination=self.root/case_id
        staging.mkdir()
        try:
            write_wav(staging/"audio.wav",clip,"float32",cancel)
            provenance={"source_native_checksum":audio_checksum(original),"audio_native_checksum":audio_checksum(clip),
                        "source_frames":[start,end],"sample_rate":clip.sample_rate,"channels":clip.channels,
                        "truth_source":"user_review"}
            row={"id":case_id,"file":"audio.wav",**annotation,"provenance":provenance}
            manifest={"schema_version":1,"cases":[row]}
            (staging/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8",newline="\n")
            load_dataset(staging/"manifest.json")
            (staging/"analysis.json").write_text(json.dumps({"provenance":provenance,"observed":observed or {}},
                                                   ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8",newline="\n")
            check_cancel(cancel)
            publish_directory(staging,destination,cancel)
            return destination/"manifest.json"
        finally:
            if staging.exists():
                for name in ("audio.wav","manifest.json","analysis.json"):
                    (staging/name).unlink(missing_ok=True)
                staging.rmdir()

    def snapshot_dataset(self,cancel=None):
        rows=[]
        folders=[]
        for folder in self.root.iterdir() if self.root.exists() else ():
            check_cancel(cancel)
            if not folder.name.startswith("."):
                folders.append(folder)
                if len(folders)>MAX_CASES:
                    raise AudioDataError("手動記録が500件を超えます。Datasetを分けてください。")
        for folder in sorted(folders):
            check_cancel(cancel)
            if folder.is_dir() and not folder.name.startswith("."):
                if folder.is_symlink() or folder.is_junction() or not folder.resolve().is_relative_to(self.root):
                    raise AudioDataError("記録フォルダーのリンクは評価できません。")
                path=folder/"manifest.json"
                dataset=load_dataset(path)
                if len(dataset.cases)!=1:
                    raise AudioDataError("記録には1つの手動正解ケースを保存してください。")
                row=read_json(path)["cases"][0]
                clip=dataset.cases[0].path
                row["file"]=clip.relative_to(self.root.parent).as_posix()
                rows.append(row)
                if len(rows)>MAX_CASES:
                    raise AudioDataError("手動記録が500件を超えます。Datasetを分けてください。")
        if not rows:
            raise AudioDataError("手動で正解を付けた記録がありません。Replayから保存してください。")
        snapshot={"schema_version":1,"description":"Manually reviewed local cases","cases":rows}
        path=self.root.parent/f"dataset-{uuid4().hex}.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        check_cancel(cancel)
        path.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8",newline="\n")
        try:
            load_dataset(path)
            check_cancel(cancel)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path
