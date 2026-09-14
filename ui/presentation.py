"""Shared Live/Overlay wording; only exact confirmation receives a confirmed title."""
from dataclasses import dataclass
import math

from encounter.sequence import SequenceSnapshot, SequenceState
from encounter.vog_oracles import OracleId


@dataclass(frozen=True, slots=True)
class SequencePresentation:
    round_index: int
    expected_count: int
    status: str
    state_text: str
    sequence: tuple[OracleId | None, ...]
    sequence_text: str
    confidence_text: str
    count_text: str
    color: str


def present(snapshot: SequenceSnapshot, message: str = "") -> SequencePresentation:
    verification = snapshot.verification
    status = verification.status.value if verification else snapshot.state.value
    labels = {
        "IDLE": "STOPPED", "ARMED": "LISTENING…", "PASS_1": "PASS 1 · 認識中",
        "WAIT_PASS_2": "PASS 1 保存 · 2回目を待機", "PASS_2": "VERIFYING…",
        "VERIFY": "VERIFYING…", "CONFIRMED": "✓ CONFIRMED",
        "LOCKOUT": "✓ CONFIRMED · LOCKOUT", "UNCERTAIN": "⚠ CHECK",
        "MISMATCH": "⚠ MISMATCH", "INFERRED": "⚠ INFERRED · 要確認", "CHECK": "⚠ CHECK",
    }
    state_text = labels.get(status, status)
    color = "#81d6b5" if snapshot.confirmed else "#eac16f" if status in ("INFERRED", "CHECK", "UNCERTAIN") else "#f29aaa" if status == "MISMATCH" else "#a8c8ff"
    if snapshot.confirmed:
        sequence, prefix = snapshot.final_sequence, "確定順"
        if snapshot.state == SequenceState.LOCKOUT:
            state_text = labels["LOCKOUT"]
    elif verification is not None:
        sequence = verification.sequence
        prefix = "推定順（要確認）" if status == "INFERRED" else "確認が必要な順序"
    else:
        entries = snapshot.pass2 or snapshot.pass1
        sequence = tuple(entry.oracle for entry in entries)
        prefix = "PASS 2" if snapshot.pass2 else "PASS 1"
    sequence = tuple(sequence or ())
    if message:
        status, state_text, color = "PAUSED", message, "#eac16f"
        sequence, prefix = (), "順序"
    text = " → ".join(item.value if item else "?" for item in sequence)
    sequence_text = f"{prefix}：{text}" if text else "Oracleの提示を待っています。"
    scores = [entry.detection.confidence for entry in (*snapshot.pass1, *snapshot.pass2)
              if type(entry.detection.confidence) in (int, float) and math.isfinite(entry.detection.confidence)]
    levels = {entry.detection.status.value for entry in (*snapshot.pass1, *snapshot.pass2)}
    uncertain = bool(levels & {"LOW", "REJECTED"})
    confidence = f"Confidence {min(scores):.3f}（最低）" + (" · 不確かな音あり" if uncertain else "") if scores and not message else "Confidence —"
    return SequencePresentation(snapshot.round_index, snapshot.expected_count, status, state_text,
        sequence, sequence_text, confidence,
        f"PASS 1  {len(snapshot.pass1)}/{snapshot.expected_count} 個    PASS 2  {len(snapshot.pass2)}/{snapshot.expected_count} 個",
        color)
