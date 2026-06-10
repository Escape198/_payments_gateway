from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status


# These are placeholders backed by app state in production wiring.
# The brief is about architecture; the exact DI mechanism (containers,
# Provide.Provides, etc.) is uninteresting noise here.


async def get_executor():
    raise NotImplementedError("wire OperationExecutor in app factory")


async def get_uow_factory():
    raise NotImplementedError("wire UnitOfWork factory in app factory")


async def get_secret_store():
    raise NotImplementedError("wire SecretStore in app factory")


async def get_provider_registry():
    raise NotImplementedError("wire ProviderRegistry in app factory")


async def get_idempotency_repo():
    raise NotImplementedError("wire IdempotencyRepository in app factory")


# ---- merchant authentication: Bearer + HMAC request signature

MerchantId = uuid.UUID  # alias; real type lives in domain


async def authenticated_merchant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_timestamp: Annotated[str | None, Header(alias="X-Timestamp")] = None,
    x_signature: Annotated[str | None, Header(alias="X-Signature")] = None,
) -> MerchantId:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    merchant_id, api_secret = await _lookup_merchant(token)
    if merchant_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")

    if not (x_timestamp and x_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing signature headers")
    try:
        ts = int(x_timestamp)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad timestamp")
    if abs(time.time() - ts) > 300:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "timestamp outside window")

    body = await request.body()
    signed = f"{ts}.".encode() + body
    expected = hmac.new(api_secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_signature.strip()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signature mismatch")

    return merchant_id


async def _lookup_merchant(token: str) -> tuple[MerchantId | None, str]:
    # production: cached lookup in `merchants` table; here is a stub.
    return uuid.UUID(int=0), "stub-secret"


# ---- idempotency precondition

class IdempotencyMissing(Exception): ...


async def required_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Idempotency-Key header is required for mutating requests",
        )
    if not (8 <= len(idempotency_key) <= 128):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Idempotency-Key must be 8..128 chars",
        )
    return idempotency_key


def hash_request_body(body: bytes | dict[str, Any]) -> str:
    raw = body if isinstance(body, bytes) else json.dumps(body, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()
