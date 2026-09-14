"""Canonical recognition conditions and verified template audio identity."""
from dataclasses import asdict
from hashlib import sha256
import json

from audio.operations import check_cancel
from templates.manager import audio_checksum
from encounter.vog_oracles import OracleId


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def recognition_profile(classifier, recognition, cancel=None):
    templates = []
    manager = getattr(classifier, "manager", None)
    if manager is not None:
        catalog = manager.catalog(cancel)
        for sample in catalog.samples:
            check_cancel(cancel)
            m = sample.metadata
            clip = manager.load_audio(m.oracle, m.sample_id, cancel, original=True)
            templates.append({"oracle": m.oracle, "sample_id": m.sample_id,
                              "native_checksum": audio_checksum(clip)})
        issues = list(catalog.issues)
    else:
        for sample in getattr(classifier, "templates", ()):
            check_cancel(cancel)
            templates.append({"oracle": OracleId(sample.oracle).value, "sample_id": sample.sample_id,
                              "native_checksum": audio_checksum(sample.clip)})
        issues = []
    return {"engine": "waveform-spectrum-v1", "sample_rate": classifier.sample_rate,
            "recognition": asdict(recognition),
            "classifier": {key:getattr(classifier,key,None) for key in ("aggregation","top_n","waveform_weight","spectrum_weight")},
            "bandpass": asdict(classifier.bandpass) if getattr(classifier,"bandpass",None) is not None else None,
            "templates": sorted(templates, key=lambda item: (item["oracle"], item["sample_id"])),
            "template_issues": issues}
