from __future__ import annotations

from typing import Any


class InMemorySecretStore:
    # dev/test only — never use in production
    def __init__(self) -> None:
        self._bags: dict[str, dict[str, str]] = {}

    async def get_many(self, secret_ref: str, names: list[str]) -> dict[str, str]: ...
    async def put_many(self, secret_ref: str, secrets: dict[str, str]) -> None: ...


class VaultSecretStore:
    # KV v2 for raw secrets; Transit engine for HMAC/sign where the raw key
    # must never enter the app process.
    def __init__(self, client: Any, mount: str = "kv") -> None:
        self._client = client
        self._mount = mount

    async def get_many(self, secret_ref: str, names: list[str]) -> dict[str, str]: ...
    async def put_many(self, secret_ref: str, secrets: dict[str, str]) -> None: ...
