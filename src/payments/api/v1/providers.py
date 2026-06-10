from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...application.commands.register_provider import (
    ProviderRegistrationFailed,
    RegisterProviderCommand,
    RegisterProviderUseCase,
)
from ..dependencies import (
    authenticated_merchant,
    get_executor,
    get_secret_store,
    get_uow_factory,
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
    description=(
        "Submits a ProviderManifest plus the secrets to operate it. On 2xx "
        "the provider instance is immediately addressable in /v1/payments."
    ),
)
async def register_provider(
    body: RegisterProviderRequest,
    _merchant_id: Annotated[uuid.UUID, Depends(authenticated_merchant)],
    uow_factory=Depends(get_uow_factory),
    executor=Depends(get_executor),
    secrets=Depends(get_secret_store),
) -> RegisterProviderResponse:
    use_case = RegisterProviderUseCase(
        uow_factory=uow_factory,
        executor=executor,
        secret_store=secrets,
        gateway_base_url="https://gateway.example",
    )
    try:
        result = await use_case.execute(RegisterProviderCommand(
            manifest_dict=body.manifest,
            account_alias=body.account_alias,
            secrets=body.secrets,
            run_healthcheck=body.run_healthcheck,
        ))
    except ProviderRegistrationFailed as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    return RegisterProviderResponse(
        provider_code=result.provider_code,
        version=result.version,
        provider_instance_id=result.provider_instance_id,
    )
