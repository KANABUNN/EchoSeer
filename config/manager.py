"""Atomic settings writes and recovery without discarding damaged originals."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from config.schema import AppConfig, ConfigValidationError

logger = logging.getLogger("oracle_assistant.config")


def _reject_constant(value: str) -> None:
    raise ConfigValidationError(f"non-finite JSON value: {value}")


class ConfigManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_warning: str | None = None
        self.last_backup: Path | None = None

    def load(self) -> AppConfig:
        self.last_warning = None
        self.last_backup = None
        defaults = AppConfig()
        try:
            raw = self.path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            self._save_defaults(defaults)
            return defaults
        except OSError as error:
            self.last_warning = "設定ファイルを読めません。初期設定で起動しました。"
            logger.warning("Cannot read settings: %s", error)
            return defaults
        except UnicodeError as error:
            self._recover(defaults, error)
            return defaults
        try:
            data = json.loads(raw, parse_constant=_reject_constant)
            return AppConfig.from_dict(data)
        except (ValueError, RecursionError) as error:
            self._recover(defaults, error)
            return defaults

    def _save_defaults(self, defaults: AppConfig) -> None:
        try:
            self.save(defaults)
        except OSError as error:
            self.last_warning = "設定を保存できません。保存先のアクセス権と空き容量を確認してください。"
            logger.warning("Cannot save initial settings: %s", error)

    def _recover(self, defaults: AppConfig, error: Exception) -> None:
        self.last_warning = "設定ファイルが不正です。初期設定で起動しました。"
        logger.warning("Invalid settings: %s", error)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.path.with_name(f"config.corrupt-{stamp}-{uuid4().hex[:8]}.json")
        try:
            shutil.copy2(self.path, backup)
        except OSError as backup_error:
            self.last_warning += " 退避に失敗したため、元の設定ファイルを保持しています。"
            logger.warning("Cannot back up settings; original preserved: %s", backup_error)
            return
        self.last_backup = backup
        try:
            self.save(defaults)
        except OSError as save_error:
            self.last_warning += " 初期設定の保存に失敗しました。"
            logger.warning("Cannot replace invalid settings: %s", save_error)

    def save(self, config: AppConfig) -> None:
        serialized = json.dumps(config.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=self.path.parent,
                prefix=".config-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(serialized + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
