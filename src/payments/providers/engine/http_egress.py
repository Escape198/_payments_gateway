from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from ..manifest.schema import Limits, RetryPolicy


class HttpEgressError(Exception):
    def __init__(self, message: str, *, category: str, attempts: int) -> None:
        super().__init__(message)
        self.category = category
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


class CircuitBreaker(Protocol):
    def allow(self, key: str) -> bool: ...
    def record_success(self, key: str) -> None: ...
    def record_failure(self, key: str) -> None: ...


class HttpEgress:
    # Single outbound HTTP component used by the engine.
    # Owns: httpx client, retry orchestration (per manifest RetryPolicy),
    # per-PSP circuit breaker, response size cap, timeout enforcement.
    # Manifests never see this — they declare policy; engine enforces here.

    def __init__(self, client=None, circuit_breaker: CircuitBreaker | None = None) -> None:
        self._client = client
        self._cb = circuit_breaker

    async def send(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        retry: RetryPolicy,
        limits: Limits,
        breaker_key: str,
    ) -> HttpResponse: ...
