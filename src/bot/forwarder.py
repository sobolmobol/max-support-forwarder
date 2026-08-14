"""Устойчивый двусторонний пересыльщик на PyMax 2.4."""

from __future__ import annotations

import asyncio
import random

from pymax import Client, ExtraConfig, Message

from src.bot.auth import FilePasswordProvider, FileSmsCodeProvider
from src.bot.handlers import handle_chat_message, handle_private_message
from src.bot.utils import build_sender_card, compact_error
from src.config.logging_config import get_logger
from src.config.settings import BotConfig
from src.models import CHAT_TO_PRIVATE, PRIVATE_TO_CHAT, RelayJob, RelayStorage

logger = get_logger(__name__)


class MessageForwarderBot:
    """Пересылать сообщения через native forward и durable outbox."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.target_chat_id = config.target_chat_id
        self.storage = RelayStorage(config.state_db, config.legacy_mapping_file)

        config.prepare_directories()
        self.client = Client(
            phone=config.phone,
            work_dir=str(config.work_dir),
            session_name=config.session_name,
            sms_code_provider=FileSmsCodeProvider(config.sms_code_file),
            password_provider=FilePasswordProvider(
                config.password_file,
                inline_password=config.two_factor_password,
            ),
            extra_config=ExtraConfig(
                reconnect=True,
                reconnect_delay=config.reconnect_delay,
                request_timeout=config.request_timeout,
                log_level=config.pymax_log_level,
            ),
        )

        self._connected = asyncio.Event()
        self._worker_wakeup = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.client.on_start()
        async def _on_start(client: Client) -> None:
            self._connected.set()
            self._worker_wakeup.set()
            account_id = client.me.contact.id if client.me else "unknown"
            logger.info(
                "MAX подключён: account=%s target_chat=%s",
                account_id,
                self.target_chat_id,
            )

        @self.client.on_disconnect()
        async def _on_disconnect(exc: Exception, reconnect: bool, delay: float) -> None:
            self._connected.clear()
            logger.warning(
                "MAX отключён: %s; reconnect=%s delay=%.1fs",
                compact_error(exc),
                reconnect,
                delay,
            )

        @self.client.on_error()
        async def _on_error(exc: Exception, _context: object) -> None:
            logger.error(
                "Ошибка PyMax handler: %s",
                compact_error(exc),
                exc_info=(type(exc), exc, exc.__traceback__),
            )

        @self.client.on_message()
        async def _on_message(message: Message, client: Client) -> None:
            try:
                if message.chat_id == self.target_chat_id:
                    await handle_chat_message(message, client, self)
                elif message.chat_id is not None and message.chat_id > 0:
                    await handle_private_message(message, client, self)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Ошибка обработки message=%s chat=%s",
                    getattr(message, "id", None),
                    getattr(message, "chat_id", None),
                )

    async def start(self) -> None:
        """Открыть outbox и передать reconnect/relogin библиотеке PyMax."""
        await self.storage.open()
        counts = await self.storage.counts()
        logger.info("Очередь открыта: %s", counts)
        self._tasks = [
            asyncio.create_task(self._outbox_loop(), name="relay-outbox"),
            asyncio.create_task(self._cleanup_loop(), name="relay-cleanup"),
        ]
        try:
            # PyMax 2.4 сам переподключается, обновляет token и запускает relogin.
            await self.client.start()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Гарантированно закрыть клиент, фоновые задачи и SQLite."""
        self._connected.clear()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        try:
            await self.client.stop()
        finally:
            await self.storage.close()
        logger.info("Бот остановлен")

    def wake_worker(self) -> None:
        self._worker_wakeup.set()

    async def _outbox_loop(self) -> None:
        while True:
            try:
                await self._connected.wait()
                job = await self.storage.next_due_job()
                if job is not None:
                    await self._process_job(job)
                    continue

                self._worker_wakeup.clear()
                try:
                    await asyncio.wait_for(
                        self._worker_wakeup.wait(),
                        timeout=self.config.outbox_poll_seconds,
                    )
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка цикла outbox")
                await asyncio.sleep(self.config.outbox_poll_seconds)

    async def _process_job(self, job: RelayJob) -> None:
        try:
            if job.direction == PRIVATE_TO_CHAT:
                await self._deliver_private_message(job)
            elif job.direction == CHAT_TO_PRIVATE:
                await self._deliver_operator_reply(job)
            else:
                raise ValueError(f"Неизвестное направление {job.direction}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            delay = self._retry_delay(job.attempts)
            failed = await self.storage.reschedule(
                job,
                compact_error(exc),
                delay,
                self.config.job_max_attempts,
            )
            if failed:
                logger.error(
                    "Задание %s остановлено после %s попыток: %s",
                    job.id,
                    job.attempts + 1,
                    compact_error(exc),
                )
            else:
                logger.warning(
                    "Задание %s не доставлено, повтор через %.1fs: %s",
                    job.id,
                    delay,
                    compact_error(exc),
                )

    async def _deliver_private_message(self, job: RelayJob) -> None:
        delivered_id = job.delivered_message_id
        if delivered_id is None:
            forwarded = await self.client.forward_message(
                chat_id=job.target_chat_id,
                message_id=job.source_message_id,
                source_chat_id=job.source_chat_id,
            )
            delivered_id = int(forwarded.id)
            await self.storage.record_delivery(job, delivered_id, create_mapping=True)
            logger.info(
                "Сообщение переслано в рабочий чат: source=%s delivered=%s",
                job.source_message_id,
                delivered_id,
            )

        if job.metadata_message_id is None:
            card = await self._sender_card(job.sender_id or job.source_chat_id)
            metadata = await self.client.send_message(
                chat_id=job.target_chat_id,
                text=card,
                reply_to=delivered_id,
            )
            await self.storage.record_metadata(job, int(metadata.id))

        await self.storage.complete(job.id)

    async def _deliver_operator_reply(self, job: RelayJob) -> None:
        if job.delivered_message_id is None:
            forwarded = await self.client.forward_message(
                chat_id=job.target_chat_id,
                message_id=job.source_message_id,
                source_chat_id=job.source_chat_id,
            )
            await self.storage.record_delivery(
                job,
                int(forwarded.id),
                create_mapping=False,
            )
        await self.storage.complete(job.id)
        logger.info(
            "Ответ оператора доставлен: source=%s target_chat=%s",
            job.source_message_id,
            job.target_chat_id,
        )

    async def _sender_card(self, sender_id: int) -> str:
        user = None
        try:
            user = await self.client.get_user(sender_id)
        except Exception as exc:
            logger.warning(
                "Не удалось получить профиль sender=%s: %s",
                sender_id,
                compact_error(exc),
            )
        return build_sender_card(user, sender_id, self.config.include_phone)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval_seconds)
                mappings, jobs = await self.storage.cleanup(
                    self.config.mapping_retention_days
                )
                if mappings or jobs:
                    logger.info(
                        "Очистка состояния: mappings=%s jobs=%s", mappings, jobs
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка очистки состояния")

    @staticmethod
    def _retry_delay(attempts: int) -> float:
        return min(900.0, 5.0 * (2 ** min(attempts, 8))) + random.uniform(0, 2)
