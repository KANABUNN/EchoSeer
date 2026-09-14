"""Optional supplied audio verifies report and comparison plumbing, not independent raid accuracy."""
from pathlib import Path
import pytest
from audio.waveio import read_wav
from config.schema import RecognitionSettings
from detector.classifier import OracleClassifier, TemplateWaveform
from encounter.vog_oracles import OracleId
from evaluation.dataset import load_dataset, read_json
from evaluation.runner import compare_reports, evaluate, export_report
from scripts.prepare_example_dataset import prepare

ROOT = Path(__file__).resolve().parents[2]


def test_provided_audio_batch_report_comparison_and_source_preservation(tmp_path):
    samples = ROOT / "samples"
    if not all((samples / f"{name}.wav").is_file() for name in "ABCDEFG"):
        pytest.skip("Optional local Oracle WAV samples are unavailable")
    dataset = load_dataset(prepare(samples, tmp_path / "dataset"))
    mapping = read_json(ROOT / "docs/sample-mapping.json")
    templates = tuple(TemplateWaveform(OracleId(oracle), name, read_wav(samples / name))
                      for name, oracle in mapping.items())
    baseline = evaluate(dataset, OracleClassifier(templates=templates), RecognitionSettings())
    assert len(baseline["cases"]) == 15
    assert baseline["metrics"]["correct"] == baseline["metrics"]["total_events"] == 63
    assert baseline["metrics"]["false_positives"] == 0
    assert baseline["metrics"]["false_positive_rate"] == 0
    assert baseline["metrics"]["final_sequence_accuracy"] == 1
    assert all(row["round_verification"]["correct"] for row in baseline["cases"] if row["mode"] == "round")
    r = RecognitionSettings(waveform_weight=1.0, spectrum_weight=0.0)
    candidate = evaluate(dataset, OracleClassifier(templates=templates, waveform_weight=1, spectrum_weight=0), r)
    comparison = compare_reports(candidate, baseline)
    assert candidate["dataset_fingerprint"] == baseline["dataset_fingerprint"]
    assert candidate["profile_hash"] != baseline["profile_hash"]
    assert comparison["deltas"]["accuracy"] == candidate["metrics"]["accuracy"] - baseline["metrics"]["accuracy"]
    candidate["comparison"] = comparison
    report_path = export_report(candidate, tmp_path / "export")
    assert read_json(report_path)["dataset_fingerprint"] == baseline["dataset_fingerprint"]
    assert (report_path.parent / "confusion-matrix.csv").is_file()
