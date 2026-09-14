"""Selectable timestamps, raw decisions and component scores for finite WAV debugging."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHeaderView, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


def timestamp(seconds):
    minutes, remainder = divmod(max(0, seconds), 60)
    return f"{int(minutes):02d}:{remainder:05.2f}"


class TimelineWidget(QGroupBox):
    analyze_requested = Signal()
    selection_changed = Signal(int)
    listen_requested = Signal()
    stop_requested = Signal()

    def __init__(self):
        super().__init__("Timeline · 各音を選んで確認")
        self.result, self._busy, self._available = None, False, False
        self._playing = False
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.analyze_button = QPushButton("Timelineを解析")
        self.listen_button = QPushButton("選択音をListen")
        self.stop_button = QPushButton("再生停止")
        for button in (self.analyze_button, self.listen_button, self.stop_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["時刻", "長さ", "認識", "信頼度", "複合", "波形", "スペクトル", "判定理由"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(170)
        self.table.setMaximumHeight(280)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setAccessibleName("時刻付き認識一覧。行を選ぶと全Oracleのスコア内訳を表示")
        layout.addWidget(self.table)
        self.conditions_label = QLabel()
        self.conditions_label.setTextFormat(Qt.TextFormat.PlainText)
        self.conditions_label.setWordWrap(True)
        layout.addWidget(self.conditions_label)
        self.analyze_button.clicked.connect(self.analyze_requested)
        self.listen_button.clicked.connect(self.listen_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.table.currentCellChanged.connect(lambda row, *unused: self.selection_changed.emit(row))
        self.clear()

    @property
    def selected_trace(self):
        row = self.table.currentRow()
        if self.result and 0 <= row < len(self.result.traces):
            return self.result.traces[row]
        return None

    def clear(self):
        self.result = None
        self.table.setRowCount(0)
        self.conditions_label.setText("全区間の音を解析します。不確かな音も元の候補を残します。")
        self.conditions_label.setToolTip("")
        self._controls()

    def set_result(self, result, sample_rate, select_last=False):
        self.result = result
        self.table.blockSignals(True)
        self.table.setRowCount(len(result.traces))
        for row, trace in enumerate(result.traces):
            d = trace.detection
            def score(value):
                return f"{value:.6f}" if value is not None else "—"
            texts = (timestamp(trace.onset_frame / sample_rate),
                     f"{(trace.signal_end_frame-trace.onset_frame)/sample_rate:.3f}秒",
                     d.oracle.value if d.oracle else "unknown", d.status.value,
                     score(d.confidence), score(d.waveform_score), score(d.spectrum_score), d.reason)
            for column, value in enumerate(texts):
                item = QTableWidgetItem(value)
                item.setToolTip(f"native frames {trace.start_frame}–{trace.end_frame}\n第1候補: {d.best_candidate.value if d.best_candidate else '—'}")
                self.table.setItem(row, column, item)
        if result.traces:
            self.table.selectRow(len(result.traces)-1 if select_last else 0)
        self.table.blockSignals(False)
        self.conditions_label.setText(f"{len(result.traces)}音 · 条件 {result.profile_hash[:12]} · スコアは類似度")
        self.conditions_label.setToolTip(result.profile_json)
        self._controls()

    def set_available(self, available):
        self._available = available
        self._controls()

    def set_playing(self, playing):
        self._playing=playing
        self._controls()

    def set_busy(self, busy):
        self._busy = busy
        self._controls()

    def _controls(self):
        self.analyze_button.setEnabled(self._available and not self._busy)
        self.listen_button.setEnabled(self.selected_trace is not None and not self._busy)
        self.stop_button.setEnabled(self._playing)
        self.table.setEnabled(not self._busy)
