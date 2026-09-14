"""Optional provided Oracle recordings; fixture timing is explicitly synthetic."""
from pathlib import Path
import pytest
from scripts.evaluate_phase7 import evaluate
from tests.integration.test_real_oracles import SAMPLES,MAPPING


def test_supplied_oracle_two_presentation_dataset(tmp_path):
    if not all((SAMPLES/(name+".wav")).is_file() for name in MAPPING):
        pytest.skip("Optional local samples/A.wav..G.wav are not supplied")
    report=evaluate(SAMPLES,tmp_path/"evaluation")
    assert report["passed"] and report["source_files_unchanged"] and report["case_count"]==10
    regular=[c for c in report["cases"] if c["mode"]=="normal"]
    assert len(regular)==5
    assert [len(c["pass1"]) for c in regular]==[3,4,5,6,7]
    assert all(c["state"]=="VERIFY" and not c["confirmed"] for c in regular)
