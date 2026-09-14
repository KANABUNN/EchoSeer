"""Dataset evaluation controls and explicit metrics; rates with no denominator stay blank."""
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (
    QAbstractItemView,QGroupBox,QHeaderView,QHBoxLayout,QLabel,QLineEdit,
    QProgressBar,QPushButton,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget,
)
from config.defaults import ORACLE_KEYS


def percent(value):
    return f"{value*100:.2f}%" if value is not None else "—"


class EvaluationPage(QWidget):
    open_requested=Signal()
    evaluate_requested=Signal()
    baseline_requested=Signal()
    export_requested=Signal()
    cancel_requested=Signal()

    def __init__(self):
        super().__init__()
        self.dataset_path=self.baseline_path=self.report=None
        self._busy=False
        layout=QVBoxLayout(self)
        instruction=QLabel("正解を手動で確認したDatasetを一括解析します。比較元レポートを選ぶと同じ音声・正解での差を表示します。")
        instruction.setWordWrap(True); layout.addWidget(instruction)
        self.path_label=QLineEdit(); self.path_label.setReadOnly(True)
        self.path_label.setPlaceholderText("評価するDatasetのJSON")
        layout.addWidget(self.path_label)
        row=QHBoxLayout()
        self.open_button=QPushButton("Datasetを開く")
        self.evaluate_button=QPushButton("一括評価")
        self.baseline_button=QPushButton("比較元を選ぶ")
        self.cancel_button=QPushButton("処理を中止")
        self.export_button=QPushButton("レポート・CSV保存")
        for button in (self.open_button,self.evaluate_button,self.baseline_button,self.cancel_button,self.export_button): row.addWidget(button)
        layout.addLayout(row)
        baseline_row=QHBoxLayout()
        self.baseline_label=QLabel("比較元：なし"); self.baseline_label.setWordWrap(True)
        self.clear_baseline_button=QPushButton("比較を解除")
        self.clear_baseline_button.clicked.connect(lambda:self.set_baseline(None))
        baseline_row.addWidget(self.baseline_label,1); baseline_row.addWidget(self.clear_baseline_button)
        layout.addLayout(baseline_row)
        self.progress=QProgressBar(); self.progress.setRange(0,100); layout.addWidget(self.progress)
        self.message_label=QLabel("正解率はunknown・欠落を含みます。棄却率だけで認識性能を判断しないでください。")
        self.message_label.setTextFormat(Qt.TextFormat.PlainText); self.message_label.setWordWrap(True); layout.addWidget(self.message_label)
        self.metrics_label=QLabel(); self.metrics_label.setTextFormat(Qt.TextFormat.PlainText)
        self.metrics_label.setWordWrap(True); layout.addWidget(self.metrics_label)
        self.table=QTableWidget(7,5)
        self.table.setHorizontalHeaderLabels(["Oracle","正解数 / 正解音数","正解率","unknown","欠落"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setFixedHeight(275); layout.addWidget(self.table)
        self.conditions_label=QLabel(); self.conditions_label.setWordWrap(True)
        self.conditions_label.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.conditions_label)
        note=QLabel("誤検出率はOracleなしと明示したケースが分母です。時刻付きの全Oracleを注釈した録音では誤検出数/分も表示します。時刻なしの順序データは背景音の時刻別誤検出率を算出しません。")
        note.setWordWrap(True); layout.addWidget(note); layout.addStretch()
        self.open_button.clicked.connect(self.open_requested)
        self.evaluate_button.clicked.connect(self.evaluate_requested)
        self.baseline_button.clicked.connect(self.baseline_requested)
        self.export_button.clicked.connect(self.export_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self._controls()

    def set_dataset(self,path):
        self.dataset_path=path; self.path_label.setText(str(path)); self.begin(); self._controls()

    def set_baseline(self,path):
        self.baseline_path=path
        self.baseline_label.setText(f"比較元：{path}" if path else "比較元：なし")
        self._controls()

    def begin(self):
        self.report=None; self.metrics_label.clear(); self.conditions_label.clear()
        self.table.clearContents(); self.progress.setValue(0)
        self._controls()

    def set_busy(self,busy):
        self._busy=busy; self._controls()

    def set_progress(self,value,message):
        self.progress.setValue(value); self.message_label.setText(f"評価中：{message}")

    def set_report(self,report):
        self.report=report
        m=report["metrics"]
        lines=[f'正解 {m["correct"]}/{m["total_events"]} · 誤認識 {m["incorrect"]} · Oracle音のunknown {m["rejected"]} · 欠落 {m["missed"]}',
               f'正解率 {percent(m["accuracy"])} · 採用した音の正解率 {percent(m["precision"])} · 棄却率 {percent(m["rejection_rate"])}',
               f'余分なunknown {m["extra_unknown"]} · 余分な採用 {m["false_positives"]} · Oracleなしケースの誤検出率 {percent(m["false_positive_rate"])}']
        if m["false_positives_per_minute"] is not None:
            lines.append(f'時刻付き録音の誤検出 {m["false_positives_per_minute"]:.3f}/分')
        if m.get("final_sequence_accuracy") is not None:
            lines.append(f'Round確定順の正解率 {percent(m["final_sequence_accuracy"])}')
        if report.get("comparison"):
            d=report["comparison"]["deltas"]
            lines.append("比較元との差："+ " · ".join(f'{name} {d[key]*100:+.2f}pt' for key,name in (("accuracy","正解率"),("precision","採用の正解率"),("rejection_rate","棄却率")) if d[key] is not None))
        self.metrics_label.setText("\n".join(lines))
        for row,oracle in enumerate(ORACLE_KEYS):
            p=report["per_oracle"][oracle]
            for col,text in enumerate((oracle,f'{p["correct"]}/{p["total"]}',percent(p["accuracy"]),str(p["rejected"]),str(p["missed"]))):
                self.table.setItem(row,col,QTableWidgetItem(text))
        self.conditions_label.setText(f'Dataset {report["dataset_fingerprint"][:12]} · 条件 {report["profile_hash"][:12]}')
        self.conditions_label.setToolTip(str(report["profile"]))
        self.message_label.setText(f'{len(report["cases"])}ケースの評価が終わりました。')
        self.progress.setValue(100); self._controls()

    def _controls(self):
        self.open_button.setEnabled(not self._busy); self.baseline_button.setEnabled(not self._busy)
        self.evaluate_button.setEnabled(self.dataset_path is not None and not self._busy)
        self.export_button.setEnabled(self.report is not None and not self._busy)
        self.cancel_button.setEnabled(self._busy)
        self.clear_baseline_button.setEnabled(self.baseline_path is not None and not self._busy)
