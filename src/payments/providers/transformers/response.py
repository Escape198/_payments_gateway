from __future__ import annotations

import operator
import re
from typing import Any

from jsonpath_ng.ext import parse as jsonpath_parse


class ResponseExtractionError(Exception): ...


_PATH_CACHE: dict[str, Any] = {}


def _path(expr: str):
    if expr not in _PATH_CACHE:
        _PATH_CACHE[expr] = jsonpath_parse(expr)
    return _PATH_CACHE[expr]


def extract_jsonpath(payload: Any, expr: str, *, default: Any = None) -> Any:
    matches = _path(expr).find(payload)
    if not matches:
        return default
    if len(matches) == 1:
        return matches[0].value
    return [m.value for m in matches]


# ---- success_when expressions are intentionally restricted.
#
# Supported forms (no eval, no Python expression engine):
#   "$.status == 'succeeded'"
#   "$.status in ['succeeded', 'pending']"
#   "$.code >= 200 and $.code < 300"
#
# We parse with a small grammar so the manifest cannot smuggle arbitrary code
# in via this field.

_TOKEN = re.compile(
    r"""
    \s*(
        \$\.[A-Za-z0-9_.\[\]\?\@'"=\s]+? (?=\s|$|==|!=|<=|>=|<|>|\bin\b|\band\b|\bor\b)
      | '[^']*'
      | "[^"]*"
      | -?\d+(?:\.\d+)?
      | ==|!=|<=|>=|<|>|\band\b|\bor\b|\bin\b
      | \[ | \] | ,
    )\s*
    """,
    re.VERBOSE,
)

_BINOPS = {
    "==": operator.eq, "!=": operator.ne,
    "<":  operator.lt, "<=": operator.le,
    ">":  operator.gt, ">=": operator.ge,
}


def evaluate_success_when(expr: str, payload: Any) -> bool:
    tokens = [m.group(1).strip() for m in _TOKEN.finditer(expr)]
    if not tokens:
        raise ResponseExtractionError(f"empty success_when: {expr!r}")
    return _eval_or(tokens, payload)


def _eval_or(tokens: list[str], payload: Any) -> bool:
    left = _eval_and(tokens, payload)
    while tokens and tokens[0] == "or":
        tokens.pop(0)
        right = _eval_and(tokens, payload)
        left = left or right
    return left


def _eval_and(tokens: list[str], payload: Any) -> bool:
    left = _eval_comparison(tokens, payload)
    while tokens and tokens[0] == "and":
        tokens.pop(0)
        right = _eval_comparison(tokens, payload)
        left = left and right
    return left


def _eval_comparison(tokens: list[str], payload: Any) -> bool:
    lhs = _eval_atom(tokens, payload)
    if not tokens:
        return bool(lhs)
    op = tokens[0]
    if op in _BINOPS:
        tokens.pop(0)
        rhs = _eval_atom(tokens, payload)
        return _BINOPS[op](lhs, rhs)
    if op == "in":
        tokens.pop(0)
        rhs_list = _eval_list(tokens, payload)
        return lhs in rhs_list
    return bool(lhs)


def _eval_list(tokens: list[str], payload: Any) -> list[Any]:
    if not tokens or tokens.pop(0) != "[":
        raise ResponseExtractionError("expected '[' after 'in'")
    items: list[Any] = []
    while tokens and tokens[0] != "]":
        items.append(_eval_atom(tokens, payload))
        if tokens and tokens[0] == ",":
            tokens.pop(0)
    if not tokens or tokens.pop(0) != "]":
        raise ResponseExtractionError("expected ']' to close list")
    return items


def _eval_atom(tokens: list[str], payload: Any) -> Any:
    tok = tokens.pop(0)
    if tok.startswith("$"):
        return extract_jsonpath(payload, tok)
    if (tok.startswith("'") and tok.endswith("'")) or (tok.startswith('"') and tok.endswith('"')):
        return tok[1:-1]
    try:
        if "." in tok:
            return float(tok)
        return int(tok)
    except ValueError:
        raise ResponseExtractionError(f"unexpected token: {tok!r}")
