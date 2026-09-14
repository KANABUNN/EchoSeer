"""Independent passes, explicit verification and marked inferred sequence."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from config.defaults import DEFAULT_ORACLE_LABELS
from encounter.definition import VOG_ORACLES
from encounter.sequence import SequenceSnapshot, SequenceState


class SequenceWidget(QGroupBox):
    analyze_requested = Signal()

    def __init__(self, labels=None):
        super().__init__("Oracle の順序")
        self.labels = labels or DEFAULT_ORACLE_LABELS
        self.result: SequenceSnapshot | None = None
        self._busy = False
        self._available = False
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.round_combo = QComboBox()
        for index, count in enumerate(VOG_ORACLES.sequence_lengths, 1):
            self.round_combo.addItem(f"Round {index} · {count} 個", index)
        self.round_combo.setAccessibleName("解析するラウンド")
        self.analyze_button = QPushButton("順序を解析")
        self.next_button = QPushButton("Next Round")
        self.reset_button = QPushButton("Reset")
        controls.addWidget(self.round_combo)
        controls.addWidget(self.analyze_button)
        controls.addStretch()
        controls.addWidget(self.next_button)
        controls.addWidget(self.reset_button)
        layout.addLayout(controls)
        self.state_label = QLabel()
        self.state_label.setTextFormat(Qt.TextFormat.PlainText)
        self.state_label.setWordWrap(True)
        self.state_label.setAccessibleName("順序解析の状態")
        layout.addWidget(self.state_label)
        self.verification_label = QLabel()
        self.verification_label.setTextFormat(Qt.TextFormat.PlainText)
        self.verification_label.setWordWrap(True)
        self.verification_label.setAccessibleName("PASSの照合結果と不一致位置")
        layout.addWidget(self.verification_label)
        self.table = QTableWidget(2, 7)
        self.table.setHorizontalHeaderLabels([str(i) for i in range(1, 8)])
        self.table.setVerticalHeaderLabels(["PASS 1", "PASS 2"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.setFixedHeight(160)
        self.table.setStyleSheet("QHeaderView::section { background: #182234; color: #e4ecf8; padding: 4px; }")
        self.table.setAccessibleName("独立した PASS1 と PASS2 の認識結果")
        layout.addWidget(self.table)
        self.final_label = QLabel()
        self.final_label.setTextFormat(Qt.TextFormat.PlainText)
        self.final_label.setWordWrap(True)
        self.final_label.setAccessibleName("確定順または確認が必要な推定順")
        layout.addWidget(self.final_label)
        self.reason_label = QLabel()
        self.reason_label.setTextFormat(Qt.TextFormat.PlainText)
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)
        self.analyze_button.clicked.connect(self.analyze_requested)
        self.next_button.clicked.connect(self._next)
        self.reset_button.clicked.connect(self._reset)
        self.round_combo.currentIndexChanged.connect(self.clear)
        self.clear()

    @property
    def round_index(self):
        return self.round_combo.currentData()

    def _next(self):
        if self.round_combo.currentIndex() < self.round_combo.count() - 1:
            self.round_combo.setCurrentIndex(self.round_combo.currentIndex() + 1)

    def _reset(self):
        self.round_combo.setCurrentIndex(0)
        self.clear()

    def set_available(self, available):
        self._available = available
        self._controls()

    def set_busy(self, busy):
        self._busy = busy
        self._controls()

    def _controls(self):
        self.analyze_button.setEnabled(self._available and not self._busy)
        self.round_combo.setEnabled(not self._busy)
        self.next_button.setEnabled(not self._busy and self.round_index < self.round_combo.count())
        self.reset_button.setEnabled(not self._busy)

    def clear(self, *_):
        self.result = None
        self.verification_label.clear()
        self.verification_label.hide()
        self.final_label.clear()
        self.final_label.hide()
        count = VOG_ORACLES.expected_count(self.round_index)
        self.state_label.setText(f"状態：IDLE / Round {self.round_index} · {count} 個")
        self.reason_label.setText("PASS1 と PASS2 を含む 1 ラウンドの音声を解析します。")
        self.table.clearContents()
        self.table.hide()
        self.reason_label.hide()
        for column in range(7):
            self.table.setColumnHidden(column, column >= count)
            for row in range(2):
                self.table.setItem(row, column, QTableWidgetItem("—"))
        self._controls()

    def set_result(self, result: SequenceSnapshot):
        self.result = result
        verification = result.verification
        self.verification_label.show()
        self.table.show()
        self.reason_label.show()
        self.state_label.setText(f"状態：{result.state.value} / Round {result.round_index} · {result.expected_count} 個")
        self.table.clearContents()
        for column in range(7):
            self.table.setColumnHidden(column, column >= result.expected_count)
            for row, entries in enumerate((result.pass1, result.pass2)):
                entry = entries[column] if column < len(entries) else None
                text = "—"
                tooltip = ""
                if entry:
                    event = entry.detection
                    name = entry.oracle.value if entry.oracle else "unknown"
                    text = f"{name}\n{event.status.value}\n{event.confidence:.3f}" if event.confidence is not None else f"{name}\n{event.status.value}"
                    tooltip = f"{entry.timestamp:.3f}〜{entry.end_time:.3f} 秒"
                    if event.best_candidate:
                        tooltip += f"\n候補：{event.best_candidate.value} · {self.labels[event.best_candidate.value]}"
                    tooltip += f"\n理由：{event.reason}"
                item = QTableWidgetItem(text)
                if verification:
                    validation = verification.pass1 if row == 0 else verification.pass2
                    history = verification.history.pass1 if row == 0 else verification.history.pass2
                    duplicate = next((d for d in validation.duplicates if column+1 in d.indices), None)
                    if column+1 in verification.mismatch_indices:
                        item.setBackground(QColor("#472936"))
                        tooltip += f"\n不一致位置：{column+1}"
                    if duplicate:
                        item.setBackground(QColor("#4c3a20"))
                        tooltip += "\n同一PASSの重複：" + duplicate.oracle.value + " / 位置 " + ", ".join(map(str,duplicate.indices))
                    if column < len(history):
                        tooltip += "\n上位候補：" + " / ".join(f"{c.oracle.value} {c.score:.3f}" for c in history[column].candidates)
                item.setToolTip(tooltip)
                self.table.setItem(row, column, item)
        if verification is None:
            self.verification_label.setText("照合：2つのPASSを待っています。")
            self.verification_label.setStyleSheet("")
            self.final_label.hide()
        else:
            text = "照合：" + verification.status.value
            if verification.mismatch_indices:
                text += " / 不一致位置：" + ", ".join(map(str,verification.mismatch_indices))
            duplicates = []
            for pass_number, validation in enumerate((verification.pass1,verification.pass2),1):
                for duplicate in validation.duplicates:
                    duplicates.append(f"PASS{pass_number} {duplicate.oracle.value}（位置 " + ", ".join(map(str,duplicate.indices)) + "）")
            if duplicates:
                text += "\n同一PASSの重複：" + " / ".join(duplicates)
            self.verification_label.setText(text)
            color = "#81d6b5" if verification.status.value == "CONFIRMED" else "#f29aaa" if verification.status.value == "MISMATCH" else "#eac16f"
            self.verification_label.setStyleSheet(f"color: {color}; font-weight: 600;")
            prefix = "確定順：" if verification.status.value == "CONFIRMED" else "推定順（要確認）：" if verification.status.value == "INFERRED" else "確認が必要な順序："
            self.final_label.setText(prefix + "  ".join(oracle.value if oracle else "?" for oracle in verification.sequence))
            self.final_label.show()
        messages = {
            "ARMED": "最初の音を待っています。",
            "PASS_1": "1 回目の提示を記録しています。",
            "WAIT_PASS_2": "1 回目を保存しました。2 回目の提示を待っています。",
            "PASS_2": "2 回目の提示を別に記録しています。",
            "AWAITING_VERIFICATION": "2 つの PASS を保存しました。照合待ちです。",
            "UNKNOWN_EVENT": "不確かな音を含みます。unknown の位置を残しています。",
            "EARLY_PASS_2": "2 回目までの間隔が短すぎます。確認が必要です。",
            "INCOMPLETE_PASS": "期待個数に届く前に長い間隔がありました。確認が必要です。",
            "INCOMPLETE_INPUT": "2 つの PASS が揃う前に音声が終了しました。",
            "EVENT_TIMEOUT": "次の音を待つ時間を超えました。",
            "NO_EVENTS": "音の候補がありません。区間と入力音量を確認してください。",
            "SOURCE_CHANGED": "音声ソースが切り替わったため確認が必要です。",
            "STALE_EVENT": "過去時刻の候補を除外しました。",
            "VERIFICATION_FAILED": "照合結果に確認が必要です。",
            "VERIFIED": "2 つの PASS が照合済みです。",
            "LOCKOUT": "次のラウンドまで候補を追加しません。",
        }
        messages.update({
            "MATCHING_PASSES": "2回の採用結果が完全一致しました。",
            "CONFIDENCE_INFERENCE": "信頼度差から推定しました。確定結果ではありません。",
            "RECONSTRUCTED": "上位候補とVoGルールから推定しました。順序を確認してください。",
            "DUPLICATE_ORACLE": "同一PASSに重複候補があります。元の認識結果を残しています。",
            "NO_VALID_SEQUENCE": "保存された候補では、重複のない順序を組めません。",
            "CORRECTION_LIMIT": "不確かな位置が補正上限を超えています。確認してください。",
            "AMBIGUOUS_RECONSTRUCTION": "複数の補正案が同点または僅差です。確認してください。",
            "WEAK_RECONSTRUCTION": "候補の根拠が弱いため、自動で推定順を採用しません。",
            "STRONG_PASS_CONFLICT": "信頼度の高い認識結果が食い違っています。確認してください。",
            "UNSAFE_EVENT": "途中で切れた音など、推定に使えない位置があります。",
            "INCOMPLETE_PASSES": "期待個数のPASSが揃っていません。",
            "UNRESOLVED_PASSES": "不一致またはunknownが残っています。確認してください。",
        })
        self.reason_label.setText(messages.get(result.reason, messages.get(result.state.value, result.reason)))
