from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ...domain.ids import ProviderInstanceId
from ...domain.provider import ProviderInstance, ProviderManifestRef
from ...providers.engine import OperationExecutor
from ...providers.engine.executor import PaymentInputs
from ...providers.engine.http_egress import HttpEgressError
from ...providers.manifest import (
    ManifestValidationError,
    ProviderManifest,
    validate_manifest_dict,
)
from ...providers.transformers.templating import render_template
from ..ports import SecretStore, UnitOfWork


class ProviderRegistrationFailed(Exception): ...


@dataclass(slots=True, frozen=True)
class RegisterProviderCommand:
    manifest_dict: dict[str, Any]
    account_alias: str
    secrets: dict[str, str]
    run_healthcheck: bool = True


@dataclass(slots=True, frozen=True)
class RegisterProviderResult:
    provider_code: str
    version: str
    provider_instance_id: ProviderInstanceId


class RegisterProviderUseCase:
    def __init__(
        self,
        uow_factory,
        executor: OperationExecutor,
        secret_store: SecretStore,
        gateway_base_url: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._executor = executor
        self._secrets = secret_store
        self._base_url = gateway_base_url

    async def execute(self, cmd: RegisterProviderCommand) -> RegisterProviderResult:
        try:
            manifest = validate_manifest_dict(cmd.manifest_dict)
        except ManifestValidationError as e:
            raise ProviderRegistrationFailed(f"manifest invalid: {e}") from e

        self._check_secrets_supplied(manifest, cmd.secrets)
        self._dry_run_templates(manifest, cmd.secrets)

        async with self._uow_factory() as uow:
            await uow.providers.upsert_manifest(manifest)

            secret_ref = f"providers/{manifest.provider.code}/{cmd.account_alias}"
            instance = ProviderInstance(
                id=ProviderInstanceId(uuid.uuid4()),
                manifest=ProviderManifestRef(
                    code=manifest.provider.code,
                    version=manifest.provider.version,
                    schema_version=manifest.provider.manifest_schema,
                ),
                account_alias=cmd.account_alias,
                secret_ref=secret_ref,
                is_active=True,
                capabilities=frozenset(manifest.capabilities),
                metadata={},
            )
            await uow.providers.upsert_instance(instance)
            await self._secrets.put_many(secret_ref, cmd.secrets)

        if cmd.run_healthcheck and "healthcheck" in manifest.operations:
            await self._healthcheck(manifest, cmd.secrets, instance.id)

        return RegisterProviderResult(
            provider_code=manifest.provider.code,
            version=manifest.provider.version,
            provider_instance_id=instance.id,
        )

    # ---- helpers

    @staticmethod
    def _check_secrets_supplied(manifest: ProviderManifest, supplied: dict[str, str]) -> None:
        missing = [s for s in manifest.auth.secrets if s not in supplied]
        if missing:
            raise ProviderRegistrationFailed(
                f"missing required secrets: {missing}"
            )

    @staticmethod
    def _dry_run_templates(manifest: ProviderManifest, secrets: dict[str, str]) -> None:
        # synthetic Payment so every template is rendered at least once
        synthetic_ctx = {
            "payment": {
                "id": "00000000-0000-0000-0000-000000000000",
                "amount_minor": 1000, "currency": "USD",
                "method_token": "tok_synth", "customer_ref": "cust_synth",
                "metadata": {}, "provider_payment_id": "ext_synth",
                "provider_capture_id": "cap_synth", "refund_amount_minor": 500,
            },
            "secrets": secrets,
            "idempotency": {"client_key": "k", "outbound_key": "k", "attempt": 1},
            "env": {"gateway_base_url": "https://gateway.test"},
        }
        for name, op in manifest.operations.items():
            try:
                render_template(op.url, synthetic_ctx)
                render_template(op.headers or {}, synthetic_ctx)
                if op.body and op.body.fields:
                    render_template(op.body.fields, synthetic_ctx)
            except Exception as e:
                raise ProviderRegistrationFailed(
                    f"operation '{name}' template failed dry-run: {e}"
                ) from e

    async def _healthcheck(
        self,
        manifest: ProviderManifest,
        secrets: dict[str, str],
        breaker_key: ProviderInstanceId,
    ) -> None:
        from ...domain.money import Money
        try:
            await self._executor.execute(
                manifest=manifest,
                op_name="healthcheck",
                payment=PaymentInputs(
                    id=uuid.UUID(int=0),  # type: ignore[arg-type]
                    amount=Money.of(0, "USD"),
                    method_token="", customer_ref=None, metadata={},
                    idempotency_key="hc",  # type: ignore[arg-type]
                ),
                secrets=secrets,
                attempt=1,
                gateway_base_url=self._base_url,
                breaker_key=str(breaker_key),
            )
        except HttpEgressError as e:
            raise ProviderRegistrationFailed(
                f"healthcheck failed: {e}"
            ) from e
