"""Провайдеры повторной авторизации для фонового сервиса."""

import asyncio
from pathlib import Path

from src.config.logging_config import get_logger

logger = get_logger(__name__)


class FileSmsCodeProvider:
    """Ждать одноразовый SMS-код в файле и удалить файл после чтения."""

    def __init__(self, path: Path, poll_interval: float = 1.0) -> None:
        self.path = path
        self.poll_interval = poll_interval

    async def get_code(self, phone: str) -> str:
        logger.warning(
            "MAX требует авторизацию для номера ***%s. "
            "Запишите SMS-код в файл %s",
            phone[-2:],
            self.path,
        )
        return await _wait_for_secret(self.path, self.poll_interval, "SMS-код")


class FilePasswordProvider:
    """Получить пароль 2FA из окружения или одноразового файла."""

    def __init__(
        self,
        path: Path,
        inline_password: str | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.path = path
        self.inline_password = inline_password
        self.poll_interval = poll_interval

    async def get_password(self, hint: str | None = None) -> str:
        if self.inline_password:
            return self.inline_password

        logger.warning(
            "MAX требует пароль 2FA%s. Запишите его в файл %s",
            f" (подсказка: {hint})" if hint else "",
            self.path,
        )
        return await _wait_for_secret(self.path, self.poll_interval, "пароль 2FA")


async def _wait_for_secret(path: Path, poll_interval: float, label: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            value = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            value = ""
        except OSError as exc:
            logger.warning("Не удалось прочитать %s: %s", path, exc)
            value = ""

        value = value.strip()
        if value:
            try:
                await asyncio.to_thread(path.unlink, missing_ok=True)
            except OSError as exc:
                logger.warning("Не удалось удалить одноразовый файл %s: %s", path, exc)
            logger.info("%s получен, продолжаю авторизацию", label)
            return value

        await asyncio.sleep(poll_interval)
