"""Configuration data only; no Qt, audio backend, or DSP imports."""

from dataclasses import asdict, dataclass, field, fields, is_dataclass
import math
from types import UnionType
from typing import Any, get_args, get_origin, get_type_hints

from config.defaults import (
    DEFAULT_MAP_POSITIONS, DEFAULT_ORACLE_LABELS, ORACLE_KEYS, SCHEMA_VERSION,
)


class ConfigValidationError(ValueError):
    """Configuration cannot be interpreted safely."""


@dataclass(slots=True)
class DeviceIdentity:
    host_api: str = "Windows WASAPI"
    device_name: str = ""
    loopback: bool = True
    channels: int | None = None
    native_sample_rate: int | None = None


@dataclass(slots=True)
class AudioSettings:
    backend: str = "wasapi_loopback"
    device: DeviceIdentity | None = None
    internal_sample_rate: int = 48000
    buffer_duration: float = 10.0
    auto_reconnect: bool = True
    reconnect_interval: float = 2.0


@dataclass(slots=True)
class RecognitionSettings:
    waveform_weight: float = 0.60
    spectrum_weight: float = 0.40
    detection_threshold: float = 0.025
    confidence_threshold: float = 0.82
    high_confidence_threshold: float = 0.92
    low_score_threshold: float = 0.55
    margin_threshold: float = 0.10
    high_margin_threshold: float = 0.18
    duplicate_cooldown: float = 0.45
    bandpass_enabled: bool = False
    bandpass_low_hz: float = 350.0
    bandpass_high_hz: float = 3000.0
    template_aggregation: str = "best"
    top_n: int = 3


@dataclass(slots=True)
class SequenceSettings:
    auto_advance: bool = True
    lockout_duration: float = 10.0
    silence_duration: float = 3.0
    pass_gap: float = 2.0
    event_timeout: float = 5.0
    candidate_top_n: int = 3
    max_corrections: int = 1
    inference_margin: float = 0.18
    reconstruction_margin: float = 0.10


@dataclass(slots=True)
class OverlaySettings:
    enabled: bool = False
    x: int = 40
    y: int = 40
    width: int = 660
    height: int = 130
    opacity: float = 0.90
    scale: float = 1.0
    font_size: int = 28
    mode: str = "sequence"
    click_through: bool = True


@dataclass(slots=True)
class LoggingSettings:
    event_logs: bool = True
    uncertain_audio: bool = True
    success_audio: bool = False
    full_recording: bool = False
    debug: bool = False


@dataclass(slots=True)
class HotkeySettings:
    enabled: bool = False
    toggle_capture: str = "Ctrl+Alt+F7"
    reset: str = "Ctrl+Alt+F8"
    next_round: str = "Ctrl+Alt+F9"
    toggle_overlay: str = "Ctrl+Alt+F10"


@dataclass(slots=True)
class MapPosition:
    x: float = 0.5
    y: float = 0.5


def _default_map() -> dict[str, MapPosition]:
    return {key: MapPosition(*point) for key, point in DEFAULT_MAP_POSITIONS.items()}


def _decode(value: Any, expected: Any, location: str) -> Any:
    """Strict recursive decoder; dataclass defaults fill missing fields."""
    origin = get_origin(expected)
    if origin is UnionType:
        if value is None and type(None) in get_args(expected):
            return None
        for option in get_args(expected):
            if option is not type(None):
                try:
                    return _decode(value, option, location)
                except ConfigValidationError:
                    continue
        raise ConfigValidationError(f"{location}: invalid optional value")
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigValidationError(f"{location}: expected object")
        key_type, item_type = get_args(expected)
        return {
            _decode(key, key_type, location): _decode(item, item_type, f"{location}.{key}")
            for key, item in value.items()
        }
    if is_dataclass(expected):
        if not isinstance(value, dict):
            raise ConfigValidationError(f"{location}: expected object")
        allowed = {item.name for item in fields(expected)}
        unknown = set(value) - allowed
        if unknown:
            raise ConfigValidationError(f"{location}: unknown fields {sorted(unknown)}")
        hints = get_type_hints(expected)
        return expected(**{
            key: _decode(item, hints[key], f"{location}.{key}")
            for key, item in value.items()
        })
    if expected is float:
        if type(value) not in (int, float):
            raise ConfigValidationError(f"{location}: expected finite number")
        try:
            number = float(value)
        except OverflowError as error:
            raise ConfigValidationError(f"{location}: number is too large") from error
        if not math.isfinite(number):
            raise ConfigValidationError(f"{location}: expected finite number")
        return number
    if expected in (bool, int, str) and type(value) is expected:
        return value
    raise ConfigValidationError(f"{location}: expected {expected.__name__}")


