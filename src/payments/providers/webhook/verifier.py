from __future__ import annotations

from typing import Mapping

from ..manifest.schema import WebhookSignature


class SignatureVerificationError(Exception): ...


# Closed enum of schemes implemented in-engine:
#   hmac_sha256, hmac_sha512, stripe_v1, paypal_certificate, none
# `none` is rejected at manifest validation in production mode.
# Adding a scheme is an engine PR with threat-model review.


def verify_signature(
    *,
    cfg: WebhookSignature,
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: str | None,
    now: float | None = None,
) -> None:
    """Raises SignatureVerificationError on any failure; never returns False."""
    ...
