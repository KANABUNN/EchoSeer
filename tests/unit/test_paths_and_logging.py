"""User-data selection and local event/diagnostic persistence."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import time

import pytest

from app.paths import AppPaths
from logging_ext.event_logger import EventLogger
from logging_ext.session_logger import close_logging, configure_logging


def test_local_appdata_is_preferred(tmp_path: Path) -> None:
    paths = AppPaths.discover(environ={
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
    })
    assert paths.root == tmp_path / "local" / "OracleAssistant"
    assert paths.config == paths.root / "config.json"
    assert paths.audio_logs == paths.root / "logs" / "audio"
    assert paths.sessions == paths.root / "logs" / "sessions"


def test_appdata_is_fallback(tmp_path: Path) -> None:
    paths = AppPaths.discover(environ={"LOCALAPPDATA": "", "APPDATA": str(tmp_path)})
    assert paths.root == tmp_path / "OracleAssistant"


def test_missing_environment_falls_back_to_user_home() -> None:
    paths = AppPaths.discover(environ={})
    assert paths.root == (Path.home() / "AppData" / "Local" / "OracleAssistant").resolve()


def test_explicit_data_dir_and_creation(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "portable", environ={})
    paths.ensure_directories()
    paths.ensure_directories()
    assert paths.root == tmp_path / "portable"
    assert paths.templates.is_dir()
    assert paths.logs.is_dir()


def test_disabled_event_logging_creates_nothing(tmp_path: Path) -> None:
    directory = tmp_path / "disabled"
    EventLogger(directory / "events.jsonl", enabled=False).write({"oracle": "L1"})
    assert not directory.exists()


def test_events_keep_wall_clock_and_monotonic_times_separate(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    before = time.time()
    EventLogger(path).write({"oracle": "中央", "stream_time": 1.25, "scores": {"MID": 0.9}})
    event = json.loads(path.read_text(encoding="utf-8"))
    assert before <= event["timestamp"] <= time.time()
    assert event["monotonic_time"] > 0
    assert event["stream_time"] == 1.25
    assert event["oracle"] == "中央"


def test_concurrent_events_remain_valid_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    logger = EventLogger(path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda index: logger.write({"index": index}), range(50)))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 50
    assert {item["index"] for item in records} == set(range(50))


def test_invalid_event_does_not_create_log(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    with pytest.raises(ValueError):
        EventLogger(path).write({"score": float("nan")})
    assert not path.exists()


def test_reconfiguring_diagnostics_does_not_duplicate_handlers(tmp_path: Path) -> None:
    try:
        configure_logging(tmp_path)
        logger = configure_logging(tmp_path)
        logging.getLogger("oracle_assistant.config").warning("日本語の診断")
        logger.info("single record")
        assert len(logger.handlers) == 1
    finally:
        close_logging()
    text = (tmp_path / "application.log").read_text(encoding="utf-8")
    assert text.count("single record") == 1
    assert "日本語の診断" in text
    assert not logger.handlers
