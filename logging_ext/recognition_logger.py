"""Optional recognition JSONL and bounded native uncertain audio on the analysis worker."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import logging
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

import numpy as np
from audio.data import AudioClip
from audio.operations import OperationCancelled, check_cancel
from audio.waveio import write_wav
from config.schema import LoggingSettings
from templates.manager import audio_checksum
from detector.classifier import ClassificationResult
from detector.confidence import ConfidenceLevel, DetectionResult
from logging_ext.event_logger import EventLogger

logger = logging.getLogger("oracle_assistant.recognition_log")
MAX_AUDIO_SECONDS = 3.0
MAX_AUDIO_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    event_path: Path | None = None
    audio_path: Path | None = None
    notices: tuple[str, ...] = ()


def uncertain_window(clip: AudioClip, cancel: Event | None = None) -> tuple[AudioClip, int, int]:
    """Up to three seconds around the loudest 100 ms block, preserving native samples."""
    check_cancel(cancel)
    frames = min(clip.frame_count, round(clip.sample_rate * MAX_AUDIO_SECONDS),
                 MAX_AUDIO_BYTES // (4 * clip.channels))
    if clip.frame_count <= frames:
        return clip, 0, clip.frame_count
    block = max(1, round(clip.sample_rate * .1))
    peak, center = -1.0, 0
    for start in range(0, clip.frame_count, block):
        check_cancel(cancel)
        values = clip.samples[start:start + block]
        power = float(np.mean(np.square(values, dtype=np.float64)))
        if power > peak:
            peak, center = power, start + len(values) // 2
    start = max(0, min(center - frames // 2, clip.frame_count - frames))
    check_cancel(cancel)
    return AudioClip(clip.samples[start:start + frames], clip.sample_rate), start, start + frames


class RecognitionRecorder:
    def __init__(self, logs: Path, settings: LoggingSettings | None = None, conditions: dict | None = None) -> None:
        self.logs = Path(logs)
        self.settings = replace(settings or LoggingSettings())
        self.conditions = conditions
        session = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex}"
        self.events = EventLogger(self.logs / "sessions" / f"{session}.jsonl", self.settings.event_logs)

    def record(self, detection: DetectionResult, classification: ClassificationResult,
               original: AudioClip, checksum: str, cancel: Event | None = None,
               sequence: dict | None = None, problem_reason: str | None = None,
               onset_detection: dict | None = None) -> PersistenceResult:
        check_cancel(cancel)
        event_id = uuid4().hex
        wall_time = time.time()
        notices, audio_path, bounds = [], None, None
        uncertain = (detection.best_candidate is not None
                     and (detection.duplicate or detection.status in (ConfidenceLevel.LOW, ConfidenceLevel.REJECTED)))
        save_uncertain=self.settings.uncertain_audio and (uncertain or problem_reason is not None)
        save_success=self.settings.success_audio and detection.accepted and not detection.duplicate
        audio_checksum_value=None
        if (save_uncertain or save_success) and original.frame_count:
            try:
                clip, start, end = uncertain_window(original, cancel)
                destination = self.logs / "audio" / f"{datetime.fromtimestamp(wall_time):%Y-%m-%d}" / f"{event_id}.wav"
                check_cancel(cancel)
                destination.parent.mkdir(parents=True, exist_ok=True)
                audio_path = write_wav(destination, clip, "float32", cancel)
                audio_checksum_value=audio_checksum(clip)
                bounds = {"start_frame": start, "end_frame": end,
                          "sample_rate": original.sample_rate, "channels": original.channels}
            except OperationCancelled:
                raise
            except (OSError, ValueError):
                logger.exception("Could not save uncertain event audio")
                notices.append("不確かな音声を保存できませんでした。保存先・空き容量を確認してください。")
        event_path = None
        if self.settings.event_logs:
            payload = {
                "schema_version": 1, "event_type": "audio_evidence" if problem_reason else "oracle", "event_id": event_id, "timestamp": wall_time,
                "round": sequence.get("round") if sequence else None,
                "pass": sequence.get("pass") if sequence else None,
                "index": sequence.get("index") if sequence else None,
                "sequence_state": sequence.get("state") if sequence else None,
                "sequence_reason": sequence.get("reason") if sequence else None,
                "monotonic_time": time.monotonic(), "event_time": detection.timestamp, "source": asdict(detection.context),
                "oracle": detection.oracle.value if detection.oracle else None,
                "best_candidate": detection.best_candidate.value if detection.best_candidate else None,
                "confidence": detection.confidence, "confidence_level": detection.status.value,
                "reason": detection.reason, "duplicate": detection.duplicate,
                "waveform_score": detection.waveform_score, "spectrum_score": detection.spectrum_score,
                "second_candidate": detection.second_candidate.value if detection.second_candidate else None,
                "second_score": detection.second_score, "margin": detection.margin,
                "classification_status": classification.status, "checksum": checksum,
                "onset_detection": onset_detection,
                "missing_oracles": [oracle.value for oracle in classification.missing_oracles],
                "ranking": [{"oracle": item.oracle.value, "combined_score": item.score,
                             "waveform_score": item.waveform_score, "spectrum_score": item.spectrum_score,
                             "sample_count": len(item.samples)} for item in classification.ranking]
                           if detection.reason != "INVALID_RANKING" else [],
                "weights": {"waveform": classification.waveform_weight, "spectrum": classification.spectrum_weight},
                "audio_path": str(audio_path.relative_to(self.logs)) if audio_path else None,
                "audio_native_checksum": audio_checksum_value, "conditions": self.conditions,
                "problem_reason": problem_reason,
                "audio_save_reason": "pass_issue" if problem_reason else "uncertain" if save_uncertain else "accepted_example" if save_success else None,
                "audio_window": bounds, "notices": [*classification.notices, *notices],
            }
            try:
                check_cancel(cancel)
                self.events.write(payload)
                event_path = self.events.path
            except OperationCancelled:
                raise
            except (OSError, ValueError):
                logger.exception("Could not save recognition event")
                notices.append("認識ログを保存できませんでした。保存先・空き容量を確認してください。")
        return PersistenceResult(event_path, audio_path, tuple(notices))

    def record_sequence(self, snapshot, checksum: str, cancel: Event | None = None,
                        source: dict | None = None) -> PersistenceResult:
        check_cancel(cancel)
        if not self.settings.event_logs:
            return PersistenceResult()
        def entry(item):
            event = item.detection
            return {"oracle": item.oracle.value if item.oracle else None,
                    "best_candidate": event.best_candidate.value if event.best_candidate else None,
                    "confidence": event.confidence, "confidence_level": event.status.value,
                    "second_candidate": event.second_candidate.value if event.second_candidate else None,
                    "second_score": event.second_score, "margin": event.margin,
                    "onset": item.timestamp, "end": item.end_time, "reason": event.reason}
        from dataclasses import asdict
        verification = asdict(snapshot.verification) if snapshot.verification else None
        if verification is not None:
            verification["mismatch_indices"] = list(snapshot.verification.mismatch_indices)
        payload = {
            "schema_version": 1, "event_type": "sequence", "event_id": uuid4().hex,
            "event_time": snapshot.timestamp, "round": snapshot.round_index,
            "expected_count": snapshot.expected_count, "state": snapshot.state.value,
            "reason": snapshot.reason, "confirmed": snapshot.confirmed,
            "final_sequence": [o.value for o in snapshot.final_sequence] if snapshot.final_sequence else None,
            "pass1": [entry(item) for item in snapshot.pass1], "pass2": [entry(item) for item in snapshot.pass2],
            "verification": verification,
            "suggested_sequence": [o.value for o in snapshot.suggested_sequence] if snapshot.suggested_sequence else None,
            "checksum": checksum, "ignored_events": snapshot.ignored_events,
            "transitions": [{"from": t.before.value, "to": t.after.value,
                             "event_time": t.timestamp, "reason": t.reason} for t in snapshot.transitions],
        }
        if source is not None:
            payload["source"] = source
        try:
            check_cancel(cancel)
            self.events.write(payload)
            return PersistenceResult(event_path=self.events.path)
        except OperationCancelled:
            raise
        except (OSError, ValueError):
            logger.exception("Could not save sequence summary")
            return PersistenceResult(notices=("PASS の記録を保存できませんでした。保存先・空き容量を確認してください。",))
