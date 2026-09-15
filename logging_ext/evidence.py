"""Bounded native cue references for manual review and later pass-conflict audio."""
from collections import deque
from dataclasses import dataclass

from audio.data import AudioClip
from detector.classifier import ClassificationResult
from detector.confidence import DetectionResult

MAX_RETAINED_BYTES=32*1024*1024


@dataclass(frozen=True,slots=True)
class CueEvidence:
    clip: AudioClip
    checksum: str
    detection: DetectionResult
    classification: ClassificationResult
    round_index: int
    pass_number: int
    index: int
    onset_detection: dict | None = None


class CueEvidenceCache:
    def __init__(self,max_bytes=MAX_RETAINED_BYTES):
        self.max_bytes=max_bytes
        self._items=deque();self._bytes=0;self.evicted=0;self._attempted=set()

    @property
    def latest(self):
        return self._items[-1] if self._items else None

    @property
    def retained_bytes(self):
        return self._bytes

    def clear(self):
        self._items.clear();self._bytes=0;self.evicted=0;self._attempted.clear()

    def add(self,item):
        size=item.clip.samples.nbytes
        if size>self.max_bytes:
            self.clear();self.evicted+=1;return
        self._items.append(item);self._bytes+=size
        while len(self._items)>14 or self._bytes>self.max_bytes:
            self._bytes-=self._items.popleft().clip.samples.nbytes;self.evicted+=1

    def save_problem(self,snapshot,recorder,cancel=None):
        if recorder is None or not recorder.settings.uncertain_audio or snapshot.confirmed:
            return ()
        verification=snapshot.verification
        indices=set(verification.mismatch_indices) if verification else set()
        notices=[]
        if self.evicted:
            notices.append("音声保持上限により、一部の照合問題の音声は保存できません。")
        for item in self._items:
            key=(item.pass_number,item.index)
            if key not in self._attempted and item.detection.accepted and (not indices or item.index in indices):
                self._attempted.add(key)
                saved=recorder.record(item.detection,item.classification,item.clip,item.checksum,cancel,
                    sequence={"round":item.round_index,"pass":item.pass_number,"index":item.index,
                              "state":snapshot.state.value,"reason":snapshot.reason},
                    problem_reason="PASS_"+(verification.status.value if verification else "CHECK"),
                    onset_detection=item.onset_detection)
                notices.extend(saved.notices)
        return tuple(dict.fromkeys(notices))
