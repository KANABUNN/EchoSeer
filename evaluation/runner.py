"""Batch shared-analyzer evaluation, reproducible reports and transactional exports."""
from dataclasses import asdict
import csv
import json
import math
from pathlib import Path
from uuid import uuid4

from app.file_operations import publish_directory
from audio.data import AudioDataError
from audio.event import EventContext
from audio.operations import check_cancel
from audio.sources import WaveFileSource
from config.defaults import ORACLE_KEYS
from config.schema import RecognitionSettings,SequenceSettings
from replay.sequence_analyzer import ReplaySequenceAnalyzer
from detector.confidence import ConfidenceEngine
from replay.analyzer import Analyzer
from replay.provenance import digest,recognition_profile
from replay.timeline import ReplayTimelineAnalyzer
from templates.manager import audio_checksum
from evaluation.dataset import file_checksum,read_json
from evaluation.metrics import ACTUAL_KEYS,PREDICTED_KEYS,Prediction,ratio,score_case

MAX_REPORT_BYTES=64*1024*1024
METRIC_KEYS=("accuracy","precision","rejection_rate","false_positive_rate","false_positives_per_minute","case_accuracy")


def evaluate(dataset,classifier,recognition=None,cancel=None,progress=None,sequence=None):
    recognition=recognition or RecognitionSettings()
    analyzer=Analyzer(classifier.sample_rate)
    sequence=sequence or SequenceSettings()
    profile=recognition_profile(classifier,recognition,cancel)
    profile["sequence"]=asdict(sequence)
    matrix={a:{p:0 for p in PREDICTED_KEYS} for a in ACTUAL_KEYS}
    totals={k:0 for k in ("expected","detected","correct","incorrect","rejected","missed","false_positives","extra_unknown","rejected_predictions")}
    cases,identity=[],[]
    negative_cases=negative_fp_cases=case_correct=timed_fp=0
    timed_duration=duration=0.0
    round_cases=round_correct=0
    for index,case in enumerate(dataset.cases):
        check_cancel(cancel)
        before=file_checksum(case.path,cancel)
        analysis=analyzer.analyze(WaveFileSource(case.path),cancel)
        seconds=analysis.original.duration_seconds
        duration+=seconds
        if seconds>(10.001 if case.mode=="clip" else 120) or duration>7200:
            raise AudioDataError("Dataset音声はclipが10秒、eventsが120秒、合計2時間以内です。")
        if case.timestamps and case.timestamps[-1][1]>seconds:
            raise AudioDataError("正解の時刻がWAVの長さを超えています。")
        round_result=None
        if case.mode=="clip":
            raw=classifier.classify_preprocessed(analysis.processed,cancel)
            decision=ConfidenceEngine(recognition).evaluate(raw,EventContext("replay",0,start_frame=0,end_frame=analysis.original.frame_count))
            predictions=(Prediction(decision.oracle.value if decision.oracle else None,0,seconds),)
            decisions=((decision,raw),)
        elif case.mode=="round":
            round_result=ReplaySequenceAnalyzer(classifier,recognition,sequence,analyzer=analyzer).analyze(analysis,case.round_index,cancel)
            predictions=tuple(Prediction(t.detection.oracle.value if t.detection.oracle else None,
                              t.onset_frame/analysis.original.sample_rate,t.signal_end_frame/analysis.original.sample_rate) for t in round_result.traces)
            decisions=tuple((t.detection,t.classification) for t in round_result.traces)
        else:
            timeline=ReplayTimelineAnalyzer(classifier,recognition,analyzer).analyze(analysis,cancel)
            predictions=tuple(Prediction(t.detection.oracle.value if t.detection.oracle else None,
                              t.onset_frame/analysis.original.sample_rate,t.signal_end_frame/analysis.original.sample_rate) for t in timeline.traces)
            decisions=tuple((t.detection,t.classification) for t in timeline.traces)
        after=file_checksum(case.path,cancel)
        if before!=after:
            raise AudioDataError("評価中にWAVが変わりました。Datasetを固定して再実行してください。")
        truth_events=case.expected*2 if case.mode=="round" else case.expected
        scored=score_case(truth_events,predictions,case.timestamps,dataset.tolerance)
        if round_result is not None:
            round_cases+=1
            final=round_result.snapshot.final_sequence
            exact=round_result.snapshot.confirmed and tuple(o.value for o in final or ())==case.expected
            round_correct+=exact
            scored["case_correct"]=scored["case_correct"] and exact
            scored["round_verification"]={"status":round_result.snapshot.verification.status.value,
                "confirmed":round_result.snapshot.confirmed,"correct":exact,
                "final_sequence":[o.value for o in final] if final else None}
        for key,value in scored["counts"].items(): totals[key]+=value
        for actual in ACTUAL_KEYS:
            for predicted in PREDICTED_KEYS: matrix[actual][predicted]+=scored["confusion_matrix"][actual][predicted]
        case_correct+=scored["case_correct"]
        if not case.expected:
            negative_cases+=1
            negative_fp_cases+=any(p.oracle is not None for p in predictions)
        if case.timestamps is not None:
            timed_duration+=seconds
            timed_fp+=scored["counts"]["false_positives"]
        truth={"id":case.case_id,"file_checksum":before,"mode":case.mode,
               "expected":list(case.expected),"timestamps":case.timestamps,
               "round":case.round_index if case.mode=="round" else None}
        identity.append(truth)
        details=[]
        for p,(decision,raw) in zip(predictions,decisions):
            details.append({**asdict(p),"confidence":decision.confidence,"confidence_level":decision.status.value,
                            "reason":decision.reason,"best_candidate":decision.best_candidate.value if decision.best_candidate else None,
                            "ranking":[{"oracle":r.oracle.value,"combined":r.score,"waveform":r.waveform_score,
                                        "spectrum":r.spectrum_score} for r in raw.ranking]})
        cases.append({**truth,"file":case.relative_path,"native_checksum":audio_checksum(analysis.original),
                      "duration_seconds":seconds,"category":case.category,"source_kind":case.source_kind,
                      "predictions":details,**scored})
        if progress: progress(round((index+1)*100/len(dataset.cases)),f"{index+1}/{len(dataset.cases)}")
    after_profile=recognition_profile(classifier,recognition,cancel)
    after_profile["sequence"]=asdict(sequence)
    if after_profile!=profile:
        raise AudioDataError("評価中にテンプレートが変わりました。同じ条件で再実行してください。")
    accepted=totals["detected"]-totals["rejected_predictions"]
    metrics={"total_events":totals["expected"],**totals,"accepted_predictions":accepted,
             "accuracy":ratio(totals["correct"],totals["expected"]),"precision":ratio(totals["correct"],accepted),
             "rejection_rate":ratio(totals["rejected_predictions"],totals["detected"]),
             "false_positive_rate":ratio(negative_fp_cases,negative_cases),
             "false_positives_per_minute":timed_fp/(timed_duration/60) if timed_duration else None,
             "negative_cases":negative_cases,"negative_false_positive_cases":negative_fp_cases,
             "timed_duration_seconds":timed_duration,"case_accuracy":ratio(case_correct,len(cases)),
             "round_cases":round_cases,"final_sequence_accuracy":ratio(round_correct,round_cases)}
    per={}
    for oracle in ORACLE_KEYS:
        row=matrix[oracle]; total=sum(row.values()); predicted=sum(matrix[a][oracle] for a in ACTUAL_KEYS)
        per[oracle]={"total":total,"correct":row[oracle],"accuracy":ratio(row[oracle],total),
                     "precision":ratio(row[oracle],predicted),"rejected":row["UNKNOWN"],"missed":row["MISSING"]}
    return {"schema_version":1,"report_type":"oracle_dataset_evaluation","dataset_fingerprint":digest({"cases":identity,"tolerance":dataset.tolerance}),
            "profile":profile,"profile_hash":digest(profile),"description":dataset.description,
            "metrics":metrics,"per_oracle":per,"confusion_matrix":matrix,"cases":cases,
            "metric_definitions":{"accuracy":"correct / expected Oracle events; missing and rejected fail",
                "precision":"correct / all accepted Oracle predictions",
                "rejection_rate":"unknown predictions / all detected predictions",
                "false_positive_rate":"negative cases with accepted Oracle / all labelled negative cases",
                "false_positives_per_minute":"extra accepted predictions / fully timestamp-labelled WAV minutes",
                "final_sequence_accuracy":"confirmed final sequences equal truth / round cases; inferred fails",
                "order_only":"extra predictions are order-alignment evidence; no timing false-positive rate"}}


