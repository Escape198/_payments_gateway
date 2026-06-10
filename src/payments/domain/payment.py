from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .events import (
    DomainEvent,
    PaymentActionRequired,
    PaymentAuthorized,
    PaymentCaptured,
    PaymentChargedBack,
    PaymentCreated,
    PaymentFailed,
    PaymentRefunded,
    PaymentVoided,
    make_event,
)
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
    def create(
        cls,
        *,
        id: PaymentId,
        merchant_id: MerchantId,
        provider_instance_id: ProviderInstanceId,
        amount: Money,
        method_token: str,
        idempotency_key: IdempotencyKey,
        customer_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Payment":
        now = datetime.now(timezone.utc)
        payment = cls(
            id=id,
            merchant_id=merchant_id,
            provider_instance_id=provider_instance_id,
            amount=amount,
            method_token=method_token,
            idempotency_key=idempotency_key,
            customer_ref=customer_ref,
            metadata=metadata or {},
            status=PaymentStatus.PENDING,
            provider_payment_id=None,
            refunded=Money.of(0, amount.currency),
            created_at=now,
            updated_at=now,
        )
        payment._record(make_event(
            PaymentCreated, id, merchant_id,
            amount_minor=amount.amount_minor, currency=amount.currency,
        ))
        return payment

    def mark_action_required(self, next_action: dict[str, Any]) -> None:
        self._transition(PaymentStatus.ACTION_REQUIRED)
        self._record(make_event(
            PaymentActionRequired, self.id, self.merchant_id, next_action=next_action,
        ))

    def authorize(self, provider_payment_id: str) -> None:
        self._transition(PaymentStatus.AUTHORIZED)
        self.provider_payment_id = provider_payment_id
        self._record(make_event(
            PaymentAuthorized, self.id, self.merchant_id,
            provider_payment_id=provider_payment_id,
        ))

    def capture(self, provider_payment_id: str | None = None) -> None:
        self._transition(PaymentStatus.CAPTURED)
        if provider_payment_id is not None:
            self.provider_payment_id = provider_payment_id
        self._record(make_event(
            PaymentCaptured, self.id, self.merchant_id,
            amount_minor=self.amount.amount_minor, currency=self.amount.currency,
            provider_payment_id=self.provider_payment_id,
        ))

    def fail(self, *, code: str | None, message: str | None) -> None:
        self._transition(PaymentStatus.FAILED)
        self._record(make_event(
            PaymentFailed, self.id, self.merchant_id, code=code, message=message,
        ))

    def void(self) -> None:
        self._transition(PaymentStatus.VOIDED)
        self._record(make_event(PaymentVoided, self.id, self.merchant_id))

    def refund(self, amount: Money) -> None:
        if self.status not in (PaymentStatus.CAPTURED, PaymentStatus.SETTLED):
            raise IllegalStateTransition(
                f"refund not allowed from status {self.status}"
            )
        if amount.currency != self.amount.currency:
            raise ValueError("refund currency mismatch")
        new_refunded = self.refunded.add(amount)
        if new_refunded.amount_minor > self.amount.amount_minor:
            raise ValueError("refund amount exceeds captured amount")
        self.refunded = new_refunded
        if new_refunded.amount_minor == self.amount.amount_minor:
            self._transition(PaymentStatus.REFUNDED)
        # partial refunds keep status CAPTURED/SETTLED; the event is emitted regardless
        self._record(make_event(
            PaymentRefunded, self.id, self.merchant_id,
            refunded_minor=amount.amount_minor, currency=amount.currency,
            total_refunded_minor=new_refunded.amount_minor,
            fully_refunded=new_refunded.amount_minor == self.amount.amount_minor,
        ))

    def chargeback(self, reason: str | None) -> None:
        self._transition(PaymentStatus.CHARGEBACK)
        self._record(make_event(
            PaymentChargedBack, self.id, self.merchant_id, reason=reason,
        ))

    # internals

    def _transition(self, dst: PaymentStatus) -> None:
        if not can_transition(self.status, dst):
            raise IllegalStateTransition(
                f"{self.status} -> {dst} is not allowed for Payment {self.id}"
            )
        self.status = dst
        self.updated_at = datetime.now(timezone.utc)

    def _record(self, event: DomainEvent) -> None:
        self.pending_events.append(event)

    def drain_events(self) -> list[DomainEvent]:
        events, self.pending_events = self.pending_events, []
        return events
