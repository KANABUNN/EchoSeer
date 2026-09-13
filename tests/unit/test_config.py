"""Settings round trips, malformed input, and failed writes."""

from dataclasses import asdict
import json
from pathlib import Path
import shutil

import pytest

from config.manager import ConfigManager
from config.schema import AppConfig, ConfigValidationError, DeviceIdentity


def test_settings_round_trip_preserves_custom_values(tmp_path: Path) -> None:
    config = AppConfig()
    config.audio.device = DeviceIdentity(
        device_name="Headphones (USB)", channels=2, native_sample_rate=44100
    )
    config.recognition.waveform_weight = 0.7
    config.recognition.spectrum_weight = 0.3
    config.overlay.x = -850
    config.oracle_labels["MID"] = "M"
    config.oracle_map_positions["L3"].x = 0.2
    manager = ConfigManager(tmp_path / "user-data" / "config.json")
    manager.save(config)
    assert manager.load() == config
    assert manager.last_warning is None
    saved = json.loads(manager.path.read_text(encoding="utf-8"))
    assert "device_index" not in saved["audio"]["device"]
    assert saved["audio"]["device"]["native_sample_rate"] == 44100


def test_first_launch_creates_defaults(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "user-data" / "config.json")
    assert manager.load() == AppConfig()
    assert manager.path.is_file()
    assert manager.last_warning is None


def test_partial_settings_fill_defaults_without_mutating_input() -> None:
    data = {
        "schema_version": 1,
        "audio": {"buffer_duration": 15},
        "oracle_labels": {"MID": "M"},
        "oracle_map_positions": {"L1": {"x": 0.4, "y": 0.6}},
    }
    before = json.dumps(data)
    config = AppConfig.from_dict(data)
    assert config.audio.buffer_duration == 15.0
    assert config.audio.internal_sample_rate == 48000
    assert config.oracle_labels["MID"] == "M"
    assert config.oracle_labels["L2"] == "左2"
    assert config.oracle_map_positions["L1"].x == 0.4
    assert json.dumps(data) == before


def test_default_mappings_are_independent() -> None:
    first, second = AppConfig(), AppConfig()
    first.oracle_labels["MID"] = "changed"
    first.oracle_map_positions["L1"].x = 0.9
    assert second.oracle_labels["MID"] == "中央"
    assert second.oracle_map_positions["L1"].x == 0.12


@pytest.mark.parametrize("section,key,value", [
    ("audio", "buffer_duration", 4),
    ("audio", "buffer_duration", 31),
    ("audio", "internal_sample_rate", "48000"),
    ("audio", "internal_sample_rate", True),
    ("audio", "backend", "asio"),
    ("recognition", "waveform_weight", 0),
    ("recognition", "confidence_threshold", 0.4),
    ("recognition", "high_margin_threshold", 0.01),
    ("recognition", "bandpass_high_hz", 24000),
    ("recognition", "top_n", 0),
    ("recognition", "template_aggregation", "unknown"),
    ("sequence", "lockout_duration", -1),
    ("overlay", "opacity", float("nan")),
    ("overlay", "scale", 0),
    ("overlay", "click_through", "yes"),
    ("logging", "full_recording", 1),
])
def test_invalid_settings_are_rejected(section: str, key: str, value: object) -> None:
    data = asdict(AppConfig())
    data[section][key] = value
    with pytest.raises(ConfigValidationError):
        AppConfig.from_dict(data)


@pytest.mark.parametrize("version", [0, 2, True, "1", None])
def test_unsupported_schema_is_rejected(version: object) -> None:
    with pytest.raises(ConfigValidationError):
        AppConfig.from_dict({"schema_version": version})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ConfigValidationError):
        AppConfig.from_dict({"schema_version": 1, "audio": {"device_index": 4}})


def test_device_backend_must_match_loopback() -> None:
    config = AppConfig()
    config.audio.device = DeviceIdentity(device_name="VoiceMeeter Output", loopback=False)
    with pytest.raises(ConfigValidationError):
        config.validate()
    config.audio.backend = "input_device"
    config.validate()


@pytest.mark.parametrize("raw", [
    b"{invalid json",
    b'{"schema_version":2}',
    b'{"schema_version":1,"overlay":{"opacity":NaN}}',
    b'{"schema_version":1,"audio":{"buffer_duration":"ten"}}',
    b'[]',
    b"{}",
    b"\xff\xfe\x00",
])
def test_damaged_settings_are_backed_up_and_recovered(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(raw)
    manager = ConfigManager(path)
    assert manager.load() == AppConfig()
    assert manager.last_warning
    assert manager.last_backup is not None
    assert manager.last_backup.read_bytes() == raw
    assert AppConfig.from_dict(json.loads(path.read_text(encoding="utf-8"))) == AppConfig()


def test_utf8_bom_settings_are_accepted(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(asdict(AppConfig())), encoding="utf-8-sig")
    manager = ConfigManager(path)
    assert manager.load() == AppConfig()
    assert manager.last_backup is None


def test_backup_failure_preserves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b"{invalid")
    def fail_backup(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated backup failure")
    monkeypatch.setattr(shutil, "copy2", fail_backup)
    manager = ConfigManager(path)
    assert manager.load() == AppConfig()
    assert path.read_bytes() == b"{invalid"
    assert manager.last_backup is None
    assert "保持" in manager.last_warning


def test_atomic_replace_failure_preserves_saved_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.save(AppConfig())
    original = path.read_bytes()
    def fail_replace(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated replacement failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    config = AppConfig()
    config.overlay.opacity = 0.5
    with pytest.raises(PermissionError):
        manager.save(config)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".config-*.tmp"))


def test_mutated_invalid_settings_cannot_overwrite_previous_file(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.save(AppConfig())
    original = manager.path.read_bytes()
    config = AppConfig()
    config.logging.full_recording = "yes"
    with pytest.raises(ConfigValidationError):
        manager.save(config)
    assert manager.path.read_bytes() == original


def test_large_integer_in_float_setting_does_not_crash_startup(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "schema_version": 1, "audio": {"buffer_duration": 10 ** 1000},
    }), encoding="utf-8")
    manager = ConfigManager(path)
    assert manager.load() == AppConfig()
    assert manager.last_backup is not None


def test_deeply_malformed_json_does_not_crash_startup(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
    manager = ConfigManager(path)
    assert manager.load() == AppConfig()
    assert manager.last_backup is not None
