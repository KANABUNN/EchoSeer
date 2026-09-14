"""Audio window identity and timestamp provenance, independent of recognition and Qt."""
from dataclasses import dataclass
import math

@dataclass(frozen=True, slots=True)
class EventContext:
    source: str = "replay"
    timestamp: float = 0.0
    stream_id: str | None = None
    start_frame: int | None = None
    end_frame: int | None = None

    def __post_init__(self) -> None:
        if self.source not in ("live", "replay"):
            raise ValueError("Unknown audio source")
        if type(self.timestamp) not in (int, float) or not math.isfinite(self.timestamp) or self.timestamp < 0:
            raise ValueError("Event time must be finite and monotonic")
        if self.source == "live" and (not isinstance(self.stream_id, str) or not self.stream_id):
            raise ValueError("Live decisions require a stream identity")
        if (self.start_frame is None) != (self.end_frame is None):
            raise ValueError("Audio frame bounds must be paired")
        if self.start_frame is not None:
            if (type(self.start_frame) is not int or type(self.end_frame) is not int
                    or not 0 <= self.start_frame <= self.end_frame):
                raise ValueError("Invalid audio frame bounds")
