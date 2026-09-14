"""Replay: shared audio conversion, Oracle waveform/spectrum ranking and explicit export."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from audio.sources import AudioSource, ClipSource, WaveFileSource
from replay.analyzer import AnalysisResult
from ui.recognition_widget import RecognitionWidget
from ui.sequence_widget import SequenceWidget
from ui.timeline_widget import TimelineWidget
from ui.review_widget import ReviewWidget


class ReplayPage(QWidget):
    open_requested = Signal()
    analyze_requested = Signal()
    sequence_requested = Signal()
    save_requested = Signal()

    def __init__(self, parent: QWidget | None = None, labels: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(540)
        self.source: AudioSource | None = None
        self.result: AnalysisResult | None = None
        self._checksum: str | None = None
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(18)
        source_group = QGroupBox("解析する音声")
        source_layout = QVBoxLayout(source_group)
        self.path_label = QLineEdit()
        self.path_label.setReadOnly(True)
        self.path_label.setPlaceholderText("WAV を選ぶか、Live の直近音声を送ってください")
        self.path_label.setAccessibleName("解析対象の音声")
        row = QHBoxLayout()
        self.open_button = QPushButton("WAV を開く")
        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.setObjectName("primaryButton")
        row.addWidget(self.path_label, 1)
        row.addWidget(self.open_button)
        row.addWidget(self.analyze_button)
        source_layout.addLayout(row)
        self.message_label = QLabel("1 音の比較は Analyze、2 回提示を含む音声は「順序を解析」で確認できます。")
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName("audioMessage")
        source_layout.addWidget(self.message_label)
        layout.addWidget(source_group)

        self.timeline = TimelineWidget()
        layout.addWidget(self.timeline)
        self.recognition = RecognitionWidget(labels)
        layout.addWidget(self.recognition)
        self.review_toggle = QPushButton("誤認識・確認用の音声を保存")
        self.review_toggle.setCheckable(True)
        layout.addWidget(self.review_toggle)
        self.review_widget=ReviewWidget()
        self.review_widget.hide()
        self.review_toggle.toggled.connect(self.review_widget.setVisible)
        layout.addWidget(self.review_widget)
        self.sequence = SequenceWidget(labels)
        self.sequence.analyze_requested.connect(self.sequence_requested.emit)
        layout.addWidget(self.sequence)
        summary = QGroupBox("音声形式")
        grid = QGridLayout(summary)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(12)
        grid.addWidget(QLabel("入力音声"), 0, 1)
        grid.addWidget(QLabel("解析後"), 0, 2)
        self.values: dict[str, tuple[QLabel, QLabel]] = {}
        for index, (key, title) in enumerate((
            ("rate", "Sample Rate"), ("channels", "Channels"),
            ("frames", "サンプル数"), ("duration", "長さ"),
            ("rms", "RMS"), ("peak", "Peak"),
        ), 1):
            grid.addWidget(QLabel(title), index, 0)
            labels = (QLabel("—"), QLabel("—"))
            for column, label in enumerate(labels, 1):
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(label, index, column)
            self.values[key] = labels
        layout.addWidget(summary)
        self.notice_label = QLabel("")
        self.notice_label.setWordWrap(True)
        self.notice_label.setObjectName("audioMessage")
        layout.addWidget(self.notice_label)

        export_group = QGroupBox("解析後の音声を保存")
        export_layout = QHBoxLayout(export_group)
        self.encoding_combo = QComboBox()
        self.encoding_combo.addItem("WAV / float32（精度を保持）", "float32")
        self.encoding_combo.addItem("WAV / PCM 16 bit（一般的な再生用）", "pcm16")
        self.save_button = QPushButton("WAV 保存")
        export_layout.addWidget(self.encoding_combo, 1)
        export_layout.addWidget(self.save_button)
        layout.addWidget(export_group)
        note = QLabel("結果が unknown の音声は Oracle として確定しません。")
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        self.open_button.clicked.connect(self.open_requested)
        self.analyze_button.clicked.connect(self.analyze_requested)
        self.save_button.clicked.connect(self.save_requested)
        self._update_controls()

    def set_wave_path(self, path) -> None:
        self.source = WaveFileSource(path)
        self.path_label.setText(str(path))
        self.path_label.setToolTip(str(path))
        self._clear_result()
        self.message_label.setText("1 音は Analyze、1 ラウンド全体は Round を選んで「順序を解析」を押します。")
        self._update_controls()

    def _clear_result(self) -> None:
        self.result = None
        self._checksum = None
        self.recognition.clear()
        self.recognition.setTitle("Oracle 候補 · 複合スコア")
        self.sequence.clear()
        self.timeline.clear()
        self.review_widget.set_available(False)
        self.review_widget.set_context({})
        self.notice_label.clear()
        for labels in self.values.values():
            for label in labels:
                label.setText("—")

    def begin_analysis(self) -> None:
        self.review_widget.set_available(False)
        self.timeline.clear()
        self.sequence.clear()
        self.recognition.clear()
        self.recognition.setTitle("Oracle 候補 · 複合スコア")
        # Keep only the checksum for comparison; a failed read must not export old audio.
        self.result = None
        self.notice_label.clear()
        for labels in self.values.values():
            for label in labels:
                label.setText("—")
        self._update_controls()

    def set_result(self, result: AnalysisResult, live: bool = False) -> None:
        self.recognition.clear()
        self.recognition.setTitle("Oracle 候補 · 複合スコア")
        if live:
            self.source = ClipSource(result.original)
            self.path_label.setText("Live の直近音声（解析開始時のコピー）")
            self.path_label.setToolTip("取得バッファのコピーです。Analyze でこの同じ音声を再解析できます。")
            self._checksum = None
        same = self._checksum == result.checksum
        previous = self._checksum is not None
        self.result, self._checksum = result, result.checksum
        original, processed = result.original, result.processed
        details = {
            "rate": (f"{original.sample_rate:,} Hz", f"{processed.sample_rate:,} Hz"),
            "channels": (f"{original.channels} ch", "1 ch"),
            "frames": (f"{original.frame_count:,}", f"{processed.frame_count:,}"),
            "duration": (f"{original.duration_seconds:.3f} 秒", f"{processed.duration_seconds:.3f} 秒"),
            "rms": (self._db(result.original_level.rms, result.original_level.rms_dbfs),
                    self._db(result.processed_level.rms, result.processed_level.rms_dbfs)),
            "peak": (self._db(result.original_level.peak, result.original_level.peak_dbfs),
                     self._db(result.processed_level.peak, result.processed_level.peak_dbfs)),
        }
        for key, texts in details.items():
            for label, value in zip(self.values[key], texts, strict=True):
                label.setText(value)
        if same:
            self.message_label.setText("前回と同じサンプル列です。")
        elif previous:
            self.message_label.setText("解析できました。前回と音声の内容が変わっています。")
        else:
            self.message_label.setText("解析できました。")
        self.notice_label.setText("\n".join(result.notices))
        self._update_controls()

    @staticmethod
    def _db(value: float, dbfs: float) -> str:
        return f"{dbfs:.1f} dBFS" if value > 0 else "−∞ dBFS"

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.message_label.setText("音声を処理しています…")
        self._update_controls()

    def show_error(self, message: str) -> None:
        self.message_label.setText(message)

    def _update_controls(self) -> None:
        self.review_widget.set_available(self.result is not None and not self._busy)
        self.timeline.set_available(self.source is not None)
        self.timeline.set_busy(self._busy)
        self.sequence.set_available(self.source is not None)
        self.sequence.set_busy(self._busy)
        self.open_button.setEnabled(not self._busy)
        self.analyze_button.setEnabled(not self._busy and self.source is not None)
        self.save_button.setEnabled(not self._busy and self.result is not None)
        self.encoding_combo.setEnabled(not self._busy and self.result is not None)
