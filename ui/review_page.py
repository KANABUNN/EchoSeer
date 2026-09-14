"""Local saved log inspection and a path into manual replay labelling and evaluation."""
from datetime import datetime
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (
    QAbstractItemView,QCheckBox,QHeaderView,QHBoxLayout,QLabel,QPushButton,
    QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget,
)


class ReviewPage(QWidget):
    open_requested=Signal()
    replay_requested=Signal()
    evaluate_requested=Signal()
    cancel_requested=Signal()
    success_audio_changed=Signal(bool)

    def __init__(self):
        super().__init__()
        self.catalog=None;self._busy=False
        layout=QVBoxLayout(self)
        note=QLabel("保存ログの音声をReplayへ送り、元の候補・理由を確認します。正解は手動で確認して付け、認識結果を正解として転記しません。")
        note.setWordWrap(True);layout.addWidget(note)
        self.success_checkbox=QCheckBox("採用した音声も短く保存（成功候補・初期OFF）")
        self.success_checkbox.setToolTip("HIGH/MEDIUMの候補も最大3秒で保存します。正解の判定は手動です。")
        self.success_checkbox.toggled.connect(self.success_audio_changed);layout.addWidget(self.success_checkbox)
        row=QHBoxLayout()
        self.open_button=QPushButton("ログフォルダーを読む")
        self.replay_button=QPushButton("音声をReplayへ")
        self.evaluate_button=QPushButton("手動記録を一括評価")
        self.cancel_button=QPushButton("処理を中止")
        for b in (self.open_button,self.replay_button,self.evaluate_button,self.cancel_button):row.addWidget(b)
        layout.addLayout(row)
        self.path_label=QLabel();self.path_label.setWordWrap(True);layout.addWidget(self.path_label)
        self.table=QTableWidget(0,7)
        self.table.setHorizontalHeaderLabels(["日時","Round / PASS","認識","第1候補","信頼度","理由","音声"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(260);layout.addWidget(self.table)
        self.message_label=QLabel("実戦で採用された音声を記録する場合は上の保存設定をONにします。")
        self.message_label.setTextFormat(Qt.TextFormat.PlainText);self.message_label.setWordWrap(True);layout.addWidget(self.message_label)
        layout.addStretch()
        self.table.currentCellChanged.connect(lambda *unused:self._controls())
        self.open_button.clicked.connect(self.open_requested);self.replay_button.clicked.connect(self.replay_requested)
        self.evaluate_button.clicked.connect(self.evaluate_requested);self.cancel_button.clicked.connect(self.cancel_requested)
        self._controls()

    @property
    def selected_event(self):
        row=self.table.currentRow()
        if self.catalog and 0<=row<len(self.catalog.events):return self.catalog.events[row]
        return None

    def set_catalog(self,catalog):
        self.catalog=catalog;self.path_label.setText(str(catalog.root))
        self.table.setRowCount(len(catalog.events))
        for row,event in enumerate(catalog.events):
            p=event.payload
            try:date=datetime.fromtimestamp(p.get("timestamp",0)).strftime("%m/%d %H:%M:%S")
            except (OSError,OverflowError,ValueError,TypeError):date="—"
            texts=(date,f'{p.get("round") or "—"} / {p.get("pass") or "—"}',
                   p.get("oracle") or "unknown",p.get("best_candidate") or "—",
                   p.get("confidence_level") or "—",p.get("problem_reason") or p.get("reason") or "—",
                   "あり" if event.audio_relative else "なし")
            for col,value in enumerate(texts):
                item=QTableWidgetItem(str(value)[:100]);item.setToolTip(event.payload_json[:2000]);self.table.setItem(row,col,item)
        if catalog.events:self.table.selectRow(0)
        self.message_label.setText("\n".join(catalog.notices) or f"{len(catalog.events)}件を読み込みました。")
        self._controls()

    def set_busy(self,busy):
        self._busy=busy;self._controls()

    def _controls(self):
        event=self.selected_event
        self.open_button.setEnabled(not self._busy)
        self.replay_button.setEnabled(not self._busy and event is not None and event.audio_relative is not None)
        self.evaluate_button.setEnabled(not self._busy)
        self.cancel_button.setEnabled(self._busy)
        self.table.setEnabled(not self._busy)
