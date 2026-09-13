"""CLI options, configuration, diagnostics, and the Qt event loop."""

import argparse
import logging
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app import __version__
from app.paths import AppPaths
from config.manager import ConfigManager
from logging_ext.session_logger import close_logging, configure_logging
from ui.main_window import MainWindow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Destiny 2 VoG Oracle Assistant")
    parser.add_argument("--version", action="version", version=f"Oracle Assistant {__version__}")
    parser.add_argument("--debug", action="store_true", help="詳細な診断ログを表示")
    parser.add_argument("--data-dir", type=Path, help="ユーザーデータの保存先を指定")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    qt_app = QApplication([sys.argv[0]])
    qt_app.setApplicationName("OracleAssistant")
    qt_app.setOrganizationName("OracleAssistant")
    qt_app.setApplicationVersion(__version__)
    paths = AppPaths.discover(args.data_dir)
    manager = ConfigManager(paths.config)
    settings = manager.load()
    warnings = [manager.last_warning] if manager.last_warning else []
    try:
        paths.ensure_directories()
    except OSError:
        warnings.append("データ保存先を作成できません。アクセス権と空き容量を確認してください。")
    try:
        logger = configure_logging(paths.logs, debug=args.debug or settings.logging.debug)
    except OSError:
        logger = logging.getLogger("oracle_assistant")
        logger.addHandler(logging.NullHandler())
        warnings.append("診断ログを保存できません。保存先を確認してください。")
    logger.info("Starting Oracle Assistant %s; Python %s", __version__, sys.version.split()[0])
    if manager.last_warning:
        logger.warning("%s; backup=%s", manager.last_warning, manager.last_backup)
    window = MainWindow(
        paths.root, "\n".join(warnings) if warnings else None,
        settings=settings, config_manager=manager,
    )
    window.show()
    if args.smoke_test:
        QTimer.singleShot(750, window.close)
    try:
        return qt_app.exec()
    finally:
        if not window.controller.shutdown(timeout=5):
            logger.error("Audio worker did not shut down")
        if not window.operations.shutdown(timeout=5):
            logger.error("Replay worker did not shut down")
        logger.info("Oracle Assistant stopped")
        close_logging()
