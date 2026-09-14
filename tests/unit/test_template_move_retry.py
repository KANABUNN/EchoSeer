"""Bound Windows transient locks without hiding permanent failure or cancellation."""
from pathlib import Path
from threading import Event
import pytest

from audio.operations import OperationCancelled
from templates.manager import TemplateManager
from tests.unit.test_templates import tone


@pytest.mark.parametrize("winerror",[5,32,33])
def test_transient_windows_lock_is_retried_without_partial_sample(tmp_path,monkeypatch,winerror):
    real=Path.rename
    calls=[]
    def rename(self,destination):
        calls.append(self)
        if len(calls)<=2:
            error=OSError("Windows lock")
            error.winerror=winerror
            raise error
        return real(self,destination)
    monkeypatch.setattr(Path,"rename",rename)
    sample=TemplateManager(tmp_path).add("L1",tone())
    assert len(calls)==3 and sample.path.exists()
    assert not list((tmp_path/"samples"/"L1").glob(".pending-*"))


def test_permanent_permission_failure_is_bounded_and_not_published(tmp_path,monkeypatch):
    calls=[]
    def rename(*args):
        calls.append(args)
        error=OSError("permission denied")
        error.winerror=5
        raise error
    monkeypatch.setattr(Path,"rename",rename)
    monkeypatch.setattr("templates.manager.time.sleep",lambda delay:None)
    with pytest.raises(OSError):
        TemplateManager(tmp_path).add("MID",tone())
    assert len(calls)==5 and not list(tmp_path.rglob("metadata.json"))


def test_cancel_during_retry_does_not_publish_or_remove_existing(tmp_path,monkeypatch):
    manager=TemplateManager(tmp_path)
    saved=manager.add("R1",tone())
    before=saved.path.read_bytes()
    cancel=Event()
    def rename(*args):
        cancel.set()
        error=OSError("Windows lock")
        error.winerror=32
        raise error
    monkeypatch.setattr(Path,"rename",rename)
    with pytest.raises(OperationCancelled):
        manager.add("R1",tone(),cancel=cancel)
    assert saved.path.read_bytes()==before
    assert len(manager.catalog().samples)==1
