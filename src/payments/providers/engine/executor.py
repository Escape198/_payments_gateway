from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ...domain.ids import IdempotencyKey, PaymentId
from ...domain.money import Money
from ..manifest.schema import ProviderManifest
from .http_egress import HttpEgress


class ProviderProtocolError(Exception): ...


@dataclass(slots=True)
class PaymentInputs:
    id: PaymentId
    amount: Money
    method_token: str
    customer_ref: str | None
    metadata: dict[str, Any]
    idempotency_key: IdempotencyKey
    provider_payment_id: str | None = None
    provider_capture_id: str | None = None
    refund_amount_minor: int | None = None


@dataclass(frozen=True, slots=True)
class EngineResult:
    success: bool
    provider_payment_id: str | None
    mapped_status: str | None
    response_mapping: dict[str, Any]
    raw_status_code: int
    raw_response: dict[str, Any]
    error_code: str | None
    error_message: str | None


class OperationExecutor:
    # Steps:
    #   1. Build template context from PaymentInputs + secrets + idempotency + env
    #   2. Render URL, headers, body per the operation declaration
    #   3. HttpEgress.send — retry policy + timeout + circuit breaker live there
    #   4. Parse response (jsonpath/xpath), evaluate success_when
    #   5. Map provider status → domain PaymentStatus
    #      Unmapped status ⇒ ProviderProtocolError (payment parks for review)

    def __init__(self, egress: HttpEgress) -> None:
        self._egress = egress

    async def execute(
        self,
        *,
        manifest: ProviderManifest,
        op_name: str,
        payment: PaymentInputs,
        secrets: Mapping[str, str],
        attempt: int,
        gateway_base_url: str,
        breaker_key: str,
    ) -> EngineResult: ...
