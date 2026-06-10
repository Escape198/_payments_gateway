from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ...providers.engine import ProviderRegistry
from ...providers.transformers import extract_jsonpath
from ...providers.webhook import (
    SignatureVerificationError,
    verify_signature,
)
from ..dependencies import get_provider_registry, get_secret_store, get_uow_factory

router = APIRouter(tags=["webhooks"])


@router.post(
    "/webhooks/{provider_code}",
    summary="Public webhook ingress; one route per provider",
    description=(
        "Verifies signature using the manifest-declared scheme, persists the raw "
        "event, returns 200 immediately. Business logic runs in a worker; the "
        "endpoint is intentionally fast (P99 < 50ms target)."
    ),
)
async def receive_webhook(
    provider_code: str,
    request: Request,
    registry: ProviderRegistry = Depends(get_provider_registry),
    uow_factory=Depends(get_uow_factory),
    secrets=Depends(get_secret_store),
) -> Response:
    raw = await request.body()
    headers = {k: v for k, v in request.headers.items()}

    try:
        manifest = await registry.get_active(provider_code)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown provider {provider_code!r}") from e

    # We need to know which ProviderInstance this webhook is for. Two strategies:
    # 1) PSP echoes our instance_id in metadata; preferred.
    # 2) A single instance per (provider_code, gateway endpoint); acceptable for
    #    single-tenant deployments.
    # For brevity the reference uses strategy 2 — production wiring documented
    # in docs/architecture.md §8.
    async with uow_factory() as uow:
        instance = await _resolve_instance(uow, provider_code)
        if instance is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no active instance")
        secret_name = manifest.webhook.signature.secret
        secret_val = None
        if secret_name:
            bag = await secrets.get_many(instance.secret_ref, [secret_name])
            secret_val = bag.get(secret_name)

        try:
            verify_signature(
                cfg=manifest.webhook.signature,
                raw_body=raw,
                headers=headers,
                secret=secret_val,
            )
        except SignatureVerificationError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e

        # parse minimally — full parse happens in the worker
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        external_event_id = extract_jsonpath(payload, manifest.webhook.event_id_path) or str(uuid.uuid4())

        # ON CONFLICT DO NOTHING semantics live in the repository impl.
        await uow.webhook_events.add(  # type: ignore[attr-defined]
            instance_id=instance.id,
            external_event_id=str(external_event_id),
            raw=payload,
            headers=headers,
            received_at=datetime.now(timezone.utc),
        )

    return Response(status_code=status.HTTP_200_OK)


async def _resolve_instance(uow, provider_code: str):
    # Production: SELECT ... WHERE manifest.code=$1 AND is_active LIMIT 1.
    # The reference omits the literal SQL.
    return await uow.providers.get_default_instance_for_code(provider_code)  # type: ignore[attr-defined]
