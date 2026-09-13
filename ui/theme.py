"""Dark, high-contrast theme for the initial window."""

DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #111827;
    color: #f3f4f6;
    font-family: "Yu Gothic UI", "Segoe UI", sans-serif;
    font-size: 14px;
}
QLabel#title { font-size: 30px; font-weight: 700; }
QLabel#subtitle { color: #b9c4d5; font-size: 16px; }
QFrame#workspace {
    background-color: #182234;
    border: 1px solid #38455c;
    border-radius: 12px;
}
QLabel#placeholder { color: #c4cfdf; font-size: 18px; }
QLabel#warning {
    background-color: #3c3017;
    color: #ffe2a3;
    border: 1px solid #d7a94b;
    border-radius: 6px;
    padding: 12px;
}
QStatusBar { background-color: #0b1220; color: #c4cfdf; }
QMenuBar, QMenu { background-color: #182234; }
QMenu::item:selected { background-color: #334967; }
"""

DARK_STYLE += """
QTabWidget::pane { border: 1px solid #38455c; border-radius: 8px; }
QTabBar::tab { background: #182234; padding: 8px 22px; }
QTabBar::tab:selected { color: #92e2ff; border-bottom: 2px solid #76d7ff; }
QGroupBox { border: 1px solid #38455c; border-radius: 8px; margin-top: 14px; padding: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #e4ecf8; }
QComboBox { background: #182234; border: 1px solid #55647b; border-radius: 5px; padding: 8px; }
QComboBox:focus { border: 2px solid #92e2ff; }
QComboBox QAbstractItemView { background: #182234; selection-background-color: #334967; }
QPushButton { background: #29374d; border: 1px solid #60718c; border-radius: 5px; padding: 8px 18px; }
QPushButton:hover { background: #354967; }
QPushButton:focus { border: 2px solid #f3f4f6; }
QPushButton:disabled, QComboBox:disabled { color: #a0abbd; border-color: #38455c; background: #182234; }
QPushButton#primaryButton:enabled { background: #76d7ff; color: #092033; border-color: #92e2ff; font-weight: 700; }
QLabel#deviceInfo { color: #b9c4d5; }
QLabel#levelValue { font-size: 22px; font-weight: 600; }
QLabel#captureStatus { color: #e4ecf8; font-weight: 700; padding: 6px 12px; border: 1px solid #60718c; border-radius: 5px; }
QLabel#captureStatus[state="LIVE"] { color: #b9eeff; border-color: #76d7ff; }
QLabel#captureStatus[state="DEVICE_LOST"], QLabel#captureStatus[state="ERROR"] { color: #ffe2a3; border-color: #d7a94b; }
QLabel#audioMessage { color: #c4cfdf; }
QProgressBar { background: #182234; border: 1px solid #55647b; border-radius: 5px; }
QProgressBar::chunk { background: #76d7ff; border-radius: 4px; }
"""
