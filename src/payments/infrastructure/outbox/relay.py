from __future__ import annotations

from typing import Awaitable, Callable


class OutboxRelay:
    # Tail outbox, publish to Kafka, mark published. Single leader per shard
    # via Redis lock. Tight loop:
    #   SELECT ... FOR UPDATE SKIP LOCKED LIMIT batch
    #   → publish → UPDATE published_at → COMMIT
    # After N failed publishes a row goes to outbox_dead_letter (alert).
    # Lag exposed as outbox_lag_seconds.

    def __init__(
        self,
        *,
        pg_pool,
        kafka_publisher: Callable[[str, bytes, bytes | None], Awaitable[None]],
        batch_size: int = 100,
        idle_sleep_seconds: float = 0.1,
        max_attempts: int = 8,
    ) -> None: ...

    async def run(self) -> None: ...
