from __future__ import annotations

from typing import Any


def extract_jsonpath(payload: Any, expr: str, *, default: Any = None) -> Any: ...


# A small grammar — NOT eval. Supported forms:
#   "$.status == 'succeeded'"
#   "$.status in ['succeeded', 'pending']"
#   "$.code >= 200 and $.code < 300"
def evaluate_success_when(expr: str, payload: Any) -> bool: ...
