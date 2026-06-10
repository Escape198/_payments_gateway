from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ...domain.ids import IdempotencyKey, PaymentId
from ...domain.money import Money
from ..manifest.schema import Operation, ProviderManifest
from ..transformers import (
    evaluate_success_when,
    extract_jsonpath,
    render_template,
)
from .http_egress import HttpEgress, HttpResponse


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


@dataclass(slots=True, frozen=True)
class EngineResult:
    success: bool
    provider_payment_id: str | None
    mapped_status: str | None     # domain PaymentStatus name, or None on failure
    response_mapping: dict[str, Any]
    raw_status_code: int
    raw_response: dict[str, Any]
    error_code: str | None
    error_message: str | None


class SecretsView(Protocol):
    def __getitem__(self, name: str) -> str: ...


class OperationExecutor:
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
    ) -> EngineResult:
        if op_name not in manifest.operations:
            raise ProviderProtocolError(f"operation {op_name!r} not declared in manifest")
        op = manifest.operations[op_name]

        ctx = self._build_context(payment, secrets, op, attempt, gateway_base_url)
        url = render_template(op.url, ctx)
        headers = render_template(op.headers or {}, ctx)
        body, content_type = self._build_body(op, ctx)
        if content_type and "Content-Type" not in headers:
            headers["Content-Type"] = content_type

        response = await self._egress.send(
            method=op.method,
            url=url,
            headers=headers,
            body=body,
            retry=manifest.retry_policy,
            limits=manifest.limits,
            breaker_key=breaker_key,
        )

        parsed = self._parse(response)
        success = self._is_success(op, parsed)
        if not success:
            return EngineResult(
                success=False,
                provider_payment_id=None,
                mapped_status=None,
                response_mapping={},
                raw_status_code=response.status_code,
                raw_response=parsed,
                error_code=self._extract(op, parsed, "error_code"),
                error_message=self._extract(op, parsed, "error_message"),
            )

        mapping = {
            key: extract_jsonpath(parsed, expr)
            for key, expr in op.response.mapping.items()
        }
        mapped_status = self._normalize_status(manifest, op, mapping.get("provider_status"))
        return EngineResult(
            success=True,
            provider_payment_id=mapping.get("provider_payment_id"),
            mapped_status=mapped_status,
            response_mapping=mapping,
            raw_status_code=response.status_code,
            raw_response=parsed,
            error_code=None,
            error_message=None,
        )

    # ---- internals

    def _build_context(
        self,
        payment: PaymentInputs,
        secrets: Mapping[str, str],
        op: Operation,
        attempt: int,
        gateway_base_url: str,
    ) -> dict[str, Any]:
        return {
            "payment": {
                "id": str(payment.id),
                "amount_minor": payment.amount.amount_minor,
                "currency": payment.amount.currency,
                "method_token": payment.method_token,
                "customer_ref": payment.customer_ref or "",
                "metadata": payment.metadata,
                "provider_payment_id": payment.provider_payment_id or "",
                "provider_capture_id": payment.provider_capture_id or "",
                "refund_amount_minor": payment.refund_amount_minor or 0,
            },
            "secrets": dict(secrets),
            "idempotency": {
                "client_key": str(payment.idempotency_key),
                "outbound_key": self._render_outbound_key(op, payment, attempt),
                "attempt": attempt,
            },
            "env": {"gateway_base_url": gateway_base_url},
        }

    @staticmethod
    def _render_outbound_key(op: Operation, payment: PaymentInputs, attempt: int) -> str:
        if not op.idempotency or op.idempotency.mechanism == "none":
            return ""
        template = op.idempotency.key_template or "{{ payment.id }}:{{ idempotency.attempt }}"
        # render in a minimal context to avoid recursion
        from ..transformers.templating import render_string_template
        return render_string_template(template, {
            "payment": {"id": str(payment.id)},
            "idempotency": {"attempt": attempt},
        })

    @staticmethod
    def _build_body(op: Operation, ctx: dict[str, Any]) -> tuple[bytes | None, str | None]:
        body = op.body
        if body is None or body.encoding == "none":
            return None, None
        rendered_fields = render_template(body.fields or {}, ctx)
        if body.encoding == "json":
            return json.dumps(rendered_fields).encode(), "application/json"
        if body.encoding == "form":
            return urllib.parse.urlencode(rendered_fields, doseq=True).encode(), \
                "application/x-www-form-urlencoded"
        if body.encoding == "xml" and body.raw_template:
            xml = render_template(body.raw_template, ctx)
            return xml.encode(), "application/xml"
        raise ProviderProtocolError(f"unsupported body encoding: {body.encoding}")

    @staticmethod
    def _parse(response: HttpResponse) -> dict[str, Any]:
        if not response.body:
            return {}
        try:
            return json.loads(response.body)
        except json.JSONDecodeError:
            return {"_raw": response.body.decode("utf-8", errors="replace")}

    @staticmethod
    def _is_success(op: Operation, parsed: dict[str, Any]) -> bool:
        try:
            return evaluate_success_when(op.response.success_when, parsed)
        except Exception:
            return False

    @staticmethod
    def _extract(op: Operation, parsed: dict[str, Any], key: str) -> str | None:
        expr = op.response.mapping.get(key)
        if not expr:
            return None
        v = extract_jsonpath(parsed, expr)
        return None if v is None else str(v)

    @staticmethod
    def _normalize_status(
        manifest: ProviderManifest, op: Operation, provider_status: Any,
    ) -> str:
        if provider_status is None:
            raise ProviderProtocolError("response_mapping must include 'provider_status'")
        key = str(provider_status)
        mapping = op.status_mapping or manifest.status_mapping or {}
        if key not in mapping:
            raise ProviderProtocolError(
                f"unmapped provider status {key!r}; manifest must extend status_mapping"
            )
        return mapping[key]
