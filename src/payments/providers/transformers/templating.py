from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Mapping

from jinja2.sandbox import ImmutableSandboxedEnvironment

from ...domain.money import minor_units_for


class TemplateSecurityError(Exception): ...


_ALLOWED_FILTERS: dict[str, Any] = {}


def _register(name: str):
    def deco(fn):
        _ALLOWED_FILTERS[name] = fn
        return fn
    return deco


@_register("lower")
def _f_lower(s: str) -> str:
    return str(s).lower()


@_register("upper")
def _f_upper(s: str) -> str:
    return str(s).upper()


@_register("trim")
def _f_trim(s: str) -> str:
    return str(s).strip()


@_register("b64")
def _f_b64(s: str | bytes) -> str:
    raw = s.encode() if isinstance(s, str) else s
    return base64.b64encode(raw).decode()


@_register("b64_url")
def _f_b64_url(s: str | bytes) -> str:
    raw = s.encode() if isinstance(s, str) else s
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@_register("sha256")
def _f_sha256(s: str | bytes) -> str:
    raw = s.encode() if isinstance(s, str) else s
    return hashlib.sha256(raw).hexdigest()


@_register("sha512")
def _f_sha512(s: str | bytes) -> str:
    raw = s.encode() if isinstance(s, str) else s
    return hashlib.sha512(raw).hexdigest()


@_register("hmac_sha256")
def _f_hmac_sha256(payload: str | bytes, key: str | bytes) -> str:
    p = payload.encode() if isinstance(payload, str) else payload
    k = key.encode() if isinstance(key, str) else key
    return hmac.new(k, p, hashlib.sha256).hexdigest()


@_register("hmac_sha512")
def _f_hmac_sha512(payload: str | bytes, key: str | bytes) -> str:
    p = payload.encode() if isinstance(payload, str) else payload
    k = key.encode() if isinstance(key, str) else key
    return hmac.new(k, p, hashlib.sha512).hexdigest()


@_register("iso_date")
def _f_iso_date(ts: int | float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


@_register("minor_units")
def _f_minor_units(amount: float | str, currency: str) -> int:
    scale = minor_units_for(currency)
    return int(round(float(amount) * (10 ** scale)))


@_register("from_minor_units")
def _f_from_minor_units(amount_minor: int, currency: str) -> str:
    scale = minor_units_for(currency)
    if scale == 0:
        return str(int(amount_minor))
    whole, frac = divmod(int(amount_minor), 10 ** scale)
    return f"{whole}.{frac:0{scale}d}"


@_register("json")
def _f_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@_register("urlencode")
def _f_urlencode(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _make_env() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(
        autoescape=False,
        keep_trailing_newline=False,
        finalize=lambda v: "" if v is None else v,
    )
    # Wipe Jinja's default filter set and replace with allow-list.
    env.filters.clear()
    env.filters.update(_ALLOWED_FILTERS)
    # No globals, no tests beyond defaults.
    env.globals.clear()
    return env


_ENV = _make_env()


def render_string_template(template: str, context: Mapping[str, Any]) -> str:
    try:
        return _ENV.from_string(template).render(**context)
    except Exception as e:
        raise TemplateSecurityError(f"Template render failed: {e}") from e


def render_template(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return render_string_template(value, context)
    if isinstance(value, Mapping):
        return {k: render_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, context) for v in value]
    return value
