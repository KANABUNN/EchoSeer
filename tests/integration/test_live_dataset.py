"""Optional provided Oracle audio: continuous streaming must agree with finite Replay."""
from pathlib import Path

import pytest

from scripts.evaluate_live import evaluate

ROOT = Path(__file__).resolve().parents[2]


def test_provided_oracles_live_streaming_matches_replay(tmp_path):
    samples = ROOT / "samples"
    if not all((samples / f"{name}.wav").is_file() for name in "ABCDEFG"):
        pytest.skip("Optional local Oracle WAV samples are unavailable")
    report = evaluate(samples, tmp_path / "evaluation")
    assert report["passed"] and report["case_count"] == 7
    assert report["source_files_unchanged"]
    assert all(case["matches_replay"] for case in report["cases"])
    assert all(case["confirmed"] for case in report["cases"] if not case["mismatch"])
    assert not next(case for case in report["cases"] if case["mismatch"])["confirmed"]
