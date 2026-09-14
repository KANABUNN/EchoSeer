"""Evaluate continuous Live vs finite Replay with provided audio and synthetic timing."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio.data import AudioClip, AudioDataError
from audio.sources import ClipSource
from audio.waveio import read_wav
from config.schema import RecognitionSettings
from detector.classifier import OracleClassifier, TemplateWaveform
from detector.live_sequence import LiveSequenceSession
from encounter.definition import VOG_ORACLES
from encounter.sequence import SequenceState
from encounter.vog_oracles import OracleId
from replay.analyzer import Analyzer
from replay.sequence_analyzer import ReplaySequenceAnalyzer


def evaluate(samples_dir: Path, output_dir: Path) -> dict:
    mapping = json.loads((ROOT / "docs/sample-mapping.json").read_text(encoding="utf-8"))
    recipe = json.loads((ROOT / "tests/datasets/phase7-oracles.json").read_text(encoding="utf-8"))
    hashes = {name: sha256((samples_dir / name).read_bytes()).hexdigest() for name in mapping}
    clips = {name: read_wav(samples_dir / name) for name in mapping}
    if len({(clip.sample_rate, clip.channels) for clip in clips.values()}) != 1:
        raise AudioDataError("評価用7音声の形式を揃えてください。")
    rate, channels = next(iter(clips.values())).sample_rate, next(iter(clips.values())).channels
    bank = tuple(TemplateWaveform(OracleId(mapping[name]), name, clip) for name, clip in clips.items())
    cases = []

    def run(round_index, chunk_frames=24000, mismatch=False, cooldown=.45):
        count = VOG_ORACLES.expected_count(round_index)
        first = recipe["order"][:count]
        second = list(first)
        if mismatch:
            second[-1] = "D.wav"
        parts = [np.zeros((round(rate * recipe["initial_silence_seconds"]), channels), np.float32)]
        for pass_index, order in enumerate((first, second)):
            for index, name in enumerate(order):
                parts.append(clips[name].samples)
                gap = (recipe["between_cues_seconds"] if index < len(order)-1 else
                       recipe["between_passes_seconds"] if pass_index == 0 else recipe["final_silence_seconds"])
                parts.append(np.zeros((round(rate * gap), channels), np.float32))
        original = AudioClip(np.concatenate(parts), rate)
        settings = RecognitionSettings(duplicate_cooldown=cooldown)
        classifier = OracleClassifier(templates=bank)
        session = LiveSequenceSession(classifier, rate, channels, "evaluation", start_frame=1234,
                                      time_origin=1000, round_index=round_index, recognition=settings)
        updates = []
        for start in range(0, original.frame_count, chunk_frames):
            updates.extend(session.feed(original.samples[start:start + chunk_frames]))
        live = session.engine.snapshot()
        replay = ReplaySequenceAnalyzer(OracleClassifier(templates=bank), recognition=settings).analyze(
            Analyzer().analyze(ClipSource(original)), round_index).snapshot
        expected = [mapping[name] for name in first]
        actual = [[entry.oracle.value if entry.oracle else None for entry in entries] for entries in (live.pass1, live.pass2)]
        replay_rows = [[entry.oracle.value if entry.oracle else None for entry in entries] for entries in (replay.pass1, replay.pass2)]
        if mismatch:
            passed = (not live.confirmed and live.final_sequence is None
                      and live.verification.status.value == "MISMATCH"
                      and live.verification.mismatch_indices == (count,) and actual == replay_rows)
        else:
            passed = (live.confirmed and actual == [expected, expected] and actual == replay_rows
                      and [oracle.value for oracle in live.final_sequence] == expected
                      and any(update.snapshot.state == SequenceState.CONFIRMED for update in updates))
        cases.append({"round": round_index, "mismatch": mismatch, "chunk_frames": chunk_frames,
                      "cooldown": cooldown, "passed": bool(passed), "live_state": live.state.value,
                      "verification": live.verification.status.value, "pass1": actual[0], "pass2": actual[1],
                      "confirmed": live.confirmed, "final_sequence": live.final_sequence,
                      "mismatch_indices": live.verification.mismatch_indices, "matches_replay": actual == replay_rows,
                      "candidate_count": sum(len(row) for row in actual), "updates": len(updates),
                      "retained_frames": session.detector.retained_frames})

    for round_index in range(1, 6):
        run(round_index)
    run(1, chunk_frames=4093, cooldown=10)
    run(1, mismatch=True)
    unchanged = hashes == {name: sha256((samples_dir / name).read_bytes()).hexdigest() for name in mapping}
    report = {"passed": unchanged and all(case["passed"] for case in cases), "case_count": len(cases),
              "cases": cases, "source_files_unchanged": unchanged, "source_sha256": hashes,
              "limitation": "Provided real cues; same source templates, artificial silence and cadence. Not independent raid accuracy."}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Live streaming and Replay comparison")
    parser.add_argument("--samples-dir", type=Path, default=ROOT / "samples")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".runtime/live-evaluation")
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.samples_dir, args.output_dir)
    except (OSError, ValueError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"passed": report["passed"], "case_count": report["case_count"],
                      "results": [{"round": case["round"], "status": case["verification"]} for case in report["cases"]]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
