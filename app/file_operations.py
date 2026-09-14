"""Publish complete new artifact directories with bounded Windows lock retries."""
from pathlib import Path
import time
from audio.operations import check_cancel


def publish_directory(source,destination,cancel=None):
    source,destination=Path(source),Path(destination)
    root=destination.parent.resolve()
    delays=(.02,.04,.08,.16)
    for attempt in range(len(delays)+1):
        check_cancel(cancel)
        if source.parent.resolve()!=root or source.is_symlink() or source.is_junction():
            raise ValueError("Artifact directory must stay in its intended parent")
        if destination.exists():
            raise FileExistsError("Artifact destination already exists")
        if destination.parent.resolve()!=root:
            raise ValueError("Artifact destination changed")
        try:
            source.rename(destination)
            return
        except OSError as error:
            if getattr(error,"winerror",None) not in (5,32,33) or attempt==len(delays):
                raise
            if cancel is None:
                time.sleep(delays[attempt])
            else:
                cancel.wait(delays[attempt])
