"""Versioned per-sample metadata; malformed entries never reset the catalog."""
from dataclasses import asdict, dataclass, fields
from datetime import datetime
import math
import re
from typing import Any

from audio.data import AudioDataError
from encounter.vog_oracles import OracleId

ID_PATTERN = re.compile(r"[0-9a-f]{32}")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class SampleMetadata:
    schema_version: int
    sample_id: str
    oracle: str
    sample_rate: int
    frames: int
    duration_seconds: float
    created_at: str
    source: str
    source_name: str
    original_sample_rate: int
    original_channels: int
    original_frames: int
    peak: float
    rms: float
    clipping: bool
    checksum: str
    original_checksum: str
    notices: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "SampleMetadata":
        try:
            if not isinstance(value, dict) or set(value) != {f.name for f in fields(cls)}:
                raise ValueError("fields")
            data = dict(value)
            if type(data["schema_version"]) is not int or data["schema_version"] != 1:
                raise ValueError("version")
            if not isinstance(data["sample_id"], str) or not ID_PATTERN.fullmatch(data["sample_id"]):
                raise ValueError("id")
            OracleId(data["oracle"])
            for key, low, high in (
                ("sample_rate", 8000, 192000), ("original_sample_rate", 8000, 384000),
                ("original_channels", 1, 64), ("frames", 1, 1920000),
                ("original_frames", 1, 3840000),
            ):
                if type(data[key]) is not int or not low <= data[key] <= high:
                    raise ValueError(key)
            for key in ("duration_seconds", "peak", "rms"):
                if type(data[key]) not in (int, float) or not math.isfinite(data[key]) or data[key] < 0:
                    raise ValueError(key)
            if not 0 < data["duration_seconds"] <= 10.001:
                raise ValueError("duration")
            if abs(data["duration_seconds"] - data["frames"] / data["sample_rate"]) > 1e-9:
                raise ValueError("duration mismatch")
            if data["original_frames"] / data["original_sample_rate"] > 10:
                raise ValueError("original duration")
            created = datetime.fromisoformat(data["created_at"])
            if created.tzinfo is None or created.utcoffset() is None:
                raise ValueError("timezone")
            if data["source"] not in ("recorded", "imported"):
                raise ValueError("source")
            if not isinstance(data["source_name"], str) or len(data["source_name"]) > 255:
                raise ValueError("name")
            if type(data["clipping"]) is not bool:
                raise ValueError("clipping")
            for key in ("checksum", "original_checksum"):
                if not isinstance(data[key], str) or not HASH_PATTERN.fullmatch(data[key]):
                    raise ValueError(key)
            notices = data["notices"]
            if not isinstance(notices, (list, tuple)) or len(notices) > 16:
                raise ValueError("notices")
            if any(not isinstance(n, str) or len(n) > 1000 for n in notices):
                raise ValueError("notice")
            data["notices"] = tuple(notices)
            return cls(**data)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            raise AudioDataError("サンプル情報が不正です。") from error
