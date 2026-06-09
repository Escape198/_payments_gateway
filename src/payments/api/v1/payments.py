from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from ..dependencies import (
    authenticated_merchant, get_executor, get_secret_store,
    get_uow_factory, required_idempotency_key,
)

router = APIRouter(tags=["payments"])


class CreatePaymentRequest(BaseModel):
    provider_instance_id: uuid.UUID
    amount_minor: int = Field(ge=1)
    currency: str = Field(min_length=3, max_length=3)
    method_token: str
    customer_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentResponse(BaseModel):
    id: uuid.UUID
    status: str
    provider_payment_id: str | None
    next_action: dict[str, Any] | None = None


@router.post("/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    body: CreatePaymentRequest,
    merchant_id: Annotated[uuid.UUID, Depends(authenticated_merchant)],
    idempotency_key: Annotated[str, Depends(required_idempotency_key)],
    uow_factory=Depends(get_uow_factory),
    executor=Depends(get_executor),
    secrets=Depends(get_secret_store),
) -> PaymentResponse: ...


@router.get("/payments/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    merchant_id: Annotated[uuid.UUID, Depends(authenticated_merchant)],
    uow_factory=Depends(get_uow_factory),
) -> PaymentResponse: ...
