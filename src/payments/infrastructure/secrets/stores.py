from __future__ import annotations

import asyncio
from typing import Any


class InMemorySecretStore:
    def __init__(self) -> None:
        self._bags: dict[str, dict[str, str]] = {}
        self._lock = asyncio.Lock()

    async def get_many(self, secret_ref: str, names: list[str]) -> dict[str, str]:
        async with self._lock:
            bag = self._bags.get(secret_ref, {})
            return {n: bag[n] for n in names if n in bag}

    async def put_many(self, secret_ref: str, secrets: dict[str, str]) -> None:
        async with self._lock:
            self._bags.setdefault(secret_ref, {}).update(secrets)


class VaultSecretStore:
    def __init__(self, client: Any, mount: str = "kv") -> None:
        self._client = client
        self._mount = mount

    async def get_many(self, secret_ref: str, names: list[str]) -> dict[str, str]:
        # client.secrets.kv.v2.read_secret_version(...)
        data = await self._client.read(f"{self._mount}/data/{secret_ref}")
        bag = data.get("data", {}).get("data", {})
        return {n: bag[n] for n in names if n in bag}

    async def put_many(self, secret_ref: str, secrets: dict[str, str]) -> None:
        await self._client.write(
            f"{self._mount}/data/{secret_ref}",
            {"data": secrets},
        )
