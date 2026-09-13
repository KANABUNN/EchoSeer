"""Phase 0 window shell; capture and recognition arrive in later phases."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow, QVBoxLayout, QWidget

from ui.theme import DARK_STYLE


class MainWindow(QMainWindow):
    def __init__(self, data_root: Path, warning: str | None = None) -> None:
        super().__init__()
        self.data_root = data_root
        self.setWindowTitle("Destiny 2 · Oracle Assistant")
        self.resize(960, 640)
        self.setMinimumSize(640, 420)
        self.setStyleSheet(DARK_STYLE)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        title = QLabel("Oracle Assistant")
        title.setObjectName("title")
        layout.addWidget(title)
        subtitle = QLabel("Destiny 2 · Vault of Glass")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        if warning:
            warning_label = QLabel(warning)
            warning_label.setObjectName("warning")
            warning_label.setWordWrap(True)
            warning_label.setAccessibleName("設定または保存先の警告")
            layout.addWidget(warning_label)
        workspace = QFrame()
        workspace.setObjectName("workspace")
        workspace_layout = QVBoxLayout(workspace)
        placeholder = QLabel("認識機能は準備中です。")
        placeholder.setObjectName("placeholder")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        workspace_layout.addWidget(placeholder)
        layout.addWidget(workspace, stretch=1)
        self.setCentralWidget(central)
        exit_action = QAction("終了", self)
        exit_action.triggered.connect(self.close)
        self.menuBar().addMenu("ファイル").addAction(exit_action)
        self.statusBar().showMessage("STOPPED")
