from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain import (
    IdempotencyKey,
    IllegalStateTransition,
    MerchantId,
    Money,
    Payment,
    PaymentId,
    PaymentStatus,
    ProviderInstanceId,
    Transaction,
)
from ...domain.ids import new_payment_id
from ...domain.status import TransactionKind, TransactionStatus
from ...providers.engine import OperationExecutor, ProviderProtocolError
from ...providers.engine.executor import PaymentInputs
from ...providers.engine.http_egress import HttpEgressError
from ..ports import SecretStore, UnitOfWork


class PaymentCreationFailed(Exception): ...


@dataclass(slots=True, frozen=True)
class CreatePaymentCommand:
    merchant_id: MerchantId
    provider_instance_id: ProviderInstanceId
    amount: Money
    method_token: str
    idempotency_key: IdempotencyKey
    customer_ref: str | None
    metadata: dict[str, Any]


@dataclass(slots=True, frozen=True)
class CreatePaymentResult:
    payment_id: PaymentId
    status: PaymentStatus
    provider_payment_id: str | None
    next_action: dict[str, Any] | None


class CreatePaymentUseCase:
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

    async def execute(self, cmd: CreatePaymentCommand) -> CreatePaymentResult:
        async with self._uow_factory() as uow:
            instance = await uow.providers.get_instance(cmd.provider_instance_id)
            if instance is None or not instance.is_active:
                raise PaymentCreationFailed("provider instance not found or inactive")
            if not instance.supports("charge"):
                raise PaymentCreationFailed("provider instance does not support charge")

            manifest = await uow.providers.load_manifest(
                instance.manifest.code, instance.manifest.version,
            )
            secrets = await self._secrets.get_many(
                instance.secret_ref, manifest.auth.secrets,
            )

            payment = Payment.create(
                id=new_payment_id(),
                merchant_id=cmd.merchant_id,
                provider_instance_id=cmd.provider_instance_id,
                amount=cmd.amount,
                method_token=cmd.method_token,
                idempotency_key=cmd.idempotency_key,
                customer_ref=cmd.customer_ref,
                metadata=cmd.metadata,
            )
            await uow.payments.add(payment)

            tx = Transaction.for_attempt(
                payment_id=payment.id,
                kind=TransactionKind.AUTHORIZE,
                amount=cmd.amount,
                request={"provider_instance": str(cmd.provider_instance_id)},
                attempt=1,
            )

            try:
                result = await self._executor.execute(
                    manifest=manifest,
                    op_name="charge",
                    payment=PaymentInputs(
                        id=payment.id,
                        amount=payment.amount,
                        method_token=payment.method_token,
                        customer_ref=payment.customer_ref,
                        metadata=payment.metadata,
                        idempotency_key=payment.idempotency_key,
                    ),
                    secrets=secrets,
                    attempt=1,
                    gateway_base_url=self._base_url,
                    breaker_key=str(cmd.provider_instance_id),
                )
            except (HttpEgressError, ProviderProtocolError) as e:
                # Settle Transaction as failed, mark Payment failed, persist, commit.
                tx_failed = tx.settle(
                    status=TransactionStatus.FAILED,
                    provider_payment_id=None,
                    response={},
                    error_code=getattr(e, "category", "engine_error"),
                    error_message=str(e),
                )
                await uow.transactions.add(tx_failed)
                payment.fail(code=tx_failed.error_code, message=tx_failed.error_message)
                await uow.payments.update(payment)
                await uow.outbox.add_many(payment.drain_events())
                return CreatePaymentResult(
                    payment_id=payment.id,
                    status=payment.status,
                    provider_payment_id=None,
                    next_action=None,
                )

            # Apply state transition based on engine result.
            mapped = result.mapped_status or "FAILED"
            try:
                self._apply_engine_result(payment, mapped, result)
            except IllegalStateTransition as e:
                raise PaymentCreationFailed(
                    f"engine produced an unreachable transition: {e}"
                ) from e

            tx_settled = tx.settle(
                status=TransactionStatus.SUCCESS if result.success else TransactionStatus.FAILED,
                provider_payment_id=result.provider_payment_id,
                response=result.raw_response,
                error_code=result.error_code,
                error_message=result.error_message,
            )
            await uow.transactions.add(tx_settled)
            await uow.payments.update(payment)
            await uow.outbox.add_many(payment.drain_events())

            return CreatePaymentResult(
                payment_id=payment.id,
                status=payment.status,
                provider_payment_id=result.provider_payment_id,
                next_action=result.response_mapping.get("next_action"),
            )

    @staticmethod
    def _apply_engine_result(payment: Payment, mapped: str, result) -> None:
        if mapped == PaymentStatus.CAPTURED:
            payment.capture(result.provider_payment_id)
        elif mapped == PaymentStatus.AUTHORIZED:
            payment.authorize(result.provider_payment_id or "")
        elif mapped == PaymentStatus.ACTION_REQUIRED:
            payment.mark_action_required(result.response_mapping.get("next_action") or {})
        elif mapped == PaymentStatus.PENDING:
            return  # stay in PENDING; webhook will drive the next transition
        else:
            payment.fail(code=result.error_code, message=result.error_message)
