from __future__ import annotations

import uuid
from typing import NewType

PaymentId = NewType("PaymentId", uuid.UUID)
MerchantId = NewType("MerchantId", uuid.UUID)
ProviderInstanceId = NewType("ProviderInstanceId", uuid.UUID)
TransactionId = NewType("TransactionId", uuid.UUID)
IdempotencyKey = NewType("IdempotencyKey", str)


def new_payment_id() -> PaymentId:
    return PaymentId(_uuid7())


def new_transaction_id() -> TransactionId:
    return TransactionId(_uuid7())


def _uuid7() -> uuid.UUID:
    # UUIDv7: time-sortable. Python stdlib will gain uuid7 in 3.13; we ship a
    # minimal implementation so DB indexes stay locality-friendly.
    import os
    import time

    ts_ms = int(time.time() * 1000)
    rand = os.urandom(10)
    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    b[6] = (0x70 | (rand[0] & 0x0F))
    b[7] = rand[1]
    b[8] = (0x80 | (rand[2] & 0x3F))
    b[9:16] = rand[3:10]
    return uuid.UUID(bytes=bytes(b))
