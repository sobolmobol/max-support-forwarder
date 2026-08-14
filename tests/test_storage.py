from src.models import PRIVATE_TO_CHAT, RelayStorage


async def test_outbox_is_durable_and_deduplicated(tmp_path) -> None:
    storage = RelayStorage(tmp_path / "relay.db")
    await storage.open()
    try:
        inserted = await storage.enqueue(
            direction=PRIVATE_TO_CHAT,
            source_chat_id=100,
            source_message_id=200,
            target_chat_id=-300,
            sender_id=400,
        )
        duplicate = await storage.enqueue(
            direction=PRIVATE_TO_CHAT,
            source_chat_id=100,
            source_message_id=200,
            target_chat_id=-300,
            sender_id=400,
        )
        assert inserted is True
        assert duplicate is False

        job = await storage.next_due_job()
        assert job is not None
        await storage.record_delivery(job, 501, create_mapping=True)
        await storage.record_metadata(job, 502)
        await storage.complete(job.id)

        mapping = await storage.get_mapping(501)
        header_mapping = await storage.get_mapping(502)
        assert mapping is not None
        assert mapping.private_chat_id == 100
        assert header_mapping is not None
        assert (await storage.counts())["done"] == 1
    finally:
        await storage.close()


async def test_pending_job_survives_reopen(tmp_path) -> None:
    path = tmp_path / "relay.db"
    first = RelayStorage(path)
    await first.open()
    await first.enqueue(
        direction=PRIVATE_TO_CHAT,
        source_chat_id=1,
        source_message_id=2,
        target_chat_id=-3,
        sender_id=4,
    )
    await first.close()

    second = RelayStorage(path)
    await second.open()
    try:
        job = await second.next_due_job()
        assert job is not None
        assert job.source_message_id == 2
    finally:
        await second.close()


async def test_zero_max_attempts_retries_forever(tmp_path) -> None:
    storage = RelayStorage(tmp_path / "relay.db")
    await storage.open()
    try:
        await storage.enqueue(
            direction=PRIVATE_TO_CHAT,
            source_chat_id=1,
            source_message_id=2,
            target_chat_id=-3,
            sender_id=4,
        )
        job = await storage.next_due_job()
        assert job is not None

        failed = await storage.reschedule(job, "network down", 0, max_attempts=0)

        assert failed is False
        retried = await storage.next_due_job()
        assert retried is not None
        assert retried.attempts == 1
    finally:
        await storage.close()
