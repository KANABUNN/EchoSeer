"""Optional supplied audio and clearly injected classification errors through the real pipeline."""
import pytest
from scripts.evaluate_phase89 import evaluate
from tests.integration.test_real_oracles import SAMPLES,MAPPING


def test_recorded_oracles_and_injected_one_position_errors(tmp_path):
    if not all((SAMPLES/(name+".wav")).is_file() for name in MAPPING):
        pytest.skip("Optional local samples/A.wav..G.wav are not supplied")
    report=evaluate(SAMPLES,tmp_path/"evaluation")
    assert report["passed"] and report["source_files_unchanged"] and report["case_count"]==8
    for case in report["cases"]:
        assert (case["confirmed"] and case["final_sequence"] is not None)==(case["mode"]=="normal")
        if case["mode"]!="normal":assert case["injections"] and case["final_sequence"] is None
