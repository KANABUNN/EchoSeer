"""Actual entry point in isolated Qt processes; no audio devices required."""

from dataclasses import asdict
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_app(*args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args],
        cwd=ROOT, env=environment, capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    )


@pytest.mark.parametrize("module", [
    "numpy", "scipy.signal", "scipy.io", "PySide6.QtWidgets", "pyaudiowpatch", "PyInstaller",
])
def test_required_dependencies_import(module: str) -> None:
    assert importlib.import_module(module) is not None


def test_help_and_version_do_not_create_user_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "unused"
    for flag in ("--help", "--version"):
        result = run_app("--data-dir", str(data_dir), flag)
        assert result.returncode == 0, result.stderr
        assert "Oracle Assistant" in result.stdout
    assert not data_dir.exists()


@pytest.mark.gui
def test_start_close_restart_retains_settings(tmp_path: Path) -> None:
    from config.schema import AppConfig
    data_dir = tmp_path / "user-data"
    first = run_app("--data-dir", str(data_dir), "--smoke-test")
    assert first.returncode == 0, first.stderr
    path = data_dir / "config.json"
    config = AppConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
    config.oracle_labels["MID"] = "M"
    path.write_text(json.dumps(asdict(config)), encoding="utf-8")
    second = run_app("--data-dir", str(data_dir), "--smoke-test")
    assert second.returncode == 0, second.stderr
    assert json.loads(path.read_text(encoding="utf-8"))["oracle_labels"]["MID"] == "M"
    assert (data_dir / "templates").is_dir()
    log = (data_dir / "logs" / "application.log").read_text(encoding="utf-8")
    assert log.count("Starting Oracle Assistant") == 2
    assert log.count("Oracle Assistant stopped") == 2
    assert "Traceback" not in first.stderr + second.stderr


@pytest.mark.gui
def test_corrupt_config_still_opens_and_closes(tmp_path: Path) -> None:
    data_dir = tmp_path / "user-data"
    data_dir.mkdir()
    (data_dir / "config.json").write_bytes(b"{broken")
    result = run_app("--data-dir", str(data_dir), "--smoke-test")
    assert result.returncode == 0, result.stderr
    backups = list(data_dir.glob("config.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"{broken"
    assert json.loads((data_dir / "config.json").read_text(encoding="utf-8"))["schema_version"] == 1
