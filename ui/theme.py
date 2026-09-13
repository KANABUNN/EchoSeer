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
