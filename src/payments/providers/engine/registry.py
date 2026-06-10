from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..manifest.schema import ProviderManifest


@dataclass(slots=True, frozen=True)
class RegistryEntry:
    code: str
    version: str
    manifest: ProviderManifest


class ProviderRegistry:
    def __init__(
        self,
        *,
        loader: Callable[[str, str], Awaitable[ProviderManifest]],
        invalidation: Callable[[Callable[[str, str], Awaitable[None]]], None] | None = None,
    ) -> None:
        self._loader = loader
        self._cache: dict[tuple[str, str], RegistryEntry] = {}
        self._active_version: dict[str, str] = {}  # code -> active version
        self._lock = asyncio.Lock()
        if invalidation is not None:
            invalidation(self._on_invalidate)

    async def get_active(self, code: str) -> ProviderManifest:
        version = self._active_version.get(code)
        if version is None:
            raise KeyError(f"no active manifest for provider {code!r}")
        return await self.get(code, version)

    async def get(self, code: str, version: str) -> ProviderManifest:
        key = (code, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached.manifest
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached.manifest
            manifest = await self._loader(code, version)
            self._cache[key] = RegistryEntry(code, version, manifest)
            self._active_version.setdefault(code, version)
            return manifest

    def set_active(self, code: str, version: str) -> None:
        self._active_version[code] = version

    async def _on_invalidate(self, code: str, version: str) -> None:
        async with self._lock:
            self._cache.pop((code, version), None)
            # next get() lazily reloads
