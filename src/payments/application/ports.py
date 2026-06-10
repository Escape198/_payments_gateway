from __future__ import annotations

from typing import Any, Protocol

from ..domain import (
    DomainEvent,
    MerchantId,
    Payment,
    PaymentId,
    ProviderInstance,
    ProviderInstanceId,
    Transaction,
)
from ..providers.manifest import ProviderManifest


class UnitOfWork(Protocol):
    payments: "PaymentRepository"
    transactions: "TransactionRepository"
    providers: "ProviderRepository"
    outbox: "OutboxRepository"
    idempotency: "IdempotencyRepository"

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, *exc: Any) -> None: ...


class PaymentRepository(Protocol):
    async def add(self, payment: Payment) -> None: ...
    async def get(self, payment_id: PaymentId) -> Payment | None: ...
    async def update(self, payment: Payment) -> None: ...


class TransactionRepository(Protocol):
    async def add(self, transaction: Transaction) -> None: ...


class ProviderRepository(Protocol):
    async def get_instance(self, instance_id: ProviderInstanceId) -> ProviderInstance | None: ...
    async def load_manifest(self, code: str, version: str) -> ProviderManifest: ...
    async def upsert_manifest(self, manifest: ProviderManifest) -> None: ...
    async def upsert_instance(self, instance: ProviderInstance) -> None: ...


class OutboxRepository(Protocol):
    async def add_many(self, events: list[DomainEvent]) -> None: ...


class IdempotencyRepository(Protocol):
    async def get(self, merchant_id: MerchantId, key: str) -> dict[str, Any] | None: ...
    async def put(
        self,
        merchant_id: MerchantId,
        key: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None: ...


class SecretStore(Protocol):
    async def get_many(self, secret_ref: str, names: list[str]) -> dict[str, str]: ...
    async def put_many(self, secret_ref: str, secrets: dict[str, str]) -> None: ...
