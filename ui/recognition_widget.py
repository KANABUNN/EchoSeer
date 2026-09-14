"""Candidate ranking view; waveform similarity is not a confidence probability."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from config.defaults import DEFAULT_ORACLE_LABELS
from detector.classifier import ClassificationResult
from encounter.vog_oracles import OracleId


class RecognitionWidget(QGroupBox):
    def __init__(self, labels: dict[str, str] | None = None) -> None:
        super().__init__("Oracle 候補 · 波形相関")
        self.labels = labels or DEFAULT_ORACLE_LABELS
        self.result: ClassificationResult | None = None
        layout = QVBoxLayout(self)
        self.best_label = QLabel()
        self.second_label = QLabel()
        self.margin_label = QLabel()
        for label in (self.best_label, self.second_label, self.margin_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        self.table = QTableWidget(7, 4)
        self.table.setHorizontalHeaderLabels(("順位", "Oracle", "波形スコア", "サンプル数"))
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
        self.table.setAccessibleName("Oracle 候補の波形スコア順位")
        layout.addWidget(self.table)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.warning_label = QLabel()
        self.warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)
        self.clear()

    def _name(self, oracle: OracleId) -> str:
        return f"{oracle.value} · {self.labels[oracle.value]}"

    def clear(self) -> None:
        self.result = None
        self.best_label.setText("第1候補：—")
        self.second_label.setText("第2候補：—")
        self.margin_label.setText("スコア差：—")
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
                     str(len(item.samples)) if item else "0")
            for column, text in enumerate(texts):
                self.table.setItem(row, column, QTableWidgetItem(text))
        messages = {
            "RANKED": "暫定候補です。波形スコアは確率ではありません。",
            "TIED": "上位候補が同点またはほぼ同点です。Oracle を確定しません。",
            "SILENT": "無音のため Oracle 候補を出しません。",
            "NO_TEMPLATES": "比較できるサンプルがありません。",
            "NO_MATCH": "波形の一致がありません。Oracle を確定しません。",
            "TOO_SHORT": "音声区間が短すぎます。",
            "TOO_LONG": "1 つの Oracle を含む短い音声区間を選んでください。",
            "LIMIT": "比較対象の音声が多すぎます。",
        }
        aggregation = ("各 Oracle の最高スコア" if result.aggregation == "best"
                       else f"各 Oracle の上位 {result.top_n} 件平均")
        self.status_label.setText(f"{messages[result.status]}\n{aggregation} / {result.sample_count} サンプルを比較")
        notices = list(result.notices)
        if result.ranking and result.missing_oracles:
            notices.append("比較サンプルなし：" + ", ".join(o.value for o in result.missing_oracles))
        self.warning_label.setText("\n".join(notices))
        self.warning_label.setVisible(bool(notices))
