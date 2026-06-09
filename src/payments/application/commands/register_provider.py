from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.ids import ProviderInstanceId
from ...providers.engine import OperationExecutor
from ..ports import SecretStore


class ProviderRegistrationFailed(Exception): ...


@dataclass(frozen=True, slots=True)
class RegisterProviderCommand:
    manifest_dict: dict[str, Any]
    account_alias: str
    secrets: dict[str, str]
    run_healthcheck: bool = True


@dataclass(frozen=True, slots=True)
class RegisterProviderResult:
    provider_code: str
    version: str
    provider_instance_id: ProviderInstanceId


class RegisterProviderUseCase:
    # 1. JSON Schema + Pydantic + semantic validation (reject bad manifests early)
    # 2. check all declared secrets are supplied
    # 3. dry-run: render every operation against a synthetic Payment
    # 4. optional healthcheck against the PSP
    # 5. persist manifest (immutable row) + provider_instance + secrets in Vault
    # 6. publish invalidation message → other replicas reload the registry

    def __init__(self, uow_factory, executor: OperationExecutor,
                 secrets: SecretStore, gateway_base_url: str) -> None: ...

    async def execute(self, cmd: RegisterProviderCommand) -> RegisterProviderResult: ...
