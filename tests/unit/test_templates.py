"""Persistence, atomic publication, quality, corruption isolation and undo."""
import json
from pathlib import Path
from threading import Event

import numpy as np
import pytest

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio.waveio import read_wav, write_wav
from encounter.vog_oracles import OracleId
from templates.manager import TemplateManager, DeletedSample
from templates.metadata import SampleMetadata


def tone(seconds=0.2, rate=44100, channels=2, amplitude=0.2, dc=0.1):
    samples = amplitude * np.sin(2 * np.pi * 440 * np.arange(round(seconds * rate)) / rate) + dc
    return AudioClip(np.repeat(samples[:, None], channels, axis=1).astype(np.float32), rate)


def test_two_samples_for_each_oracle_survive_new_manager(tmp_path):
    manager = TemplateManager(tmp_path / "templates")
    original = tone()
    wav = write_wav(tmp_path / "音声.wav", original, "pcm16")
    expected = set()
    for oracle in OracleId:
        a = manager.import_wav(oracle, wav)
        b = manager.add(oracle, original)
        expected.update((a.metadata.sample_id, b.metadata.sample_id))
        assert a.metadata.source == "imported" and b.metadata.source == "recorded"
        assert (a.metadata.sample_rate, a.metadata.frames) == (48000, 9600)
        assert a.metadata.oracle == oracle.value
        assert manager.load_audio(oracle, a.metadata.sample_id).channels == 1
        np.testing.assert_array_equal(read_wav(b.directory / "original.wav").samples, original.samples)
    catalog = TemplateManager(manager.root).catalog()
    assert not catalog.issues and len(catalog.samples) == 14
    assert {s.metadata.sample_id for s in catalog.samples} == expected
    assert len({s.directory for s in catalog.samples}) == 14


