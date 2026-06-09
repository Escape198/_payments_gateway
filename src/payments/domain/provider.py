from __future__ import annotations

from dataclasses import dataclass

from .ids import ProviderInstanceId


@dataclass(frozen=True, slots=True)
class ProviderManifestRef:
    code: str
    version: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class ProviderInstance:
    id: ProviderInstanceId
    manifest: ProviderManifestRef
    account_alias: str
    secret_ref: str
    is_active: bool
    capabilities: frozenset[str]
