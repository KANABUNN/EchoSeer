"""Replay supplied Oracle audio in two presentations; timing is controlled, not a raid recording."""
import argparse
from hashlib import sha256
from dataclasses import asdict
import json
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from audio.data import AudioClip, AudioDataError
from audio.sources import WaveFileSource
from audio.waveio import read_wav, write_wav
from detector.classifier import OracleClassifier, TemplateWaveform
from encounter.definition import VOG_ORACLES
from encounter.vog_oracles import OracleId
from replay.analyzer import Analyzer
from replay.sequence_analyzer import ReplaySequenceAnalyzer


def evaluate(samples_dir: Path, output_dir: Path) -> dict:
    recipe_path = ROOT / "tests/datasets/phase7-oracles.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    mapping = json.loads((ROOT / "docs/sample-mapping.json").read_text(encoding="utf-8"))
    hashes = {name: sha256((samples_dir/name).read_bytes()).hexdigest() for name in mapping}
    clips = {name: read_wav(samples_dir/name) for name in mapping}
    if len({(clip.sample_rate, clip.channels) for clip in clips.values()}) != 1:
        raise AudioDataError("評価用7音声のレートとチャンネル数を揃えてください。")
    rate, channels = next(iter(clips.values())).sample_rate, next(iter(clips.values())).channels
    templates = tuple(TemplateWaveform(OracleId(mapping[name]), name, clip) for name, clip in clips.items())
    classifier = OracleClassifier(templates=templates)
    analyzer = Analyzer()
    pipeline = ReplaySequenceAnalyzer(classifier)
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = []

    def case(name, round_index, mode="normal"):
        count = VOG_ORACLES.expected_count(round_index)
        order = recipe["order"][:count]
        first = order[:]
        second = order[:]
        if mode == "missing":
            first.pop(1)
        if mode == "mismatch":
            second[-1] = "D.wav"
        parts = []
        timeline = []
        cursor = 0
        def append(values):
            nonlocal cursor
            parts.append(values)
            cursor += len(values)
        def silence(seconds):
            append(np.zeros((round(rate*seconds),channels),np.float32))
        silence(recipe["initial_silence_seconds"])
        for pass_index, presentation in enumerate((first, second), 1):
            for index, source in enumerate(presentation, 1):
                values = clips[source].samples
                if mode == "noise" and pass_index == 1 and index == 2:
                    rms = float(np.sqrt(np.mean(np.square(values,dtype=np.float64))))
                    noise = np.random.default_rng(recipe["noise_seed"]).normal(
                        0,rms*10**(-recipe["noise_snr_db"]/20),values.shape).astype(np.float32)
                    values = values + noise
                if mode == "eof" and pass_index == 2 and index == len(presentation):
                    values = values[:round(rate*.5)]
                timeline.append({"pass":pass_index,"index":index,"oracle":mapping[source],"source":source,
                                 "start_frame":cursor,"end_frame":cursor+len(values)})
                append(values)
                if index < len(presentation):
                    silence(recipe["between_cues_seconds"])
            if pass_index == 1:
                silence(.5 if mode == "early" else recipe["between_passes_seconds"])
            elif mode != "eof":
                silence(recipe["final_silence_seconds"])
        clip = AudioClip(np.concatenate(parts),rate)
        path = write_wav(output_dir/f"{name}.wav",clip)
        result = pipeline.analyze(analyzer.analyze(WaveFileSource(path)),round_index)
        snapshot = result.snapshot
        expected_first = [mapping[source] for source in first]
        expected_second = [mapping[source] for source in second]
        actual_first = [entry.oracle.value if entry.oracle else None for entry in snapshot.pass1]
        actual_second = [entry.oracle.value if entry.oracle else None for entry in snapshot.pass2]
        if mode == "normal":
            passed = (snapshot.confirmed and snapshot.state.value == "CONFIRMED"
                      and [o.value for o in snapshot.final_sequence] == expected_first
                      and actual_first == expected_first and actual_second == expected_second)
        else:
            passed = not snapshot.confirmed and snapshot.final_sequence is None
        if mode == "mismatch":
            passed &= (snapshot.verification.status.value == "MISMATCH"
                       and snapshot.verification.mismatch_indices == (count,)
                       and actual_first == expected_first and actual_second == expected_second)
        elif mode in ("noise","eof"):
            passed &= snapshot.state.value=="UNCERTAIN" and len(actual_first)==len(actual_second)==count
            passed &= actual_first[1] is None if mode=="noise" else actual_second[-1] is None
        elif mode == "missing":
            passed &= snapshot.state.value=="UNCERTAIN" and len(actual_first)==count-1 and not actual_second
        elif mode == "early":
            passed &= snapshot.reason=="EARLY_PASS_2" and not actual_second
        report = {"case":name,"round":round_index,"mode":mode,"passed":bool(passed),"wav":str(path),
                  "state":snapshot.state.value,"reason":snapshot.reason,"expected_pass1":expected_first,
                  "expected_pass2":expected_second,"pass1":actual_first,"pass2":actual_second,
                  "confirmed":snapshot.confirmed,"final_sequence":snapshot.final_sequence,"verification":asdict(snapshot.verification),"timeline":timeline,
                  "traces":[{"onset_frame":t.onset_frame,"signal_end_frame":t.signal_end_frame,
                             "window_start":t.start_frame,"window_end":t.end_frame,
                             "oracle":t.detection.oracle.value if t.detection.oracle else None,
                             "candidate":t.detection.best_candidate.value if t.detection.best_candidate else None,
                             "score":t.detection.confidence,"level":t.detection.status.value,"reason":t.detection.reason}
                            for t in result.traces],
                  "transitions":[{"from":t.before.value,"to":t.after.value,"time":t.timestamp,"reason":t.reason}
                                 for t in snapshot.transitions]}
        cases.append(report)

    for round_index in range(1,6):
        case(f"round-{round_index}",round_index)
    for mode in ("missing","mismatch","noise","eof","early"):
        case(mode,1,mode)
    unchanged = hashes=={name:sha256((samples_dir/name).read_bytes()).hexdigest() for name in mapping}
    report={"passed":unchanged and all(c["passed"] for c in cases),"case_count":len(cases),"cases":cases,
            "source_files_unchanged":unchanged,"source_sha256":hashes,"recipe":recipe,
            "recipe_sha256":sha256(recipe_path.read_bytes()).hexdigest(),
            "limitation":"Individual real Oracle recordings; concatenation, silence and cadence are synthetic."}
    (output_dir/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description="Phase 7 PASS1/PASS2 Replay evaluation")
    parser.add_argument("--samples-dir",type=Path,default=ROOT/"samples")
    parser.add_argument("--output-dir",type=Path,default=ROOT/".runtime/phase7-evaluation")
    args=parser.parse_args(argv)
    try:
        result=evaluate(args.samples_dir,args.output_dir)
    except (OSError,ValueError) as error:
        print(f"Evaluation failed: {error}",file=sys.stderr)
        return 1
    print(json.dumps({"passed":result["passed"],"case_count":result["case_count"],
                      "states":{c["case"]:c["state"] for c in result["cases"]}}))
    return 0 if result["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
