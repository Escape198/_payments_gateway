from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Mapping, Protocol

import httpx

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
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=None)
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
    ) -> HttpResponse:
        if self._cb and not self._cb.allow(breaker_key):
            raise HttpEgressError(
                "circuit open", category="circuit_open", attempts=0,
            )

        last_error: Exception | None = None
        last_category: str = "unknown"

        for attempt in range(1, retry.max_attempts + 1):
            try:
                resp = await self._send_once(method, url, headers, body, limits)
            except httpx.TimeoutException as e:
                last_error, last_category = e, "timeout"
            except httpx.TransportError as e:
                last_error, last_category = e, "network_error"
            else:
                category = self._classify(resp.status_code)
                if category is None:
                    if self._cb:
                        self._cb.record_success(breaker_key)
                    return resp
                last_error = HttpEgressError(
                    f"PSP returned {resp.status_code}",
                    category=category, attempts=attempt,
                )
                last_category = category

            if not self._should_retry(last_category, retry):
                break
            if attempt >= retry.max_attempts:
                break
            await asyncio.sleep(self._backoff_seconds(attempt, retry))

        if self._cb:
            self._cb.record_failure(breaker_key)
        raise HttpEgressError(
            f"egress failed: {last_error}",
            category=last_category, attempts=retry.max_attempts,
        )

    async def _send_once(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        limits: Limits,
    ) -> HttpResponse:
        timeout = httpx.Timeout(limits.request_timeout_ms / 1000.0)
        async with self._client.stream(
            method, url, headers=dict(headers), content=body, timeout=timeout,
        ) as resp:
            raw = bytearray()
            async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                raw.extend(chunk)
                if len(raw) > limits.max_response_bytes:
                    raise HttpEgressError(
                        "response exceeds max_response_bytes",
                        category="oversize", attempts=1,
                    )
            return HttpResponse(
                status_code=resp.status_code,
                headers={k: v for k, v in resp.headers.items()},
                body=bytes(raw),
            )

    @staticmethod
    def _classify(status_code: int) -> str | None:
        if 200 <= status_code < 300:
            return None
        if status_code == 429:
            return "http_429"
        if 500 <= status_code < 600:
            return "http_5xx"
        return f"http_{status_code}"

    @staticmethod
    def _should_retry(category: str, retry: RetryPolicy) -> bool:
        if category in retry.do_not_retry_on:
            return False
        return category in retry.retry_on

    @staticmethod
    def _backoff_seconds(attempt: int, retry: RetryPolicy) -> float:
        if retry.backoff == "fixed":
            delay = retry.base_ms
        elif retry.backoff == "linear":
            delay = retry.base_ms * attempt
        else:  # exponential
            delay = retry.base_ms * (2 ** (attempt - 1))
        delay = min(delay, retry.cap_ms)
        if retry.jitter == "full":
            delay = random.uniform(0, delay)
        return delay / 1000.0
