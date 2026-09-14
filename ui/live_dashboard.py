"""Glanceable Live round, map, two raw passes and explicit final/inferred results."""
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from config.defaults import DEFAULT_ORACLE_LABELS
from encounter.definition import VOG_ORACLES
from encounter.sequence import SequenceEngine, SequenceState
from ui.oracle_map import OracleMapWidget
from ui.presentation import present


class LiveDashboard(QGroupBox):
    round_requested = Signal(int)
    overlay_requested = Signal(bool)
    overlay_settings_requested = Signal()
    auto_advance_requested = Signal(bool)

    def __init__(self, labels=None, positions=None):
        super().__init__("Live Oracle")
        self.labels = dict(labels or DEFAULT_ORACLE_LABELS)
        self.result = None
        self.message = ""
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        row = QHBoxLayout()
        self.round_combo = QComboBox()
        self.round_combo.setAccessibleName("Liveのラウンド")
        for index, count in enumerate(VOG_ORACLES.sequence_lengths, 1):
            self.round_combo.addItem(f"Round {index} · {count}個", index)
        self.next_button, self.reset_button = QPushButton("Next Round"), QPushButton("Reset")
        self.reset_button.setText("Reset（Round 1）")
        self.retry_button = QPushButton("このRoundをやり直す")
        self.previous_button = QPushButton("前のRound")
        self.retry_button.setToolTip("現在のRoundの判定と保存候補を消して、提示を待ち直します。")
        self.reset_button.setToolTip("Round 1からやり直します。")
        self.overlay_button = QPushButton("Overlay")
        self.overlay_button.setCheckable(True)
        self.overlay_settings_button = QPushButton("Overlay設定")
        for widget in (self.round_combo, self.previous_button, self.next_button):
            row.addWidget(widget)
        self.auto_checkbox = QCheckBox("自動で次のRoundへ")
        self.auto_checkbox.setToolTip("確定後の待機時間と無音を確認して進みます。Stop後に変更できます。")
        row.addWidget(self.auto_checkbox)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.retry_button)
        row.addWidget(self.reset_button)
        row.addStretch()
        row.addWidget(self.overlay_button)
        row.addWidget(self.overlay_settings_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.state_label, self.confidence_label = QLabel(), QLabel()
        for widget in (self.state_label, self.confidence_label):
            widget.setTextFormat(Qt.TextFormat.PlainText)
        self.state_label.setAccessibleName("Liveの認識状態と照合結果")
        self.confidence_label.setAccessibleName("認識した音の最低複合スコア")
        self.confidence_label.setToolTip("記録した音の複合スコアの最低値です。正解の確率ではありません。")
        row.addWidget(self.state_label, 1)
        row.addWidget(self.confidence_label)
        layout.addLayout(row)
        self.count_label = QLabel()
        self.count_label.setAccessibleName("PASSごとの認識個数")
        layout.addWidget(self.count_label)
        self.final_label = QLabel()
        self.final_label.setTextFormat(Qt.TextFormat.PlainText)
        self.final_label.setWordWrap(True)
        self.final_label.setAccessibleName("Liveの確定順または確認が必要な順序")
        layout.addWidget(self.final_label)
        self.oracle_map = OracleMapWidget(self.labels, positions)
        self.oracle_map.setFixedHeight(180)
        layout.addWidget(self.oracle_map)
        self.pass_table = QTableWidget(2, 7)
        self.pass_table.setHorizontalHeaderLabels([str(i) for i in range(1, 8)])
        self.pass_table.setVerticalHeaderLabels(["PASS 1", "PASS 2"])
        self.pass_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pass_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pass_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pass_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.pass_table.verticalHeader().setDefaultSectionSize(42)
        self.pass_table.setFixedHeight(132)
        self.pass_table.setAccessibleName("Liveの補正前PASS1とPASS2")
        self.pass_table.setStyleSheet("QHeaderView::section { background: #182234; color: #e4ecf8; padding: 3px; } QTableWidget { font-size: 11px; } QTableCornerButton::section { background: #182234; border: 1px solid #38455c; }")
        layout.addWidget(self.pass_table)
        self.reason_label = QLabel()
        self.reason_label.setTextFormat(Qt.TextFormat.PlainText)
        self.reason_label.setWordWrap(True)
        self.reason_label.setAccessibleName("Live認識の確認が必要な理由")
        layout.addWidget(self.reason_label)
        self.round_combo.currentIndexChanged.connect(lambda: self.round_requested.emit(self.round_index))
        self.next_button.clicked.connect(self._next)
        self.reset_button.clicked.connect(lambda: self.round_requested.emit(1))
        self.retry_button.clicked.connect(lambda: self.round_requested.emit(self.round_index))
        self.previous_button.clicked.connect(lambda: self.round_requested.emit(max(1, self.round_index - 1)))
        self.auto_checkbox.toggled.connect(self.auto_advance_requested)
        self.overlay_button.toggled.connect(self.overlay_requested)
        self.overlay_settings_button.clicked.connect(self.overlay_settings_requested)
        self.clear()

    @property
    def round_index(self):
        return self.round_combo.currentData()

    def _next(self):
        if self.round_index < 5:
            self.round_requested.emit(self.round_index + 1)

    def clear(self, round_index=None, message=""):
        engine = SequenceEngine()
        snapshot = engine.arm(0, round_index or self.round_index)
        self.set_result(replace(snapshot, state=SequenceState.IDLE, reason="STOPPED"), message)

    def set_labels(self, labels):
        self.labels = dict(labels)
        self.oracle_map.set_labels(labels)
        if self.result:
            self.set_result(self.result, self.message)

    def set_result(self, snapshot, message=""):
        self.result, self.message = snapshot, message
        view = present(snapshot, message)
        self.round_combo.blockSignals(True)
        self.round_combo.setCurrentIndex(snapshot.round_index - 1)
        self.round_combo.blockSignals(False)
        self.next_button.setEnabled(snapshot.round_index < 5)
        self.previous_button.setEnabled(snapshot.round_index > 1)
        self.state_label.setText(view.state_text)
        self.state_label.setStyleSheet(f"color: {view.color}; font-weight: 600;")
        self.confidence_label.setText(view.confidence_text)
        self.count_label.setText(view.count_text)
        self.final_label.setText(view.sequence_text)
        self.final_label.setStyleSheet(f"color: {view.color}; font-size: 20px; font-weight: 600;")
        self.oracle_map.set_result(view)
        verification = snapshot.verification
        reasons = {
            "AUDIO_GAP": "音声が欠落しました。Roundを選び直して再開してください。",
            "SOURCE_CHANGED": "音声ストリームが変わりました。Roundを選び直して再開してください。",
            "DEVICE_LOST": "音声デバイスとの接続が切れました。",
            "LIVE_ANALYSIS_ERROR": "認識処理に失敗しました。入力とサンプルを確認してResetしてください。",
            "EVENT_TIMEOUT": "次の音を待つ時間を超えました。Roundを選び直してください。",
            "EARLY_PASS_2": "2回目までの間隔が短すぎます。",
            "INCOMPLETE_PASS": "期待個数の前に提示が途切れました。",
            "RECONSTRUCTED": "候補とVoGルールから推定しました。順序を確認してください。",
            "CONFIDENCE_INFERENCE": "信頼度差から推定しました。順序を確認してください。",
            "WEAK_RECONSTRUCTION": "候補の根拠が弱いため確認が必要です。",
            "AMBIGUOUS_RECONSTRUCTION": "補正案が同点または僅差です。",
            "STRONG_PASS_CONFLICT": "信頼度の高い認識が食い違っています。",
            "CORRECTION_LIMIT": "不確かな位置が補正上限を超えています。",
            "UNSAFE_EVENT": "途中切れなど、推定に使えない音があります。",
        }
        reason = message or reasons.get(snapshot.reason, "認識に不一致またはunknownが残っています。")
        if verification and verification.mismatch_indices:
            reason += " 不一致位置：" + ", ".join(map(str, verification.mismatch_indices))
        self.reason_label.setText(reason)
        self.reason_label.setVisible(bool(message) or snapshot.state == SequenceState.UNCERTAIN)
        self.pass_table.clearContents()
        for column in range(7):
            self.pass_table.setColumnHidden(column, column >= snapshot.expected_count)
            for row, entries in enumerate((snapshot.pass1, snapshot.pass2)):
                text, tooltip = "—", ""
                entry = entries[column] if column < len(entries) else None
                if entry:
                    event = entry.detection
                    text = (entry.oracle.value if entry.oracle else "unknown") + "\n" + event.status.value
                    if event.confidence is not None:
                        text += f" {event.confidence:.3f}"
                    ranking = entry.classification.ranking
                    tooltip = f"{entry.timestamp:.3f}〜{entry.end_time:.3f} 秒 / {event.reason}\n"
                    tooltip += "候補：" + " / ".join(f"{item.oracle.value} {item.score:.3f}" for item in ranking)
                    if event.best_candidate:
                        tooltip += "\n" + event.best_candidate.value + " · " + self.labels[event.best_candidate.value]
                item = QTableWidgetItem(text)
                if verification:
                    if column + 1 in verification.mismatch_indices:
                        item.setBackground(QColor("#472936"))
                        tooltip += f"\n不一致位置：{column + 1}"
                    validation = verification.pass1 if row == 0 else verification.pass2
                    duplicate = next((d for d in validation.duplicates if column + 1 in d.indices), None)
                    if duplicate:
                        item.setBackground(QColor("#4c3a20"))
                        tooltip += "\n同一PASS重複：" + duplicate.oracle.value + " / " + ", ".join(map(str, duplicate.indices))
                item.setToolTip(tooltip)
                self.pass_table.setItem(row, column, item)
        return view
