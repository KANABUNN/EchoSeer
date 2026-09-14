"""Quality checks re-read native audio without replacing samples or trusting stale metadata."""
from hashlib import sha256
from threading import Event

import numpy as np
import pytest

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from templates.manager import TemplateManager


def clip(seconds=.2, amplitude=.2, rate=44100):
    values = (amplitude * np.sin(2 * np.pi * 880 * np.arange(round(rate * seconds)) / rate)).astype(np.float32)
    return AudioClip(np.repeat(values[:, None], 2, axis=1), rate)


def hashes(root):
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def test_native_quality_recheck_preserves_all_sample_files(tmp_path):
    manager = TemplateManager(tmp_path / "templates")
    sample = manager.add("L2", clip())
    before = hashes(manager.root)
    report = manager.check_quality("L2", sample.metadata.sample_id)
    assert report.oracle == "L2" and report.sample_rate == 44100 and report.channels == 2
    assert report.duration_seconds == .2 and report.peak == pytest.approx(.2, abs=1e-5)
    assert report.rms == pytest.approx(.2 / np.sqrt(2), abs=1e-5)
    assert not report.clipping and not report.notices
    assert report.checksum == sample.metadata.original_checksum
    assert hashes(manager.root) == before


@pytest.mark.parametrize("seconds,amplitude,word", [
    (.05, .2, "短"), (5.1, .2, "長"), (.2, .00001, "小"), (.2, 1.02, "クリッピング"),
])
def test_quality_warning_is_based_on_native_audio(seconds, amplitude, word, tmp_path):
    manager = TemplateManager(tmp_path / "templates")
    sample = manager.add("MID", clip(seconds, amplitude))
    report = manager.check_quality("MID", sample.metadata.sample_id)
    assert any(word in notice for notice in report.notices)
    assert report.clipping == (amplitude > 1)


@pytest.mark.parametrize("filename", ["original.wav", "sample.wav"])
def test_quality_check_rejects_changed_audio(filename, tmp_path):
    manager = TemplateManager(tmp_path / "templates")
    sample = manager.add("R1", clip())
    (sample.directory / filename).write_bytes(b"not a saved WAV")
    with pytest.raises((AudioDataError, OSError)):
        manager.check_quality("R1", sample.metadata.sample_id)


def test_quality_cancellation_preserves_catalog_and_audio(tmp_path):
    manager = TemplateManager(tmp_path / "templates")
    sample = manager.add("L1", clip())
    before = hashes(manager.root)
    cancel = Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        manager.check_quality("L1", sample.metadata.sample_id, cancel)
    assert hashes(manager.root) == before


def test_quality_recheck_after_internal_rate_change_uses_original_format(tmp_path):
    manager = TemplateManager(tmp_path / "templates", 16000)
    sample = manager.add("L3", clip())
    report = TemplateManager(manager.root, 48000).check_quality("L3", sample.metadata.sample_id)
    assert report.sample_rate == 44100 and report.channels == 2
    assert sample.metadata.sample_rate == 16000
