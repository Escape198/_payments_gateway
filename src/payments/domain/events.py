from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .ids import PaymentId, MerchantId


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: uuid.UUID
    occurred_at: datetime
    payment_id: PaymentId
    merchant_id: MerchantId
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:  # pragma: no cover - override in subclasses
        return self.__class__.__name__


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_event_id() -> uuid.UUID:
    return uuid.uuid4()


def _make(cls, payment_id: PaymentId, merchant_id: MerchantId, **payload: Any) -> DomainEvent:
    return cls(
        event_id=_new_event_id(),
        occurred_at=_now(),
        payment_id=payment_id,
        merchant_id=merchant_id,
        payload=payload,
    )


class PaymentCreated(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.created"


class PaymentAuthorized(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.authorized"


class PaymentActionRequired(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.action_required"


class PaymentCaptured(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.captured"


class PaymentFailed(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.failed"


class PaymentRefunded(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.refunded"


class PaymentVoided(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.voided"


class PaymentChargedBack(DomainEvent):
    @property
    def type(self) -> str:
        return "payment.chargeback"


def make_event(
    cls: type[DomainEvent],
    payment_id: PaymentId,
    merchant_id: MerchantId,
    **payload: Any,
) -> DomainEvent:
    return _make(cls, payment_id, merchant_id, **payload)
