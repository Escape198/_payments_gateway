from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain import (
    IdempotencyKey, MerchantId, Money, PaymentId, PaymentStatus, ProviderInstanceId,
)
from ...providers.engine import OperationExecutor
from ..ports import SecretStore, UnitOfWork


class PaymentCreationFailed(Exception): ...


@dataclass(frozen=True, slots=True)
class CreatePaymentCommand:
    merchant_id: MerchantId
    provider_instance_id: ProviderInstanceId
    amount: Money
    method_token: str
    idempotency_key: IdempotencyKey
    customer_ref: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CreatePaymentResult:
    payment_id: PaymentId
    status: PaymentStatus
    provider_payment_id: str | None
    next_action: dict[str, Any] | None


class CreatePaymentUseCase:
    # 1. resolve ProviderInstance + manifest + secrets
    # 2. create Payment aggregate (PENDING), insert pending Transaction
    # 3. engine.execute("charge") inside the UoW
    # 4. apply mapped status to aggregate
    # 5. settle Transaction, drain events to outbox
    # 6. commit; idempotency record on the way out

    def __init__(self, uow_factory, executor: OperationExecutor,
                 secrets: SecretStore, gateway_base_url: str) -> None:
        self._uow = uow_factory
        self._exec = executor
        self._secrets = secrets
        self._base = gateway_base_url

    async def execute(self, cmd: CreatePaymentCommand) -> CreatePaymentResult: ...