def _range(name: str, value: float | int, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ConfigValidationError(f"{name}: must be between {low} and {high}")


@dataclass(slots=True)
class AppConfig:
    schema_version: int = SCHEMA_VERSION
    audio: AudioSettings = field(default_factory=AudioSettings)
    recognition: RecognitionSettings = field(default_factory=RecognitionSettings)
    sequence: SequenceSettings = field(default_factory=SequenceSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    oracle_labels: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_ORACLE_LABELS)
    )
    oracle_map_positions: dict[str, MapPosition] = field(default_factory=_default_map)

    @classmethod
    def from_dict(cls, data: Any) -> "AppConfig":
        if not isinstance(data, dict):
            raise ConfigValidationError("config: expected object")
        if "schema_version" not in data:
            raise ConfigValidationError("config: schema_version is required")
        payload = dict(data)
        defaults = asdict(cls())
        for key in ("oracle_labels", "oracle_map_positions"):
            if key in payload:
                if not isinstance(payload[key], dict):
                    raise ConfigValidationError(f"{key}: expected object")
                payload[key] = {**defaults[key], **payload[key]}
        config = _decode(payload, cls, "config")
        config._validate_ranges()
        return config

    def validate(self) -> None:
        # Includes values assigned after construction.
        _decode(asdict(self), AppConfig, "config")._validate_ranges()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def _validate_ranges(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ConfigValidationError(f"unsupported schema_version: {self.schema_version}")
        if self.audio.backend not in ("wasapi_loopback", "input_device"):
            raise ConfigValidationError("audio.backend: unsupported backend")
        _range("audio.internal_sample_rate", self.audio.internal_sample_rate, 8000, 192000)
        _range("audio.buffer_duration", self.audio.buffer_duration, 5, 30)
        _range("audio.reconnect_interval", self.audio.reconnect_interval, .25, 60)
        from config.hotkeys import bindings
        try:
            bindings(self.hotkeys)
        except ValueError as error:
            raise ConfigValidationError(str(error)) from error
        device = self.audio.device
        if device is not None:
            if not device.host_api.strip() or not device.device_name.strip():
                raise ConfigValidationError("audio.device: API and name are required")
            if device.loopback != (self.audio.backend == "wasapi_loopback"):
                raise ConfigValidationError("audio.device: loopback does not match backend")
            if device.channels is not None:
                _range("audio.device.channels", device.channels, 1, 64)
            if device.native_sample_rate is not None:
                _range("audio.device.native_sample_rate", device.native_sample_rate, 8000, 384000)
        recognition = self.recognition
        for name in (
            "waveform_weight", "spectrum_weight", "detection_threshold",
            "confidence_threshold", "high_confidence_threshold", "low_score_threshold",
            "margin_threshold", "high_margin_threshold",
        ):
            _range(f"recognition.{name}", getattr(recognition, name), 0, 1)
        if not math.isclose(
            recognition.waveform_weight + recognition.spectrum_weight, 1.0, abs_tol=1e-6
        ):
            raise ConfigValidationError("recognition weights must sum to 1")
        if not (
            recognition.low_score_threshold <= recognition.confidence_threshold
            <= recognition.high_confidence_threshold
        ):
            raise ConfigValidationError("recognition score thresholds must be ordered")
        if recognition.margin_threshold > recognition.high_margin_threshold:
            raise ConfigValidationError("recognition margin thresholds must be ordered")
        _range("recognition.duplicate_cooldown", recognition.duplicate_cooldown, 0, 10)
        if not (
            0 < recognition.bandpass_low_hz < recognition.bandpass_high_hz
            < self.audio.internal_sample_rate / 2
        ):
            raise ConfigValidationError("bandpass range must be below Nyquist")
        if recognition.template_aggregation not in ("best", "top_n_mean"):
            raise ConfigValidationError("recognition.template_aggregation: unsupported mode")
        _range("recognition.top_n", recognition.top_n, 1, 20)
        for name in ("lockout_duration", "silence_duration", "pass_gap", "event_timeout"):
            _range(f"sequence.{name}", getattr(self.sequence, name), 0.01, 120)
        _range("sequence.candidate_top_n", self.sequence.candidate_top_n, 1, 7)
        _range("sequence.max_corrections", self.sequence.max_corrections, 0, 7)
        for name in ("inference_margin", "reconstruction_margin"):
            _range(f"sequence.{name}", getattr(self.sequence, name), 0, 1)
        _range("overlay.width", self.overlay.width, 120, 3840)
        _range("overlay.height", self.overlay.height, 60, 2160)
        _range("overlay.opacity", self.overlay.opacity, 0.05, 1)
        _range("overlay.scale", self.overlay.scale, 0.25, 4)
        _range("overlay.font_size", self.overlay.font_size, 8, 160)
        if self.overlay.mode not in ("sequence", "map"):
            raise ConfigValidationError("overlay.mode: unsupported mode")
        for name, mapping in (
            ("oracle_labels", self.oracle_labels),
            ("oracle_map_positions", self.oracle_map_positions),
        ):
            if set(mapping) != set(ORACLE_KEYS):
                raise ConfigValidationError(f"{name}: requires exactly seven Oracle keys")
        if any(not label.strip() for label in self.oracle_labels.values()):
            raise ConfigValidationError("oracle_labels: labels must not be empty")
        for key, point in self.oracle_map_positions.items():
            _range(f"oracle_map_positions.{key}.x", point.x, 0, 1)
            _range(f"oracle_map_positions.{key}.y", point.y, 0, 1)
