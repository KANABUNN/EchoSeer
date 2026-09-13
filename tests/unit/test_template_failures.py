"""Keep other Oracle samples usable and preserve data across cancelled changes."""
from pathlib import Path
from threading import Event, Thread
import pytest

from audio.data import AudioDataError
from audio.playback import AudioPlayer
from audio.operations import OperationCancelled
from templates.manager import TemplateManager
from tests.fakes_audio import AudioFactory, FakeModule
from tests.unit.test_templates import tone


@pytest.mark.parametrize("kind", ["file", "link"])
def test_invalid_oracle_folder_does_not_hide_other_oracles(tmp_path, monkeypatch, kind):
    manager = TemplateManager(tmp_path)
    sample = manager.add("R3", tone())
    invalid = tmp_path / "samples" / "L1"
    if kind == "file":
        invalid.write_bytes(b"bad")
    else:
        invalid.mkdir()
        real = Path.is_junction
        monkeypatch.setattr(Path, "is_junction", lambda self: self == invalid or real(self))
    catalog = manager.catalog()
    assert len(catalog.samples) == 1 and len(catalog.issues) == 1
    assert catalog.samples[0].metadata.sample_id == sample.metadata.sample_id


def test_cancelled_delete_and_restore_keep_current_files(tmp_path):
    manager = TemplateManager(tmp_path)
    sample = manager.add("L1", tone())
    before = sample.path.read_bytes()
    cancel = Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        manager.delete("L1", sample.metadata.sample_id, cancel)
    assert sample.path.read_bytes() == before
    deleted = manager.delete("L1", sample.metadata.sample_id)
    with pytest.raises(OperationCancelled):
        manager.restore(deleted, cancel)
    assert not sample.directory.exists()
    assert (tmp_path / ".trash" / deleted.trash_name / "sample.wav").read_bytes() == before
    assert manager.restore(deleted).path.read_bytes() == before


def test_inactive_output_is_reported_as_failure_and_freed():
    factory = AudioFactory()
    errors = []
    def play():
        try:
            AudioPlayer(FakeModule, factory).play(tone(5))
        except AudioDataError as error:
            errors.append(str(error))
    thread = Thread(target=play)
    thread.start()
    assert factory.open_entered.wait(2)
    # The interface records its stream just after open_entered; wait for creation.
    import time
    deadline = time.monotonic() + 2
    while not factory.streams or not factory.streams[0].is_active():
        assert time.monotonic() < deadline
        time.sleep(0.001)
    factory.streams[0].lose()
    thread.join(2)
    assert not thread.is_alive() and errors and "途中" in errors[0]
    assert factory.streams[0].closed and factory.interfaces[0].terminated
