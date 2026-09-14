"""Create a labelled local demonstration; cadence and negative audio are synthetic."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.file_operations import publish_directory
from audio.data import AudioClip, AudioDataError
from audio.waveio import read_wav, write_wav
from evaluation.dataset import file_checksum, load_dataset, read_json


def prepare(samples: Path, output: Path) -> Path:
    mapping = read_json(ROOT / "docs/sample-mapping.json")
    recipe = read_json(ROOT / "tests/datasets/phase7-oracles.json")
    hashes = {name: file_checksum(samples / name) for name in mapping}
    clips = {name: read_wav(samples / name) for name in mapping}
    formats = {(clip.sample_rate, clip.channels) for clip in clips.values()}
    if len(formats) != 1 or any(not 0 < clip.duration_seconds <= 10 for clip in clips.values()):
        raise AudioDataError("7音声のレート・チャンネル数と長さを確認してください。")
    rate, channels = next(iter(formats))
    output = Path(output)
    if output.exists():
        raise FileExistsError("出力先には新しいフォルダーを指定してください。")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / (".example-" + uuid4().hex)
    stage.mkdir()
    audio = stage / "audio"
    audio.mkdir()
    rows = []
    written = []

    def save(name, clip, mode, expected, source_kind, **extra):
        path = audio / (name + ".wav")
        written.append(path)
        write_wav(path, clip)
        rows.append({"id": name, "file": "audio/" + path.name, "mode": mode,
                     "expected": expected, "source_kind": source_kind, **extra})

    try:
        for name, clip in clips.items():
            save(name[:-4], clip, "clip", [mapping[name]], "isolated", category="success")
        save("silence", AudioClip(np.zeros((rate * 2, channels), np.float32), rate),
             "clip", [], "synthetic")
        noise = np.random.default_rng(17).normal(0, .03, (rate, channels)).astype(np.float32)
        save("white-noise", AudioClip(noise, rate), "clip", [], "synthetic",
             notes="Synthetic white noise; no gameplay combat audio.")
        first_round = None
        for round_index in range(1, 6):
            order = recipe["order"][:round_index + 2]
            parts, stamps = [], []
            cursor = 0

            def append(values):
                nonlocal cursor
                parts.append(values)
                cursor += len(values)

            def silence(seconds):
                append(np.zeros((round(rate * seconds), channels), np.float32))

            silence(recipe["initial_silence_seconds"])
            for pass_index in (1, 2):
                for index, name in enumerate(order):
                    clip = clips[name]
                    stamps.append({"start": cursor / rate, "end": (cursor + clip.frame_count) / rate})
                    append(clip.samples)
                    if index + 1 < len(order):
                        silence(recipe["between_cues_seconds"])
                silence(recipe["between_passes_seconds"] if pass_index == 1
                        else recipe["final_silence_seconds"])
            joined = AudioClip(np.concatenate(parts), rate)
            expected = [mapping[name] for name in order]
            save("round-" + str(round_index), joined, "round", expected, "synthetic",
                 round=round_index, category="success",
                 notes="Both presentations assembled from the same isolated template recordings.")
            if round_index == 1:
                first_round = joined, stamps, expected
        joined, stamps, expected = first_round
        timed = AudioClip(np.concatenate((joined.samples, np.zeros((rate * 2, channels), np.float32),
                                         noise, np.zeros((rate, channels), np.float32))), rate)
        save("timed-with-noise", timed, "events", expected * 2, "synthetic", timestamps=stamps,
             notes="All Oracle intervals labelled from source insertion times; appended noise is background.")
        manifest = stage / "dataset.json"
        manifest.write_text(json.dumps({"schema_version": 1, "description":
            "Local demonstration only: templates and queries share source recordings. Timing, silence and noise are synthetic.",
            "cases": rows, "provenance": {"source_sha256": hashes, "recipe": recipe}},
            ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        load_dataset(manifest)
        if hashes != {name: file_checksum(samples / name) for name in mapping}:
            raise AudioDataError("元音声が準備中に変更されました。")
        publish_directory(stage, output)
        return output / "dataset.json"
    finally:
        if stage.exists():
            for path in written + [stage / "dataset.json"]:
                path.unlink(missing_ok=True)
            audio.rmdir()
            stage.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a labelled Oracle demonstration dataset")
    parser.add_argument("--samples", type=Path, default=ROOT / "samples")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/example-dataset" / uuid4().hex)
    args = parser.parse_args(argv)
    print(prepare(args.samples, args.output))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"Preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
