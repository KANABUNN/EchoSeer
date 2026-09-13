"""Bounded application diagnostics; does not record session audio."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "oracle_assistant"
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def close_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def configure_logging(log_directory: Path, debug: bool = False) -> logging.Logger:
    log_directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_directory / "application.log", maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    close_logging()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    if debug:
        console = logging.StreamHandler()
        console.setFormatter(handler.formatter)
        logger.addHandler(console)
    return logger
