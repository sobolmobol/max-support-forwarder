"""Быстрые обработчики: принимают событие и кладут его в durable outbox."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymax import Client, Message

from src.bot.utils import extract_reply_message_id, has_message_payload
from src.config.logging_config import get_logger
from src.models import CHAT_TO_PRIVATE, PRIVATE_TO_CHAT

if TYPE_CHECKING:
    from src.bot.forwarder import MessageForwarderBot


logger = get_logger(__name__)


async def handle_private_message(
    message: Message,
    client: Client,
    bot: MessageForwarderBot,
) -> None:
    """Поставить личное сообщение в очередь пересылки в рабочий чат."""
    if message.chat_id is None or not has_message_payload(message):
        return
    if _is_from_self(message, client):
        return

    sender_id = message.sender if message.sender is not None else message.chat_id
    queued = await bot.storage.enqueue(
        direction=PRIVATE_TO_CHAT,
        source_chat_id=message.chat_id,
        source_message_id=message.id,
        target_chat_id=bot.target_chat_id,
        sender_id=sender_id,
    )
    if queued:
        logger.info(
            "Личное сообщение поставлено в очередь: chat=%s message=%s",
            message.chat_id,
            message.id,
        )
        bot.wake_worker()


async def handle_chat_message(
    message: Message,
    client: Client,
    bot: MessageForwarderBot,
) -> None:
    """Поставить reply оператора в очередь пересылки пользователю."""
    if message.chat_id is None or not has_message_payload(message):
        return
    if _is_from_self(message, client):
        return

    reply_message_id = extract_reply_message_id(message)
    if reply_message_id is None:
        return

    mapping = await bot.storage.get_mapping(reply_message_id)
    if mapping is None:
        logger.warning("Не найден маршрут для reply message=%s", reply_message_id)
        return

    queued = await bot.storage.enqueue(
        direction=CHAT_TO_PRIVATE,
        source_chat_id=message.chat_id,
        source_message_id=message.id,
        target_chat_id=mapping.private_chat_id,
        sender_id=message.sender,
    )
    if queued:
        logger.info(
            "Ответ оператора поставлен в очередь: message=%s target_chat=%s",
            message.id,
            mapping.private_chat_id,
        )
        bot.wake_worker()


def _is_from_self(message: Message, client: Client) -> bool:
    me = client.me
    return bool(me and message.sender == me.contact.id)
