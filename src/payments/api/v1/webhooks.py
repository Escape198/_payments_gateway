from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ..dependencies import get_provider_registry, get_secret_store, get_uow_factory

router = APIRouter(tags=["webhooks"])


# Persist-then-process. Endpoint targets P99 < 50ms:
#   1. Look up ProviderInstance + manifest from cache
#   2. Verify signature per manifest-declared scheme (fail-closed)
#   3. INSERT webhook_events ON CONFLICT DO NOTHING (dedup by external_event_id)
#   4. Return 200 immediately
# Business logic runs in a worker that tails webhook_events.

@router.post("/webhooks/{provider_code}")
async def receive_webhook(
    provider_code: str,
    request: Request,
    registry=Depends(get_provider_registry),
    uow_factory=Depends(get_uow_factory),
    secrets=Depends(get_secret_store),
) -> Response: ...
