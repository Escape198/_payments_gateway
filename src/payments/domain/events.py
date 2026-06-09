from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .ids import MerchantId, PaymentId


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: uuid.UUID
    type: str
    occurred_at: datetime
    payment_id: PaymentId
    merchant_id: MerchantId
    payload: dict[str, Any] = field(default_factory=dict)


def PaymentCreated(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
def PaymentAuthorized(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
def PaymentActionRequired(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
def PaymentCaptured(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
def PaymentFailed(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
def PaymentRefunded(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
def PaymentVoided(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
def PaymentChargedBack(*, payment_id, merchant_id, **payload) -> DomainEvent: ...
