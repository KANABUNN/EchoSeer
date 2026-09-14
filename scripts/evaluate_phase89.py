"""Provided Oracle recordings plus explicit classification-error fixtures for Phases 8/9."""
import argparse
from dataclasses import asdict,replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from audio.data import AudioClip,AudioDataError
from audio.sources import WaveFileSource
from audio.waveio import read_wav,write_wav
from detector.classifier import OracleClassifier,TemplateWaveform
from encounter.vog_oracles import OracleId
from replay.analyzer import Analyzer
from replay.sequence_analyzer import ReplaySequenceAnalyzer

ORDER=("F.wav","E.wav","C.wav")
MODES=("normal","low-one","high-duplicate","low-both","weak-both","ambiguous","two-low","high-mismatch")
EXPECTED=("L2","R1","MID")


def round_clip(clips):
    rate=clips[ORDER[0]].sample_rate
    channels=clips[ORDER[0]].channels
    if len({(c.sample_rate,c.channels) for c in clips.values()})!=1:
        raise AudioDataError("Provide the seven recordings at one rate and channel count.")
    silence=lambda seconds:np.zeros((round(rate*seconds),channels),np.float32)
    parts=[silence(.5)]
    for presentation in range(2):
        for index,name in enumerate(ORDER):
            parts.append(clips[name].samples)
            if index<2:parts.append(silence(.5))
        parts.append(silence(2.6 if presentation==0 else 1))
    return AudioClip(np.concatenate(parts),rate)


class FixtureClassifier(OracleClassifier):
    """Only the evaluation tool replaces scores; application classification is unchanged."""
    def __init__(self,templates,mode,**kwargs):
        super().__init__(templates=templates,**kwargs)
        self.mode=mode
        self.cursor=0
        self.injections=[]

    def classify_preprocessed(self,clip,cancel=None):
        raw=super().classify_preprocessed(clip,cancel)
        self.cursor+=1
        selected = ((self.mode in ("low-one","high-duplicate","high-mismatch") and self.cursor==3)
                    or (self.mode in ("low-both","weak-both","ambiguous") and self.cursor in (3,6))
                    or (self.mode=="two-low" and self.cursor in (2,3)))
        if not selected:return raw
        true=raw.best_candidate
        scores={row.oracle:min(row.score,.3) for row in raw.ranking}
        if self.mode=="ambiguous":
            scores[true]=scores[OracleId.R3]=.9
        else:
            wrong=OracleId.R3 if self.mode=="high-mismatch" else OracleId.L2
            best,runner={"low-one":(.83,.82),"two-low":(.83,.82),
                         "high-duplicate":(.99,.2),"high-mismatch":(.99,.2),
                         "low-both":(.86,.85),"weak-both":(.61,.60)}[self.mode]
            scores[wrong],scores[true]=best,runner
        rows=[]
        for row in raw.ranking:
            score=scores[row.oracle]
            samples=tuple(replace(sample,match=replace(sample.match,score=score),
                                  spectrum_score=score,combined_score=score) for sample in row.samples)
            rows.append(replace(row,score=score,waveform_score=score,spectrum_score=score,samples=samples))
        rows.sort(key=lambda row:-row.score)
        modified=replace(raw,status="RANKED",ranking=tuple(rows))
        self.injections.append({"event":self.cursor,"pass":1 if self.cursor<=3 else 2,
                                "index":(self.cursor-1)%3+1,"original_candidate":true.value,
                                "original_ranking":[{"oracle":row.oracle.value,"score":row.score} for row in raw.ranking],
                                "injected_ranking":[{"oracle":row.oracle.value,"score":row.score} for row in modified.ranking]})
        return modified


def evaluate(samples_dir:Path,output_dir:Path):
    mapping=json.loads((ROOT/"docs/sample-mapping.json").read_text(encoding="utf-8"))
    hashes={name:sha256((samples_dir/name).read_bytes()).hexdigest() for name in mapping}
    clips={name:read_wav(samples_dir/name) for name in mapping}
    templates=tuple(TemplateWaveform(OracleId(mapping[name]),name,clip) for name,clip in clips.items())
    output_dir.mkdir(parents=True,exist_ok=True)
    path=write_wav(output_dir/"round-1.wav",round_clip(clips))
    analyzer=Analyzer()
    cases=[]
    for mode in MODES:
        classifier=FixtureClassifier(templates,mode)
        result=ReplaySequenceAnalyzer(classifier).analyze(analyzer.analyze(WaveFileSource(path)))
        snapshot=result.snapshot
        comparison=snapshot.verification
        expected_status={"normal":"CONFIRMED","low-one":"INFERRED","high-duplicate":"INFERRED",
                         "low-both":"INFERRED","weak-both":"CHECK","ambiguous":"CHECK",
                         "two-low":"MISMATCH","high-mismatch":"MISMATCH"}[mode]
        passed=comparison.status.value==expected_status
        if mode=="normal":
            passed &= snapshot.confirmed and tuple(o.value for o in snapshot.final_sequence)==EXPECTED
        else:
            passed &= not snapshot.confirmed and snapshot.final_sequence is None
        if expected_status=="INFERRED":
            passed &= tuple(o.value for o in snapshot.suggested_sequence)==EXPECTED and comparison.correction_indices==(3,)
        if mode in ("low-one","high-duplicate","high-mismatch"):
            passed &= comparison.mismatch_indices==(3,)
        if mode=="two-low":passed &= comparison.mismatch_indices==(2,3) and snapshot.suggested_sequence is None
        if mode=="ambiguous":passed &= comparison.reconstruction.margin==0 and snapshot.suggested_sequence is None
        cases.append({"mode":mode,"passed":bool(passed),"state":snapshot.state.value,
                      "status":comparison.status.value,"reason":comparison.reason,
                      "mismatch_indices":comparison.mismatch_indices,"correction_indices":comparison.correction_indices,
                      "confirmed":snapshot.confirmed,"final_sequence":snapshot.final_sequence,
                      "suggested_sequence":snapshot.suggested_sequence,
                      "pass1":[entry.oracle.value if entry.oracle else None for entry in snapshot.pass1],
                      "pass2":[entry.oracle.value if entry.oracle else None for entry in snapshot.pass2],
                      "verification":asdict(comparison),"injections":classifier.injections,
                      "transitions":[{"from":t.before.value,"to":t.after.value,"time":t.timestamp} for t in snapshot.transitions]})
    unchanged=hashes=={name:sha256((samples_dir/name).read_bytes()).hexdigest() for name in mapping}
    report={"passed":unchanged and all(c["passed"] for c in cases),"case_count":len(cases),"cases":cases,
            "wav":str(path),"source_sha256":hashes,"source_files_unchanged":unchanged,
            "limitation":"Individual real Oracle recordings with synthetic timing; misrecognition scores are explicitly injected."}
    (output_dir/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description="Phase 8/9 verification and reconstruction evaluation")
    parser.add_argument("--samples-dir",type=Path,default=ROOT/"samples")
    parser.add_argument("--output-dir",type=Path,default=ROOT/".runtime/phase89-evaluation")
    args=parser.parse_args(argv)
    try:result=evaluate(args.samples_dir,args.output_dir)
    except (OSError,ValueError) as error:
        print(f"Evaluation failed: {error}",file=sys.stderr);return 1
    print(json.dumps({"passed":result["passed"],"case_count":result["case_count"],
                      "statuses":{c["mode"]:c["status"] for c in result["cases"]}}))
    return 0 if result["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
