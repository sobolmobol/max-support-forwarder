from types import SimpleNamespace

from src.bot.handlers import handle_chat_message, handle_private_message
from src.models import CHAT_TO_PRIVATE, PRIVATE_TO_CHAT, RelayMapping


class FakeStorage:
    def __init__(self) -> None:
        self.enqueued = []
        self.mapping = None

    async def enqueue(self, **kwargs):
        self.enqueued.append(kwargs)
        return True

    async def get_mapping(self, message_id):
        return self.mapping


class FakeBot:
    target_chat_id = -999

    def __init__(self) -> None:
        self.storage = FakeStorage()
        self.wakeups = 0

    def wake_worker(self) -> None:
        self.wakeups += 1


def fake_client():
    return SimpleNamespace(me=SimpleNamespace(contact=SimpleNamespace(id=777)))


async def test_private_sticker_is_enqueued() -> None:
    bot = FakeBot()
    message = SimpleNamespace(
        id=2,
        chat_id=1,
        sender=1,
        text="",
        attaches=[SimpleNamespace(type="STICKER")],
    )
    await handle_private_message(message, fake_client(), bot)
    assert bot.storage.enqueued[0]["direction"] == PRIVATE_TO_CHAT
    assert bot.wakeups == 1


async def test_operator_video_reply_is_routed() -> None:
    bot = FakeBot()
    bot.storage.mapping = RelayMapping(10, 123, 123, 9)
    message = SimpleNamespace(
        id=11,
        chat_id=-999,
        sender=888,
        text="",
        attaches=[SimpleNamespace(type="VIDEO")],
        link={"type": "REPLY", "message": {"id": 10}},
    )
    await handle_chat_message(message, fake_client(), bot)
    job = bot.storage.enqueued[0]
    assert job["direction"] == CHAT_TO_PRIVATE
    assert job["target_chat_id"] == 123
