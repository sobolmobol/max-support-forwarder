from types import SimpleNamespace

from src.bot.utils import build_sender_card, extract_reply_message_id, has_message_payload


def test_extract_reply_from_nested_message() -> None:
    message = SimpleNamespace(link={"type": "REPLY", "message": {"id": 123}})
    assert extract_reply_message_id(message) == 123


def test_extract_reply_from_direct_message_id() -> None:
    message = SimpleNamespace(link={"type": "REPLY", "messageId": "456"})
    assert extract_reply_message_id(message) == 456


def test_extract_reply_from_legacy_attribute() -> None:
    message = SimpleNamespace(link=None, reply_to_message_id=789)
    assert extract_reply_message_id(message) == 789


def test_payload_accepts_sticker_without_text() -> None:
    message = SimpleNamespace(text="", attaches=[SimpleNamespace(type="STICKER")])
    assert has_message_payload(message)


def test_sender_card_escapes_markdown() -> None:
    user = SimpleNamespace(
        names=[SimpleNamespace(name="A*B`C")],
        phone="+70000000000",
    )
    card = build_sender_card(user, 42, include_phone=True)
    assert "A\\*B\\`C" in card
    assert "+70000000000" in card
