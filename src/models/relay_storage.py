"""Надёжное SQLite-хранилище mappings и очереди пересылки."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.config.logging_config import get_logger

logger = get_logger(__name__)

PRIVATE_TO_CHAT = "private_to_chat"
CHAT_TO_PRIVATE = "chat_to_private"


@dataclass(frozen=True, slots=True)
class RelayMapping:
    chat_message_id: int
    private_chat_id: int
    sender_id: int | None
    private_message_id: int


@dataclass(frozen=True, slots=True)
class RelayJob:
    id: int
    direction: str
    source_chat_id: int
    source_message_id: int
    target_chat_id: int
    sender_id: int | None
    delivered_message_id: int | None
    metadata_message_id: int | None
    attempts: int


class RelayStorage:
    """Хранить задания до успешной доставки и маршруты ответов."""

    def __init__(self, database: Path, legacy_json: Path | None = None) -> None:
        self.database = database
        self.legacy_json = legacy_json
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.database)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS relay_mappings (
                chat_message_id INTEGER PRIMARY KEY,
                private_chat_id INTEGER NOT NULL,
                sender_id INTEGER,
                private_message_id INTEGER NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_relay_mappings_private
            ON relay_mappings(private_chat_id, private_message_id);

            CREATE TABLE IF NOT EXISTS relay_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                target_chat_id INTEGER NOT NULL,
                sender_id INTEGER,
                delivered_message_id INTEGER,
                metadata_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(direction, source_chat_id, source_message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_relay_jobs_due
            ON relay_jobs(status, next_attempt_at);
            """
        )
        await self._db.commit()
        await self._migrate_legacy_json()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def enqueue(
        self,
        *,
        direction: str,
        source_chat_id: int,
        source_message_id: int,
        target_chat_id: int,
        sender_id: int | None,
    ) -> bool:
        if direction not in {PRIVATE_TO_CHAT, CHAT_TO_PRIVATE}:
            raise ValueError(f"Неизвестное направление: {direction}")
        now = time.time()
        async with self._lock:
            cursor = await self._conn.execute(
                """
                INSERT OR IGNORE INTO relay_jobs (
                    direction, source_chat_id, source_message_id, target_chat_id,
                    sender_id, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    direction,
                    source_chat_id,
                    source_message_id,
                    target_chat_id,
                    sender_id,
                    now,
                    now,
                    now,
                ),
            )
            await self._conn.commit()
            return cursor.rowcount > 0

    async def next_due_job(self) -> RelayJob | None:
        cursor = await self._conn.execute(
            """
            SELECT id, direction, source_chat_id, source_message_id,
                   target_chat_id, sender_id, delivered_message_id,
                   metadata_message_id, attempts
            FROM relay_jobs
            WHERE status = 'pending' AND next_attempt_at <= ?
            ORDER BY next_attempt_at, id
            LIMIT 1
            """,
            (time.time(),),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return RelayJob(**dict(row)) if row else None

    async def get_mapping(self, chat_message_id: int) -> RelayMapping | None:
        cursor = await self._conn.execute(
            """
            SELECT chat_message_id, private_chat_id, sender_id, private_message_id
            FROM relay_mappings WHERE chat_message_id = ?
            """,
            (chat_message_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return RelayMapping(**dict(row)) if row else None

    async def record_delivery(
        self,
        job: RelayJob,
        delivered_message_id: int,
        *,
        create_mapping: bool,
    ) -> None:
        now = time.time()
        async with self._lock:
            await self._conn.execute(
                "UPDATE relay_jobs SET delivered_message_id = ?, updated_at = ? WHERE id = ?",
                (delivered_message_id, now, job.id),
            )
            if create_mapping:
                await self._insert_mapping(
                    delivered_message_id,
                    job.source_chat_id,
                    job.sender_id,
                    job.source_message_id,
                    now,
                )
            await self._conn.commit()

    async def record_metadata(self, job: RelayJob, metadata_message_id: int) -> None:
        now = time.time()
        async with self._lock:
            await self._conn.execute(
                "UPDATE relay_jobs SET metadata_message_id = ?, updated_at = ? WHERE id = ?",
                (metadata_message_id, now, job.id),
            )
            await self._insert_mapping(
                metadata_message_id,
                job.source_chat_id,
                job.sender_id,
                job.source_message_id,
                now,
            )
            await self._conn.commit()

    async def complete(self, job_id: int) -> None:
        await self._conn.execute(
            """
            UPDATE relay_jobs
            SET status = 'done', last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (time.time(), job_id),
        )
        await self._conn.commit()

    async def reschedule(
        self,
        job: RelayJob,
        error: str,
        delay: float,
        max_attempts: int,
    ) -> bool:
        """Перенести попытку; вернуть True, если задание окончательно остановлено."""
        attempts = job.attempts + 1
        failed = max_attempts > 0 and attempts >= max_attempts
        await self._conn.execute(
            """
            UPDATE relay_jobs
            SET status = ?, attempts = ?, next_attempt_at = ?,
                last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "failed" if failed else "pending",
                attempts,
                time.time() + delay,
                error[:1000],
                time.time(),
                job.id,
            ),
        )
        await self._conn.commit()
        return failed

    async def counts(self) -> dict[str, int]:
        cursor = await self._conn.execute(
            "SELECT status, COUNT(*) AS count FROM relay_jobs GROUP BY status"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {row["status"]: row["count"] for row in rows}

    async def cleanup(self, retention_days: int) -> tuple[int, int]:
        cutoff = time.time() - retention_days * 86400
        async with self._lock:
            mapping_cursor = await self._conn.execute(
                "DELETE FROM relay_mappings WHERE created_at < ?", (cutoff,)
            )
            job_cursor = await self._conn.execute(
                """
                DELETE FROM relay_jobs
                WHERE status IN ('done', 'failed') AND updated_at < ?
                """,
                (cutoff,),
            )
            await self._conn.commit()
            return mapping_cursor.rowcount, job_cursor.rowcount

    async def _insert_mapping(
        self,
        chat_message_id: int,
        private_chat_id: int,
        sender_id: int | None,
        private_message_id: int,
        created_at: float,
    ) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO relay_mappings (
                chat_message_id, private_chat_id, sender_id,
                private_message_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                chat_message_id,
                private_chat_id,
                sender_id,
                private_message_id,
                created_at,
            ),
        )

    async def _migrate_legacy_json(self) -> None:
        if not self.legacy_json or not self.legacy_json.exists():
            return
        try:
            raw = await asyncio.to_thread(self.legacy_json.read_text, encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Не удалось прочитать старое mapping-хранилище: %s", exc)
            return

        imported = 0
        async with self._lock:
            for key, value in data.items():
                try:
                    created = datetime.fromisoformat(value["created_at"]).timestamp()
                    cursor = await self._conn.execute(
                        """
                        INSERT OR IGNORE INTO relay_mappings (
                            chat_message_id, private_chat_id, sender_id,
                            private_message_id, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            int(key),
                            int(value["chat_sender_id"]),
                            int(value["sender_id"]) if value.get("sender_id") is not None else None,
                            int(value.get("private_message_id") or 0),
                            created,
                        ),
                    )
                    imported += max(cursor.rowcount, 0)
                except (KeyError, TypeError, ValueError):
                    logger.warning("Пропущена некорректная legacy mapping-запись %s", key)
            await self._conn.commit()
        if imported:
            logger.info("Импортировано legacy mappings: %s", imported)

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("RelayStorage не открыт")
        return self._db
