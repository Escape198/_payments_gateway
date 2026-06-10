from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManifestValidationError(ValueError): ...


# ---- Pydantic models — used for parsing and semantic validation only.
# Persistence stores the manifest as JSONB; this model is the in-memory shape.


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderInfo(_StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_schema: Literal[1]
    vendor_docs: str | None = None
    contact: str | None = None
    mode: Literal["production", "sandbox"] = "production"


class AuthConfig(_StrictModel):
    type: Literal["bearer", "basic", "api_key_header", "api_key_query",
                  "hmac_request", "oauth2_cc", "mtls"]
    secrets: list[str]
    # bearer
    token_secret: str | None = None
    # basic
    username_secret: str | None = None
    password_secret: str | None = None
    # api_key_*
    header: str | None = None
    param: str | None = None
    secret: str | None = None
    # hmac_request
    algorithm: Literal["sha256", "sha512"] | None = None
    payload: str | None = None
    # oauth2_cc
    token_url: str | None = None
    client_id_secret: str | None = None
    client_secret_secret: str | None = None
    scope: str | None = None
    # mtls
    client_cert_secret: str | None = None
    client_key_secret: str | None = None

    @model_validator(mode="after")
    def _check_type_fields(self) -> "AuthConfig":
        required = {
            "bearer":         {"token_secret"},
            "basic":          {"username_secret", "password_secret"},
            "api_key_header": {"header", "secret"},
            "api_key_query":  {"param", "secret"},
            "hmac_request":   {"algorithm", "header", "secret", "payload"},
            "oauth2_cc":      {"token_url", "client_id_secret", "client_secret_secret"},
            "mtls":           {"client_cert_secret", "client_key_secret"},
        }[self.type]
        missing = [f for f in required if getattr(self, f) is None]
        if missing:
            raise ValueError(f"auth.type={self.type} requires fields: {missing}")
        return self


class RequestBody(_StrictModel):
    encoding: Literal["json", "form", "xml", "none"]
    fields: dict[str, str] | None = None
    raw_template: str | None = None


class ResponseMapping(_StrictModel):
    parser: Literal["jsonpath", "xpath"]
    success_when: str
    mapping: dict[str, str]


class IdempotencyConfig(_StrictModel):
    mechanism: Literal["header", "body", "none"]
    target: str | None = None
    key_template: str | None = None
    server_enforced: bool = False


class Operation(_StrictModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: RequestBody | None = None
    response: ResponseMapping
    status_mapping: dict[str, str] | None = None
    idempotency: IdempotencyConfig | None = None


class WebhookSignature(_StrictModel):
    scheme: Literal["hmac_sha256", "hmac_sha512", "stripe_v1",
                    "paypal_certificate", "none"]
    header: str | None = None
    secret: str | None = None
    timestamp_header: str | None = None
    tolerance_seconds: int = 300


class WebhookConfig(_StrictModel):
    signature: WebhookSignature
    event_id_path: str
    event_type_path: str
    event_mapping: dict[str, str]
    payload_paths: dict[str, str] = Field(default_factory=dict)


class RetryPolicy(_StrictModel):
    max_attempts: int = Field(ge=1, le=20)
    backoff: Literal["exponential", "linear", "fixed"]
    base_ms: int = Field(ge=50, le=60_000)
    cap_ms: int = Field(ge=50, le=600_000)
    jitter: Literal["full", "none"] = "full"
    retry_on: list[str]
    do_not_retry_on: list[str] = Field(default_factory=list)


class Limits(_StrictModel):
    request_timeout_ms: int = 8000
    max_response_bytes: int = 524_288
    outbound_rps: int = 50


_MONEY_OPERATIONS = {"charge", "capture", "refund", "payout"}


class ProviderManifest(_StrictModel):
    provider: ProviderInfo
    auth: AuthConfig
    capabilities: list[str]
    operations: dict[str, Operation]
    webhook: WebhookConfig
    status_mapping: dict[str, str] | None = None
    retry_policy: RetryPolicy
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def _check_semantics(self) -> "ProviderManifest":
        caps = set(self.capabilities)
        ops = set(self.operations.keys())
        missing_ops = caps - ops - {"webhook"}  # webhook is not an operation
        if missing_ops:
            raise ValueError(
                f"capabilities {missing_ops} have no matching operations"
            )

        if self.provider.mode == "production" and self.webhook.signature.scheme == "none":
            raise ValueError(
                "webhook.signature.scheme=none is forbidden for production manifests"
            )

        for op_name in _MONEY_OPERATIONS & ops:
            op = self.operations[op_name]
            mechanism = op.idempotency.mechanism if op.idempotency else "none"
            server_enforced = op.idempotency.server_enforced if op.idempotency else False
            if mechanism == "none" and not server_enforced:
                raise ValueError(
                    f"operation '{op_name}' is a money operation and must declare "
                    f"idempotency.mechanism != none OR idempotency.server_enforced=true"
                )

        return self


def validate_manifest_dict(data: dict[str, Any]) -> ProviderManifest:
    try:
        return ProviderManifest.model_validate(data)
    except Exception as e:
        raise ManifestValidationError(str(e)) from e


def load_manifest(path: Path | str) -> ProviderManifest:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return validate_manifest_dict(data)
