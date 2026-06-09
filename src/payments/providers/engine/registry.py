from __future__ import annotations

from typing import Awaitable, Callable

from ..manifest.schema import ProviderManifest


class ProviderRegistry:
    # In-process cache of active manifests.
    # Source of truth = Postgres.
    # Invalidation = Redis pub/sub channel; on POST/PATCH /v1/providers,
    # all API and worker replicas reload the affected manifest in ms.
    # No restart, no manual cache bust.

    def __init__(
        self,
        loader: Callable[[str, str], Awaitable[ProviderManifest]],
        invalidation: Callable | None = None,
    ) -> None:
        self._loader = loader
        self._cache: dict[tuple[str, str], ProviderManifest] = {}
        self._active_version: dict[str, str] = {}

    async def get_active(self, code: str) -> ProviderManifest: ...
    async def get(self, code: str, version: str) -> ProviderManifest: ...
    def set_active(self, code: str, version: str) -> None: ...
