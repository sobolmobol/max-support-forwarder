"""Небольшие чистые функции для маршрутизации сообщений."""

from __future__ import annotations

from typing import Any


def extract_reply_message_id(message: Any) -> int | None:
    """Извлечь ID исходного сообщения из разных wire-форматов MAX."""
    link = getattr(message, "link", None)
    candidates: list[Any] = [
        getattr(message, "reply_to", None),
        getattr(message, "reply_to_message_id", None),
    ]

    if link is not None:
        candidates.extend(_link_candidates(link))

    for candidate in candidates:
        result = _as_int(candidate)
        if result is not None:
            return result
    return None


def has_message_payload(message: Any) -> bool:
    """Отфильтровать пустые и служебные события."""
    return bool((getattr(message, "text", "") or "").strip()) or bool(
        getattr(message, "attaches", None)
    )


def build_sender_card(user: Any, sender_id: int, include_phone: bool) -> str:
    """Сформировать безопасную короткую карточку автора обращения."""
    name = str(sender_id)
    phone = None
    if user is not None:
        names = getattr(user, "names", None) or []
        if names:
            first = names[0]
            name = getattr(first, "name", None) or str(first)
        else:
            name = getattr(user, "name", None) or name
        phone = getattr(user, "phone", None)

    lines = [f"👤 **Обращение от:** {_escape_markdown(str(name))}", f"ID: `{sender_id}`"]
    if include_phone and phone:
        lines.append(f"Телефон: `{_escape_markdown(str(phone))}`")
    return "\n".join(lines)


def compact_error(exc: Exception) -> str:
    """Не допустить многострочные мегалоги и случайный вывод payload."""
    return " ".join(str(exc).split())[:1000] or type(exc).__name__


def _link_candidates(link: Any) -> list[Any]:
    if hasattr(link, "model_dump"):
        link = link.model_dump(by_alias=True)

    if isinstance(link, dict):
        result = [
            link.get("messageId"),
            link.get("message_id"),
            link.get("replyTo"),
            link.get("reply_to"),
        ]
        nested = link.get("message")
        if isinstance(nested, dict):
            result.extend((nested.get("id"), nested.get("messageId")))
        elif nested is not None:
            result.extend(
                (getattr(nested, "id", None), getattr(nested, "message_id", None))
            )
        return result

    return [
        getattr(link, "message_id", None),
        getattr(link, "messageId", None),
        getattr(getattr(link, "message", None), "id", None),
    ]


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _escape_markdown(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*")
