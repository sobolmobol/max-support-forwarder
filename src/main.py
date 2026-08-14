"""Точка входа MAX Forwarder."""

import asyncio
import sys

from src.bot.forwarder import MessageForwarderBot
from src.config.logging_config import get_logger, setup_logging
from src.config.settings import BotConfig


def main() -> None:
    """Загрузить конфигурацию и запустить бота."""
    try:
        config = BotConfig.from_env()
    except ValueError as exc:
        print(f"Ошибка конфигурации: {exc}")
        print("Создайте .env на основе .env.example и заполните его.")
        sys.exit(1)

    setup_logging(
        log_level=config.log_level,
        log_file=config.log_file,
        log_max_bytes=config.log_max_bytes,
        log_backup_count=config.log_backup_count,
        pymax_log_level=config.pymax_log_level,
    )
    logger = get_logger(__name__)
    logger.info("Запуск MAX Forwarder Bot: %s", config.to_public_dict())

    bot = MessageForwarderBot(config)
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception:
        logger.exception("Бот завершился с необработанной ошибкой")
        sys.exit(1)


if __name__ == "__main__":
    main()
