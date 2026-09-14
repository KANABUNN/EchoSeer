"""Candidate ranking view; component and combined similarities are not confidence probabilities."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGroupBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout,
)
from config.defaults import DEFAULT_ORACLE_LABELS
from detector.classifier import ClassificationResult
from detector.confidence import DetectionResult
from encounter.vog_oracles import OracleId


class RecognitionWidget(QGroupBox):
    event_logging_changed = Signal(bool)
    uncertain_audio_changed = Signal(bool)
    def __init__(self, labels: dict[str, str] | None = None) -> None:
        super().__init__("Oracle 候補 · 複合スコア")
        self.labels = labels or DEFAULT_ORACLE_LABELS
        self.result: ClassificationResult | None = None
        self.detection: DetectionResult | None = None
        layout = QVBoxLayout(self)
        self.decision_label = QLabel()
        self.decision_label.setTextFormat(Qt.TextFormat.PlainText)
        self.decision_label.setWordWrap(True)
        self.decision_label.setAccessibleName("認識結果と信頼度")
        self.decision_label.setStyleSheet("font-weight: 600; padding: 6px; border: 1px solid #55647b; border-radius: 4px;")
        layout.addWidget(self.decision_label)
        self.best_label = QLabel()
        self.second_label = QLabel()
        self.margin_label = QLabel()
        for label in (self.best_label, self.second_label, self.margin_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        self.details_checkbox = QCheckBox("スコア内訳を表示")
        layout.addWidget(self.details_checkbox)
        self.weights_label = QLabel()
        self.weights_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.weights_label)
        self.table = QTableWidget(7, 6)
        self.table.setHorizontalHeaderLabels(("順位", "Oracle", "複合スコア", "サンプル数", "波形", "スペクトル"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setFixedHeight(260)
        self.table.setStyleSheet("""
            QTableWidget { background: #111827; border: 1px solid #55647b; gridline-color: #29374d; }
            QHeaderView::section { background: #182234; color: #e4ecf8; border: 0; padding: 5px; }
        """)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setAccessibleName("Oracle 候補の複合スコア順位と内訳")
        self.details_checkbox.toggled.connect(self._show_details)
        self._show_details(False)
        layout.addWidget(self.table)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.warning_label = QLabel()
        self.warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)
        logging_row = QHBoxLayout()
        self.event_logs_checkbox = QCheckBox("認識ログを保存")
        self.uncertain_audio_checkbox = QCheckBox("不確かな音声を保存")
        self.event_logs_checkbox.setChecked(True)
        self.uncertain_audio_checkbox.setChecked(True)
        self.event_logs_checkbox.toggled.connect(self.event_logging_changed.emit)
        self.uncertain_audio_checkbox.toggled.connect(self.uncertain_audio_changed.emit)
        logging_row.addWidget(self.event_logs_checkbox)
        logging_row.addWidget(self.uncertain_audio_checkbox)
        logging_row.addStretch()
        layout.addLayout(logging_row)
        self.clear()

    def _show_details(self, enabled: bool) -> None:
        self.table.setColumnHidden(4, not enabled)
        self.table.setColumnHidden(5, not enabled)

    def _name(self, oracle: OracleId) -> str:
        return f"{oracle.value} · {self.labels[oracle.value]}"

    def clear(self) -> None:
        self.result = None
        self.detection = None
        self.decision_label.setText("認識結果：— / 信頼度：—")
        self.best_label.setText("第1候補：—")
        self.second_label.setText("第2候補：—")
        self.margin_label.setText("スコア差：—")
        self.weights_label.setText("波形とスペクトルを比較します。")
        self.table.clearContents()
        for row, oracle in enumerate(OracleId):
            self.table.setItem(row, 1, QTableWidgetItem(self._name(oracle)))
        self.status_label.setText("Analyze で登録サンプルと比較します。")
        self.warning_label.clear()
        self.warning_label.hide()

    def set_result(self, result: ClassificationResult | None) -> None:
        self.clear()
        self.result = result
        if result is None:
            return
        self.weights_label.setText(f"複合 = 波形 × {result.waveform_weight:.4f} + スペクトル × {result.spectrum_weight:.4f}")
        if result.best_candidate is not None:
            self.best_label.setText(f"第1候補：{self._name(result.best_candidate)} / {result.best_score:.6f}")
        if result.second_candidate is not None:
            self.second_label.setText(f"第2候補：{self._name(result.second_candidate)} / {result.second_score:.6f}")
        if result.margin is not None:
            self.margin_label.setText(f"スコア差：{result.margin:.6f}")
        rows = [(item.oracle, item) for item in result.ranking]
        rows.extend((oracle, None) for oracle in result.missing_oracles)
        for row, (oracle, item) in enumerate(rows):
            texts = (str(row + 1) if item else "—", self._name(oracle),
                     f"{item.score:.6f}" if item else "未比較",
                     str(len(item.samples)) if item else "0",
                     f"{item.waveform_score:.6f}" if item else "未比較",
                     f"{item.spectrum_score:.6f}" if item else "未比較")
            for column, text in enumerate(texts):
                self.table.setItem(row, column, QTableWidgetItem(text))
        messages = {
            "RANKED": "暫定候補です。類似度スコアは確率ではありません。",
            "TIED": "上位候補が同点またはほぼ同点です。Oracle を確定しません。",
            "SILENT": "無音のため Oracle 候補を出しません。",
            "NO_TEMPLATES": "比較できるサンプルがありません。",
            "NO_MATCH": "特徴の一致がありません。Oracle を確定しません。",
            "TOO_SHORT": "音声区間が短すぎます。",
            "TOO_LONG": "1 つの Oracle を含む短い音声区間を選んでください。",
            "LIMIT": "比較対象の音声が多すぎます。",
        }
        aggregation = ("各 Oracle の最高複合スコア" if result.aggregation == "best"
                       else f"各 Oracle の上位 {result.top_n} 件平均")
        self.status_label.setText(f"{messages[result.status]}\n{aggregation} / {result.sample_count} サンプルを比較")
        notices = list(result.notices)
        if result.ranking and result.missing_oracles:
            notices.append("比較サンプルなし：" + ", ".join(o.value for o in result.missing_oracles))
        self.warning_label.setText("\n".join(notices))
        self.warning_label.setVisible(bool(notices))

    def set_detection(self, detection: DetectionResult | None) -> None:
        self.detection = detection
        if detection is None:
            self.decision_label.setText("認識結果：— / 信頼度：—")
            return
        name = self._name(detection.oracle) if detection.oracle else "unknown"
        suffix = " / 重複を除外" if detection.duplicate else ""
        self.decision_label.setText(f"認識結果：{name} / 信頼度：{detection.status.value}{suffix}")
        reasons = {
            "ACCEPTED": "スコアと候補間の差が基準を満たしています。類似度は確率ではありません。",
            "BELOW_LOW_THRESHOLD": "一致スコアが低いため Oracle を確定しません。",
            "BELOW_CONFIDENCE_THRESHOLD": "信頼度の基準に届かないため Oracle を確定しません。",
            "AMBIGUOUS_MARGIN": "候補間の差が小さいため Oracle を確定しません。",
            "INCOMPLETE_BANK": "7 種すべての比較サンプルが揃うまで Oracle を確定しません。",
            "DUPLICATE": "同じ Oracle が短時間内に再検出されました。重複候補を除外しました。",
            "STALE_EVENT": "取得時刻が過去のイベントのため Oracle を確定しません。",
            "INVALID_RANKING": "スコアが不正なため Oracle を確定しません。",
        }
        reason = reasons.get(detection.reason)
        if reason:
            self.status_label.setText(reason + "\n" + self.status_label.text().split("\n")[-1])

    def append_notices(self, notices: tuple[str, ...]) -> None:
        messages = [self.warning_label.text()] if self.warning_label.text() else []
        self.warning_label.setText("\n".join([*messages, *notices]))
        self.warning_label.show()
