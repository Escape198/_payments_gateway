from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from ..dependencies import (
    authenticated_merchant, get_executor, get_secret_store, get_uow_factory,
)

router = APIRouter(tags=["providers"])


class RegisterProviderRequest(BaseModel):
    manifest: dict[str, Any]
    account_alias: str = Field(min_length=1, max_length=64)
    secrets: dict[str, str]
    run_healthcheck: bool = True


class RegisterProviderResponse(BaseModel):
    provider_code: str
    version: str
    provider_instance_id: uuid.UUID


@router.post(
    "/providers",
    response_model=RegisterProviderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a provider via manifest — no code, no restart",
)
async def register_provider(
    body: RegisterProviderRequest,
    _merchant_id: Annotated[uuid.UUID, Depends(authenticated_merchant)],
    uow_factory=Depends(get_uow_factory),
    executor=Depends(get_executor),
    secrets=Depends(get_secret_store),
) -> RegisterProviderResponse: ...
