from __future__ import annotations

import uuid
from typing import NewType

PaymentId = NewType("PaymentId", uuid.UUID)
MerchantId = NewType("MerchantId", uuid.UUID)
ProviderInstanceId = NewType("ProviderInstanceId", uuid.UUID)
TransactionId = NewType("TransactionId", uuid.UUID)
IdempotencyKey = NewType("IdempotencyKey", str)


def new_payment_id() -> PaymentId:
    return PaymentId(uuid.uuid4())  # production: UUIDv7 for index locality


def new_transaction_id() -> TransactionId:
    return TransactionId(uuid.uuid4())
