"""Reproducible Phase 4 vs Phase 5 Replay comparison with supplied local WAVs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
from audio.data import AudioClip, AudioDataError
from audio.sources import WaveFileSource
from audio.waveio import read_wav, write_wav
from detector.classifier import OracleClassifier, TemplateWaveform
from encounter.vog_oracles import OracleId
from replay.analyzer import Analyzer


def evaluate(samples_dir: Path, output_dir: Path) -> dict:
    mapping = json.loads((ROOT/"docs/sample-mapping.json").read_text(encoding="utf-8"))
    recipe_path = ROOT/"tests/datasets/phase5-oracles.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    raw = {name: read_wav(samples_dir/name) for name in mapping}
    hashes = {name: hashlib.sha256((samples_dir/name).read_bytes()).hexdigest() for name in mapping}
    for name, clip in raw.items():
        if clip.duration_seconds < recipe["query_end_seconds"]:
            raise AudioDataError(f"{name} は比較区間より短い音声です。")
    output_dir = output_dir.resolve()
    (output_dir/"queries").mkdir(parents=True, exist_ok=True)
    analyzer = Analyzer(48000)
    cases = []

    def bank(mode):
        return tuple(TemplateWaveform(OracleId(mapping[name]), name,
                     clip if mode == "whole" else AudioClip(
                         clip.samples[:round(clip.sample_rate*recipe["template_seconds"])], clip.sample_rate))
                     for name, clip in raw.items())

    def run_case(name, query, group, case_id, classifiers):
        path = write_wav(output_dir/"queries"/(case_id+".wav"), query)
        analysis = analyzer.analyze(WaveFileSource(path))
        baseline, combined = (c.classify_preprocessed(analysis.processed) for c in classifiers)
        expected = mapping[name]
        cases.append({
            "case": case_id, "group": group, "source": name, "expected": expected,
            "query_path": str(path), "waveform_candidate": baseline.oracle.value if baseline.oracle else None,
            "combined_candidate": combined.oracle.value if combined.oracle else None,
            "waveform_correct": baseline.oracle == expected, "combined_correct": combined.oracle == expected,
            "scores": [{"oracle": r.oracle.value, "waveform": r.waveform_score,
                        "spectrum": r.spectrum_score, "combined": r.combined_score}
                       for r in combined.ranking],
        })

    for mode in ("whole", "disjoint"):
        templates = bank(mode)
        classifiers = (OracleClassifier(templates=templates, waveform_weight=1, spectrum_weight=0),
                       OracleClassifier(templates=templates, waveform_weight=recipe["waveform_weight"],
                                        spectrum_weight=recipe["spectrum_weight"]))
        for name, clip in raw.items():
            query = clip if mode == "whole" else AudioClip(
                clip.samples[round(clip.sample_rate*recipe["query_start_seconds"]):
                             round(clip.sample_rate*recipe["query_end_seconds"])], clip.sample_rate)
            run_case(name, query, "quiet-"+mode, mode+"-"+Path(name).stem, classifiers)
        if mode == "disjoint":
            for snr in recipe["snr_db"]:
                for seed in recipe["seeds"]:
                    for index, (name, clip) in enumerate(raw.items()):
                        signal = clip.samples[round(clip.sample_rate*recipe["query_start_seconds"]):
                                              round(clip.sample_rate*recipe["query_end_seconds"])].mean(axis=1)
                        noise = np.random.default_rng(seed+index).normal(size=len(signal))
                        rms = np.sqrt(np.mean((signal-signal.mean())**2))
                        noise *= rms/np.sqrt(np.mean(noise**2))*10**(-snr/20)
                        query = AudioClip(signal+noise, clip.sample_rate)
                        run_case(name, query, f"white-{snr}", f"white-{snr}-{seed}-"+Path(name).stem, classifiers)
                print(f"Evaluated SNR {snr} dB", flush=True)
    groups = {}
    for case in cases:
        counts = groups.setdefault(case["group"], {"count":0, "waveform_correct":0, "combined_correct":0})
        counts["count"] += 1
        counts["waveform_correct"] += int(case["waveform_correct"])
        counts["combined_correct"] += int(case["combined_correct"])
    noisy = [c for c in cases if c["group"].startswith("white")]
    passed = all(g["combined_correct"] >= g["waveform_correct"] for g in groups.values())
    passed &= sum(c["combined_correct"] for c in noisy) > sum(c["waveform_correct"] for c in noisy)
    after = {name: hashlib.sha256((samples_dir/name).read_bytes()).hexdigest() for name in mapping}
    if hashes != after:
        raise AudioDataError("比較中に元サンプルが変更されました。再実行してください。")
    report = {"passed": bool(passed), "case_count":len(cases), "groups":groups,
              "recipe":recipe, "recipe_sha256":hashlib.sha256(recipe_path.read_bytes()).hexdigest(),
              "source_sha256":hashes, "source_files_unchanged":True,
              "regressions":[c["case"] for c in cases if c["waveform_correct"] and not c["combined_correct"]],
              "improvements":[c["case"] for c in cases if not c["waveform_correct"] and c["combined_correct"]],
              "cases":cases}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 4 / Phase 5 Replay comparison")
    parser.add_argument("--samples-dir", type=Path, default=ROOT/"samples")
    parser.add_argument("--output-dir", type=Path, default=ROOT/".runtime/phase5-evaluation")
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.samples_dir, args.output_dir)
    except (OSError, ValueError, AudioDataError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"passed":result["passed"], "case_count":result["case_count"], "groups":result["groups"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
