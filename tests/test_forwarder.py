from types import SimpleNamespace

from src.bot.forwarder import MessageForwarderBot
from src.models import CHAT_TO_PRIVATE, PRIVATE_TO_CHAT, RelayJob


class FakeClient:
    def __init__(self) -> None:
        self.forwarded = []
        self.sent = []

    async def forward_message(self, **kwargs):
        self.forwarded.append(kwargs)
        return SimpleNamespace(id=700 + len(self.forwarded))

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=800 + len(self.sent))

    async def get_user(self, _sender_id):
        return None


class FakeStorage:
    def __init__(self) -> None:
        self.deliveries = []
        self.metadata = []
        self.completed = []

    async def record_delivery(self, job, message_id, *, create_mapping):
        self.deliveries.append((job.id, message_id, create_mapping))

    async def record_metadata(self, job, message_id):
        self.metadata.append((job.id, message_id))

    async def complete(self, job_id):
        self.completed.append(job_id)


def make_bot() -> MessageForwarderBot:
    bot = object.__new__(MessageForwarderBot)
    bot.client = FakeClient()
    bot.storage = FakeStorage()
    bot.config = SimpleNamespace(include_phone=False)
    return bot


async def test_private_delivery_uses_native_forward() -> None:
    bot = make_bot()
    job = RelayJob(1, PRIVATE_TO_CHAT, 100, 200, -300, 100, None, None, 0)

    await bot._deliver_private_message(job)

    assert bot.client.forwarded == [
        {"chat_id": -300, "message_id": 200, "source_chat_id": 100}
    ]
    assert bot.storage.deliveries == [(1, 701, True)]
    assert bot.client.sent[0]["reply_to"] == 701
    assert bot.storage.completed == [1]


async def test_operator_delivery_uses_native_forward() -> None:
    bot = make_bot()
    job = RelayJob(2, CHAT_TO_PRIVATE, -300, 201, 100, 900, None, None, 0)

    await bot._deliver_operator_reply(job)

    assert bot.client.forwarded == [
        {"chat_id": 100, "message_id": 201, "source_chat_id": -300}
    ]
    assert bot.storage.deliveries == [(2, 701, False)]
    assert bot.storage.completed == [2]
