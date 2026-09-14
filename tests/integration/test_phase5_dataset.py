"""Phase 5 acceptance: same quiet recordings and a fixed noisy Replay recipe."""
from pathlib import Path
import numpy as np
import pytest
from scripts.evaluate_phase5 import evaluate

SAMPLES = Path(__file__).resolve().parents[2]/"samples"


def test_supplied_replay_dataset_meets_phase5(tmp_path):
    if not all((SAMPLES/(name+".wav")).is_file() for name in "ABCDEFG"):
        pytest.skip("Optional local Oracle recordings are not supplied")
    report = evaluate(SAMPLES, tmp_path/"evaluation")
    assert report["passed"] and report["case_count"] == 98
    assert report["source_files_unchanged"] and not report["regressions"]
    assert report["groups"]["quiet-whole"]["combined_correct"] == 7
    assert report["groups"]["quiet-disjoint"]["combined_correct"] == 7
    assert report["improvements"]
    for case in report["cases"]:
        for row in case["scores"]:
            assert all(np.isfinite(row[s]) and 0 <= row[s] <= 1 for s in ("waveform","spectrum","combined"))
            assert row["combined"] == pytest.approx(.6*row["waveform"]+.4*row["spectrum"])
