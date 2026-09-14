"""Small presentation view; comparison/final sequence UI follows in Phase 8."""
from PySide6.QtCore import Qt, Signal
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
                item.setToolTip(tooltip)
                self.table.setItem(row, column, item)
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
        self.reason_label.setText(messages.get(result.reason, messages.get(result.state.value, result.reason)))
