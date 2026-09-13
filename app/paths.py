"""Writable user-data paths, independent of source and bundled resources."""

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

APP_DIRECTORY = "OracleAssistant"


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path

    @classmethod
    def discover(
        cls,
        data_dir: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "AppPaths":
        if data_dir is not None:
            return cls(data_dir.expanduser().resolve())
        values = os.environ if environ is None else environ
        base = values.get("LOCALAPPDATA") or values.get("APPDATA")
        user_base = Path(base) if base else Path.home() / "AppData" / "Local"
        return cls((user_base / APP_DIRECTORY).resolve())

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def templates(self) -> Path:
        return self.root / "templates"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def audio_logs(self) -> Path:
        return self.logs / "audio"

    @property
    def sessions(self) -> Path:
        return self.logs / "sessions"

    def ensure_directories(self) -> None:
        for directory in (self.root, self.templates, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
