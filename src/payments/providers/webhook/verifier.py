from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Mapping

from ..manifest.schema import WebhookSignature


class SignatureVerificationError(Exception): ...


def verify_signature(
    *,
    cfg: WebhookSignature,
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: str | None,
    now: float | None = None,
) -> None:
    now = now or time.time()
    scheme = cfg.scheme

    if scheme == "none":
        return  # only allowed in sandbox manifests; engine rejects in prod

    if secret is None:
        raise SignatureVerificationError("missing secret material for webhook scheme")

    if scheme == "hmac_sha256":
        _verify_plain_hmac(cfg, raw_body, headers, secret, "sha256", now)
        return
    if scheme == "hmac_sha512":
        _verify_plain_hmac(cfg, raw_body, headers, secret, "sha512", now)
        return
    if scheme == "stripe_v1":
        _verify_stripe_v1(cfg, raw_body, headers, secret, now)
        return
    if scheme == "paypal_certificate":
        _verify_paypal_certificate(cfg, raw_body, headers, secret, now)
        return

    raise SignatureVerificationError(f"unsupported scheme: {scheme}")


def _verify_plain_hmac(
    cfg: WebhookSignature, body: bytes, headers: Mapping[str, str],
    secret: str, algo: str, now: float,
) -> None:
    if not cfg.header:
        raise SignatureVerificationError("scheme=hmac_* requires header")
    received = headers.get(cfg.header) or headers.get(cfg.header.lower())
    if not received:
        raise SignatureVerificationError(f"missing header {cfg.header}")

    if cfg.timestamp_header:
        ts_raw = headers.get(cfg.timestamp_header) or headers.get(cfg.timestamp_header.lower())
        if not ts_raw:
            raise SignatureVerificationError(f"missing header {cfg.timestamp_header}")
        try:
            ts = int(ts_raw)
        except ValueError as e:
            raise SignatureVerificationError(f"bad timestamp: {ts_raw!r}") from e
        if abs(now - ts) > cfg.tolerance_seconds:
            raise SignatureVerificationError("timestamp outside tolerance window")
        signed = f"{ts}.".encode() + body
    else:
        signed = body

    expected = hmac.new(secret.encode(), signed, getattr(hashlib, algo)).hexdigest()
    if not hmac.compare_digest(expected, received.strip()):
        raise SignatureVerificationError("signature mismatch")


_STRIPE_SIG = re.compile(r"(?:^|,)\s*(t|v1|v0)=([A-Za-z0-9]+)")


def _verify_stripe_v1(
    cfg: WebhookSignature, body: bytes, headers: Mapping[str, str],
    secret: str, now: float,
) -> None:
    raw = headers.get("Stripe-Signature") or headers.get("stripe-signature")
    if not raw:
        raise SignatureVerificationError("missing Stripe-Signature header")

    parts = dict(_STRIPE_SIG.findall(raw))
    ts_raw, v1 = parts.get("t"), parts.get("v1")
    if not (ts_raw and v1):
        raise SignatureVerificationError("malformed Stripe-Signature")

    ts = int(ts_raw)
    if abs(now - ts) > cfg.tolerance_seconds:
        raise SignatureVerificationError("timestamp outside tolerance window")

    signed_payload = f"{ts}.".encode() + body
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise SignatureVerificationError("Stripe-v1 signature mismatch")


def _verify_paypal_certificate(
    cfg: WebhookSignature, body: bytes, headers: Mapping[str, str],
    secret: str, now: float,
) -> None:
    # Real implementation talks to PayPal's webhook verification endpoint
    # (POST /v1/notifications/verify-webhook-signature). Tactically that means:
    #   - submit cert headers + transmission id + body + webhook_id to PayPal
    #   - require {"verification_status":"SUCCESS"} response
    # For the engine reference, we mark this as not-implemented; production
    # builds wire the real verifier with a circuit breaker around the call.
    raise SignatureVerificationError(
        "paypal_certificate verification is implemented via PayPal's verification "
        "endpoint; not available in the reference engine"
    )
