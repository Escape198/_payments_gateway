from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .ids import PaymentId, TransactionId
from .money import Money
from .status import TransactionKind, TransactionStatus


@dataclass(frozen=True, slots=True)
class Transaction:
    id: TransactionId
    payment_id: PaymentId
    kind: TransactionKind
    status: TransactionStatus
    amount: Money
    provider_payment_id: str | None
    request: dict[str, Any]
    response: dict[str, Any]
    error_code: str | None
    error_message: str | None
    attempt: int
    created_at: datetime
