from __future__ import annotations

from enum import StrEnum


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    VOIDED = "VOIDED"
    CHARGEBACK = "CHARGEBACK"


class TransactionKind(StrEnum):
    AUTHORIZE = "AUTHORIZE"
    CAPTURE = "CAPTURE"
    VOID = "VOID"
    REFUND = "REFUND"
    PAYOUT = "PAYOUT"


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


ALLOWED: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING:         frozenset({PaymentStatus.ACTION_REQUIRED, PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED, PaymentStatus.FAILED}),
    PaymentStatus.ACTION_REQUIRED: frozenset({PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED, PaymentStatus.FAILED}),
    PaymentStatus.AUTHORIZED:      frozenset({PaymentStatus.CAPTURED, PaymentStatus.VOIDED, PaymentStatus.FAILED}),
    PaymentStatus.CAPTURED:        frozenset({PaymentStatus.SETTLED, PaymentStatus.REFUNDED, PaymentStatus.CHARGEBACK}),
    PaymentStatus.SETTLED:         frozenset({PaymentStatus.REFUNDED, PaymentStatus.CHARGEBACK}),
    PaymentStatus.REFUNDED:        frozenset(),
    PaymentStatus.VOIDED:          frozenset(),
    PaymentStatus.FAILED:          frozenset(),
    PaymentStatus.CHARGEBACK:      frozenset(),
}


def can_transition(src: PaymentStatus, dst: PaymentStatus) -> bool:
    return dst in ALLOWED[src]
