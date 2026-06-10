from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self,
        *,
        pg_pool,
        kafka_publisher: Callable[[str, bytes, bytes | None], Awaitable[None]],
        batch_size: int = 100,
        idle_sleep_seconds: float = 0.1,
        max_attempts: int = 8,
        topic_for_type: Callable[[str], str] | None = None,
    ) -> None:
        self._pool = pg_pool
        self._publish = kafka_publisher
        self._batch = batch_size
        self._idle_sleep = idle_sleep_seconds
        self._max_attempts = max_attempts
        self._topic_for_type = topic_for_type or (lambda _t: "payments.v1")

    async def run(self) -> None:
        while True:
            try:
                handled = await self._drain_batch()
            except Exception:
                logger.exception("outbox relay batch failed")
                await asyncio.sleep(1.0)
                continue
            if handled == 0:
                await asyncio.sleep(self._idle_sleep)

    async def _drain_batch(self) -> int:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT id, aggregate_id, type, payload
                      FROM outbox
                     WHERE published_at IS NULL
                     ORDER BY id
                     LIMIT $1
                     FOR UPDATE SKIP LOCKED
                    """,
                    self._batch,
                )
                if not rows:
                    return 0

                for row in rows:
                    payload = self._serialize(row)
                    try:
                        await self._publish(
                            self._topic_for_type(row["type"]),
                            payload,
                            str(row["aggregate_id"]).encode(),
                        )
                    except Exception as e:
                        await self._dead_letter(conn, row["id"], str(e))
                        continue
                    await conn.execute(
                        "UPDATE outbox SET published_at = now() WHERE id = $1",
                        row["id"],
                    )
                return len(rows)

    @staticmethod
    def _serialize(row: dict[str, Any]) -> bytes:
        envelope = {
            "id": row["id"],
            "aggregate_id": str(row["aggregate_id"]),
            "type": row["type"],
            "payload": row["payload"],
        }
        return json.dumps(envelope, default=str).encode()

    async def _dead_letter(self, conn, outbox_id: int, error: str) -> None:
        await conn.execute(
            "INSERT INTO outbox_dead_letter (outbox_id, error) VALUES ($1, $2)",
            outbox_id, error,
        )
        logger.error("outbox row moved to dead letter", extra={"outbox_id": outbox_id})
