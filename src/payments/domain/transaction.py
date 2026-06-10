from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .ids import PaymentId, TransactionId, new_transaction_id
from .money import Money
from .status import TransactionKind, TransactionStatus


@dataclass(slots=True, frozen=True)
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

    @classmethod
    def for_attempt(
        cls,
        *,
        payment_id: PaymentId,
        kind: TransactionKind,
        amount: Money,
        request: dict[str, Any],
        attempt: int,
    ) -> "Transaction":
        return cls(
            id=new_transaction_id(),
            payment_id=payment_id,
            kind=kind,
            status=TransactionStatus.PENDING,
            amount=amount,
            provider_payment_id=None,
            request=request,
            response={},
            error_code=None,
            error_message=None,
            attempt=attempt,
            created_at=datetime.now(timezone.utc),
        )

    def settle(
        self,
        *,
        status: TransactionStatus,
        provider_payment_id: str | None,
        response: dict[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> "Transaction":
        # Transaction is frozen; settling produces a new row, never mutates.
        return Transaction(
            id=self.id,
            payment_id=self.payment_id,
            kind=self.kind,
            status=status,
            amount=self.amount,
            provider_payment_id=provider_payment_id,
            request=self.request,
            response=response,
            error_code=error_code,
            error_message=error_message,
            attempt=self.attempt,
            created_at=self.created_at,
        )
