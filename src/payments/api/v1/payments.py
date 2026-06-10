from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...application.commands.create_payment import (
    CreatePaymentCommand,
    CreatePaymentUseCase,
    PaymentCreationFailed,
)
from ...domain import IdempotencyKey, MerchantId, Money, ProviderInstanceId
from ..dependencies import (
    authenticated_merchant,
    get_executor,
    get_secret_store,
    get_uow_factory,
    required_idempotency_key,
)

router = APIRouter(tags=["payments"])


class CreatePaymentRequest(BaseModel):
    provider_instance_id: uuid.UUID
    amount_minor: int = Field(ge=1)
    currency: str = Field(min_length=3, max_length=3)
    method_token: str = Field(min_length=1, max_length=256)
    customer_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentResponse(BaseModel):
    id: uuid.UUID
    status: str
    provider_payment_id: str | None
    next_action: dict[str, Any] | None = None


@router.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment",
)
async def create_payment(
    body: CreatePaymentRequest,
    merchant_id: Annotated[uuid.UUID, Depends(authenticated_merchant)],
    idempotency_key: Annotated[str, Depends(required_idempotency_key)],
    uow_factory=Depends(get_uow_factory),
    executor=Depends(get_executor),
    secrets=Depends(get_secret_store),
) -> PaymentResponse:
    use_case = CreatePaymentUseCase(
        uow_factory=uow_factory,
        executor=executor,
        secret_store=secrets,
        gateway_base_url="https://gateway.example",
    )
    cmd = CreatePaymentCommand(
        merchant_id=MerchantId(merchant_id),
        provider_instance_id=ProviderInstanceId(body.provider_instance_id),
        amount=Money.of(body.amount_minor, body.currency),
        method_token=body.method_token,
        idempotency_key=IdempotencyKey(idempotency_key),
        customer_ref=body.customer_ref,
        metadata=body.metadata,
    )
    try:
        result = await use_case.execute(cmd)
    except PaymentCreationFailed as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    return PaymentResponse(
        id=result.payment_id,
        status=result.status.value,
        provider_payment_id=result.provider_payment_id,
        next_action=result.next_action,
    )


@router.get("/payments/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    merchant_id: Annotated[uuid.UUID, Depends(authenticated_merchant)],
    uow_factory=Depends(get_uow_factory),
) -> PaymentResponse:
    async with uow_factory() as uow:
        payment = await uow.payments.get(payment_id)
        if payment is None or payment.merchant_id != merchant_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "payment not found")
        return PaymentResponse(
            id=payment.id,
            status=payment.status.value,
            provider_payment_id=payment.provider_payment_id,
        )
