from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .events import DomainEvent
from .ids import IdempotencyKey, MerchantId, PaymentId, ProviderInstanceId
from .money import Money
from .status import PaymentStatus, can_transition


class IllegalStateTransition(Exception): ...


@dataclass(slots=True)
class Payment:
    id: PaymentId
    merchant_id: MerchantId
    provider_instance_id: ProviderInstanceId
    amount: Money
    method_token: str
    idempotency_key: IdempotencyKey
    customer_ref: str | None
    metadata: dict[str, Any]
    status: PaymentStatus
    provider_payment_id: str | None
    refunded: Money
    created_at: datetime
    updated_at: datetime
    pending_events: list[DomainEvent] = field(default_factory=list)

    @classmethod
    def create(cls, **kw) -> "Payment": ...

    def mark_action_required(self, next_action: dict[str, Any]) -> None: ...
    def authorize(self, provider_payment_id: str) -> None: ...
    def capture(self, provider_payment_id: str | None = None) -> None: ...
    def fail(self, *, code: str | None, message: str | None) -> None: ...
    def void(self) -> None: ...
    def refund(self, amount: Money) -> None: ...
    def chargeback(self, reason: str | None) -> None: ...

    def _transition(self, dst: PaymentStatus) -> None:
        if not can_transition(self.status, dst):
            raise IllegalStateTransition(f"{self.status} -> {dst}")
        self.status = dst

    def drain_events(self) -> list[DomainEvent]:
        events, self.pending_events = self.pending_events, []
        return events
