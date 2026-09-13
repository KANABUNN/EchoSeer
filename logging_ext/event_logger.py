"""Low-volume JSONL event sink with optional persistence."""

from collections.abc import Mapping
import json
from pathlib import Path
from threading import Lock
import time
from typing import Any


class EventLogger:
    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = Lock()

    def write(self, event: Mapping[str, Any]) -> None:
        if not self.enabled:
            return
        payload = {
            "timestamp": time.time(),
            "monotonic_time": time.monotonic(),
            **event,
        }
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized + "\n")
