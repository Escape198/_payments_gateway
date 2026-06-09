from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Header, Request


# Wired in the app factory. DI container choice (containers, dependency-injector,
# wireup, hand-rolled provide) is uninteresting at the architecture level.

async def get_executor(): ...
async def get_uow_factory(): ...
async def get_secret_store(): ...
async def get_provider_registry(): ...
async def get_idempotency_repo(): ...


# Bearer + HMAC-signed body + timestamp window (anti-replay).
async def authenticated_merchant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_timestamp: Annotated[str | None, Header(alias="X-Timestamp")] = None,
    x_signature: Annotated[str | None, Header(alias="X-Signature")] = None,
) -> uuid.UUID: ...


async def required_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str: ...