def compare_reports(current,baseline):
    if (not isinstance(baseline,dict) or type(baseline.get("schema_version")) is not int or baseline.get("schema_version")!=1 or baseline.get("report_type")!="oracle_dataset_evaluation"
        or baseline.get("dataset_fingerprint")!=current["dataset_fingerprint"]):
        raise AudioDataError("比較元のDataset・正解・WAVが異なります。同じDatasetで評価してください。")
    try:
        if baseline["profile_hash"]!=digest(baseline["profile"]):
            raise ValueError("Profile hash")
        deltas={}
        for key in METRIC_KEYS:
            before,after=baseline["metrics"][key],current["metrics"][key]
            if before is not None and (type(before) not in (int,float) or not math.isfinite(before) or before<0 or (key!="false_positives_per_minute" and before>1)):
                raise ValueError("Metric")
            deltas[key]=after-before if before is not None and after is not None else None
        per={o:current["per_oracle"][o]["accuracy"]-baseline["per_oracle"][o]["accuracy"]
             if current["per_oracle"][o]["accuracy"] is not None and baseline["per_oracle"][o]["accuracy"] is not None else None for o in ORACLE_KEYS}
    except (KeyError,TypeError,ValueError) as error:
        raise AudioDataError("比較元レポートの形式が不正です。") from error
    return {"baseline_profile_hash":baseline["profile_hash"],"current_profile_hash":current["profile_hash"],
            "deltas":deltas,"per_oracle_accuracy_delta":per}


def export_report(report,destination,cancel=None):
    destination=Path(destination).resolve()
    check_cancel(cancel)
    if destination.exists():
        raise AudioDataError("保存先は新しいフォルダーを指定してください。既存レポートは保持します。")
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=destination.parent/f".evaluation-{uuid4().hex}"
    staging.mkdir()
    try:
        data=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
        if len(data.encode("utf-8"))>MAX_REPORT_BYTES:
            raise AudioDataError("評価レポートが64 MiBを超えます。Datasetを分割してください。")
        check_cancel(cancel)
        (staging/"report.json").write_text(data+"\n",encoding="utf-8",newline="\n")
        with (staging/"confusion-matrix.csv").open("w",encoding="utf-8-sig",newline="") as stream:
            writer=csv.writer(stream); writer.writerow(["actual / predicted",*PREDICTED_KEYS])
            for actual in ACTUAL_KEYS:
                writer.writerow([actual,*(report["confusion_matrix"][actual][p] for p in PREDICTED_KEYS)])
        check_cancel(cancel)
        publish_directory(staging,destination,cancel)
        return destination/"report.json"
    finally:
        if staging.exists():
            for name in ("report.json","confusion-matrix.csv"):
                (staging/name).unlink(missing_ok=True)
            staging.rmdir()
