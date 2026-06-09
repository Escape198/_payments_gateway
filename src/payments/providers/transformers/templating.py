from __future__ import annotations

from typing import Any, Mapping


class TemplateSecurityError(Exception): ...


# Jinja2 ImmutableSandboxedEnvironment with an allow-list of filters.
# Anything outside this list raises TemplateSecurityError.
ALLOWED_FILTERS = (
    "lower", "upper", "trim",
    "b64", "b64_url",
    "sha256", "sha512", "hmac_sha256", "hmac_sha512",
    "iso_date", "minor_units", "from_minor_units",
    "json", "urlencode",
)


def render_template(value: Any, context: Mapping[str, Any]) -> Any:
    """Recursively render any string inside a dict/list structure."""
    ...
