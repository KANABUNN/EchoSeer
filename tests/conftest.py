"""Keep test artifacts in a dedicated, ignored workspace directory."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp is None:
        config.option.basetemp = str(ROOT / ".runtime" / "pytest-tmp")
    temporary = Path(config.option.basetemp).resolve()
    if temporary.is_relative_to(ROOT):
        temporary.parent.mkdir(parents=True, exist_ok=True)