@pytest.mark.parametrize("damage", ["metadata", "version", "identity", "audio", "missing", "oversize"])
def test_bad_entry_does_not_hide_good_entries(tmp_path, damage):
    manager = TemplateManager(tmp_path)
    good, bad = manager.add("L1", tone()), manager.add("R3", tone())
    path = bad.directory / "metadata.json"
    if damage == "metadata":
        path.write_text("{", encoding="utf-8")
    elif damage in ("version", "identity"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["schema_version" if damage == "version" else "oracle"] = 2 if damage == "version" else "MID"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif damage == "audio":
        write_wav(bad.path, tone(rate=48000, channels=1, amplitude=0.3))
    elif damage == "missing":
        bad.path.unlink()
    else:
        path.write_text(" " * 32769, encoding="utf-8")
    catalog = manager.catalog()
    assert [s.metadata.sample_id for s in catalog.samples] == [good.metadata.sample_id]
    assert len(catalog.issues) == 1
    assert bad.directory.exists()


@pytest.mark.parametrize("step", ["wav", "metadata", "publish", "cancel"])
def test_failed_add_never_publishes_partial_or_changes_existing(tmp_path, monkeypatch, step):
    import templates.manager as module
    manager = TemplateManager(tmp_path)
    good = manager.add("MID", tone())
    original = good.path.read_bytes()
    cancel = Event()
    if step in ("wav", "cancel"):
        real = module.write_wav
        calls = []
        def writer(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 2:
                if step == "wav":
                    raise OSError("disk full")
                cancel.set()
            return real(*args, **kwargs)
        monkeypatch.setattr(module, "write_wav", writer)
    elif step == "metadata":
        real = Path.open
        def opened(self, *args, **kwargs):
            if self.name == "metadata.json" and args and args[0] == "x":
                raise OSError("disk full")
            return real(self, *args, **kwargs)
        monkeypatch.setattr(Path, "open", opened)
    else:
        monkeypatch.setattr(Path, "rename", lambda *a: (_ for _ in ()).throw(OSError("rename failed")))
    with pytest.raises((OSError, OperationCancelled)):
        manager.add("MID", tone(), cancel=cancel)
    assert good.path.read_bytes() == original
    assert [s.metadata.sample_id for s in manager.catalog().samples] == [good.metadata.sample_id]
    assert not list((tmp_path / "samples" / "MID").glob(".pending-*"))


def test_delete_and_restore_preserve_all_files(tmp_path):
    manager = TemplateManager(tmp_path)
    a, b = manager.add("L2", tone()), manager.add("L2", tone())
    before = {p.name: p.read_bytes() for p in a.directory.iterdir()}
    deleted = manager.delete("L2", a.metadata.sample_id)
    assert not a.directory.exists()
    assert [s.metadata.sample_id for s in manager.catalog().samples] == [b.metadata.sample_id]
    restored = TemplateManager(tmp_path).restore(deleted)
    assert {p.name: p.read_bytes() for p in restored.directory.iterdir()} == before
    assert len(manager.catalog().samples) == 2
    with pytest.raises((OSError, AudioDataError)):
        manager.restore(deleted)


def test_invalid_identity_and_restore_do_not_touch_other_files(tmp_path):
    manager = TemplateManager(tmp_path)
    a = manager.add("R1", tone())
    with pytest.raises(ValueError):
        manager.delete("../", a.metadata.sample_id)
    with pytest.raises(AudioDataError):
        manager.delete("R1", "../" + a.metadata.sample_id)
    with pytest.raises(AudioDataError):
        manager.restore(DeletedSample(OracleId.R1, a.metadata.sample_id, "../../outside"))
    assert a.path.exists() and len(manager.catalog().samples) == 1


def test_restore_collision_does_not_overwrite(tmp_path):
    manager = TemplateManager(tmp_path)
    sample = manager.add("R2", tone())
    deleted = manager.delete("R2", sample.metadata.sample_id)
    sample.directory.mkdir()
    sentinel = sample.directory / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(AudioDataError):
        manager.restore(deleted)
    assert sentinel.read_text() == "keep"
    assert (tmp_path / ".trash" / deleted.trash_name).exists()


@pytest.mark.parametrize("seconds,amplitude,fragment", [
    (0.02, 0.2, "短"), (5.1, 0.2, "長"), (0.2, 0.00001, "小"),
    (0.2, 1.1, "クリッピング"),
])
def test_quality_warnings_are_persisted(tmp_path, seconds, amplitude, fragment):
    manager = TemplateManager(tmp_path)
    sample = manager.add("L3", tone(seconds, amplitude=amplitude, dc=0))
    assert any(fragment in n for n in sample.metadata.notices)
    assert manager.catalog().samples[0].metadata.notices == sample.metadata.notices


@pytest.mark.parametrize("samples", [np.zeros(4800), np.ones(4800) * 0.2,
                                    np.stack((np.sin(np.arange(4800)), -np.sin(np.arange(4800))), axis=1)])
def test_silence_dc_and_opposite_phase_cannot_be_registered(tmp_path, samples):
    with pytest.raises(AudioDataError, match="無音"):
        TemplateManager(tmp_path).add("MID", AudioClip(samples, 48000))
    assert not list(tmp_path.rglob("metadata.json"))


@pytest.mark.parametrize("seconds", [0, 10.01])
def test_invalid_length_is_rejected(tmp_path, seconds):
    with pytest.raises(AudioDataError):
        TemplateManager(tmp_path).add("MID", tone(seconds))
    assert not list(tmp_path.rglob("metadata.json"))


def test_linked_entry_is_rejected_without_modifying_target(tmp_path, monkeypatch):
    manager = TemplateManager(tmp_path / "store")
    sample = manager.add("L1", tone())
    link = sample.directory.parent / ("a" * 32)
    link.mkdir()
    real = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == link or real(self))
    catalog = manager.catalog()
    assert len(catalog.samples) == 1 and len(catalog.issues) == 1
    assert sample.path.exists()


@pytest.mark.parametrize("key,value", [
    ("schema_version", True), ("sample_id", "../id"), ("sample_rate", 0),
    ("frames", True), ("duration_seconds", float("nan")), ("rms", -1),
    ("created_at", "2026-09-14T12:00:00"), ("source", "unknown"),
    ("clipping", 1), ("notices", "warning"), ("checksum", "bad"),
])
def test_strict_metadata_validation(tmp_path, key, value):
    sample = TemplateManager(tmp_path).add("L1", tone())
    data = sample.metadata.to_dict()
    data[key] = value
    with pytest.raises(AudioDataError):
        SampleMetadata.from_dict(data)


def test_internal_rate_change_preserves_sample_and_reports_notice(tmp_path):
    old = TemplateManager(tmp_path, 44100).add("L1", tone())
    catalog = TemplateManager(tmp_path, 48000).catalog()
    assert len(catalog.samples) == 1 and len(catalog.issues) == 1
    assert catalog.samples[0].metadata.sample_id == old.metadata.sample_id
