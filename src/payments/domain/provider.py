from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ids import ProviderInstanceId


@dataclass(slots=True, frozen=True)
class ProviderManifestRef:
    code: str
    version: str
    schema_version: int


@dataclass(slots=True, frozen=True)
class ProviderInstance:
    id: ProviderInstanceId
    manifest: ProviderManifestRef
    account_alias: str
    secret_ref: str
    is_active: bool
    capabilities: frozenset[str]
    metadata: dict[str, Any]

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities
