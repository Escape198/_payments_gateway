from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ManifestValidationError(ValueError): ...


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderInfo(_Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_schema: Literal[1]
    mode: Literal["production", "sandbox"] = "production"
    vendor_docs: str | None = None
    contact: str | None = None


class AuthConfig(_Strict):
    type: Literal["bearer", "basic", "api_key_header", "api_key_query",
                  "hmac_request", "oauth2_cc", "mtls"]
    secrets: list[str]
    # per-type fields, see docs/manifest-spec.md:
    token_secret: str | None = None
    username_secret: str | None = None
    password_secret: str | None = None
    header: str | None = None
    param: str | None = None
    secret: str | None = None
    algorithm: Literal["sha256", "sha512"] | None = None
    payload: str | None = None
    token_url: str | None = None
    client_id_secret: str | None = None
    client_secret_secret: str | None = None
    scope: str | None = None
    client_cert_secret: str | None = None
    client_key_secret: str | None = None


class RequestBody(_Strict):
    encoding: Literal["json", "form", "xml", "none"]
    fields: dict[str, str] | None = None
    raw_template: str | None = None


class ResponseMapping(_Strict):
    parser: Literal["jsonpath", "xpath"]
    success_when: str
    mapping: dict[str, str]


class IdempotencyConfig(_Strict):
    mechanism: Literal["header", "body", "none"]
    target: str | None = None
    key_template: str | None = None
    server_enforced: bool = False


class Operation(_Strict):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: RequestBody | None = None
    response: ResponseMapping
    status_mapping: dict[str, str] | None = None
    idempotency: IdempotencyConfig | None = None


class WebhookSignature(_Strict):
    scheme: Literal["hmac_sha256", "hmac_sha512", "stripe_v1",
                    "paypal_certificate", "none"]
    header: str | None = None
    secret: str | None = None
    timestamp_header: str | None = None
    tolerance_seconds: int = 300


class WebhookConfig(_Strict):
    signature: WebhookSignature
    event_id_path: str
    event_type_path: str
    event_mapping: dict[str, str]
    payload_paths: dict[str, str] = Field(default_factory=dict)


class RetryPolicy(_Strict):
    max_attempts: int = Field(ge=1, le=20)
    backoff: Literal["exponential", "linear", "fixed"]
    base_ms: int
    cap_ms: int
    jitter: Literal["full", "none"] = "full"
    retry_on: list[str]
    do_not_retry_on: list[str] = Field(default_factory=list)


class Limits(_Strict):
    request_timeout_ms: int = 8000
    max_response_bytes: int = 524_288
    outbound_rps: int = 50


class ProviderManifest(_Strict):
    provider: ProviderInfo
    auth: AuthConfig
    capabilities: list[str]
    operations: dict[str, Operation]
    webhook: WebhookConfig
    status_mapping: dict[str, str] | None = None
    retry_policy: RetryPolicy
    limits: Limits = Field(default_factory=Limits)

    # semantic invariants enforced on top of the Pydantic schema:
    # - mode=production ⇒ webhook.signature.scheme != "none"
    # - every money op (charge/capture/refund/payout) declares idempotency
    # - capabilities ⊆ operations.keys() ∪ {"webhook"}


def validate_manifest_dict(data: dict) -> ProviderManifest: ...
def load_manifest(path: Path | str) -> ProviderManifest: ...
