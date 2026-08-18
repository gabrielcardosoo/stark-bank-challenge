"""Registra o evento recebido e encaminha conforme a assinatura.

Recebe o evento **já autenticado** — a validação da assinatura é da borda HTTP, feita
no entrypoint. Aqui não há autenticação, nem chamada à API do Stark.

Duas assinaturas:
  - `invoice`  — o crédito enfileira a Transfer
  - `transfer` — o desfecho da Transfer atualiza o status local

A segunda existe porque **aceita pelo Stark não é dinheiro entregue**: uma Transfer pode
ir de `created` a `failed` minutos depois. Sem escutar isso, o banco diria `created` para
sempre e a conciliação fecharia falsamente.
"""
import logging

from app.adapters.db.models import InvoiceStatus, TransferStatus

logger = logging.getLogger(__name__)

SUBSCRIPTION_INVOICE = "invoice"
SUBSCRIPTION_TRANSFER = "transfer"

LOG_TYPE_CREDITED = "credited"

# `created` é ignorado de propósito: o issuer já gravou a invoice com esse status no
# momento da emissão, e um `created` atrasado só teria o efeito de regredir o estado.
LOG_TYPES_IGNORADOS = {"created"}

STATUS_POR_LOG_TYPE = {
    "paid": InvoiceStatus.paid,
    "overdue": InvoiceStatus.overdue,
    "expired": InvoiceStatus.expired,
    "canceled": InvoiceStatus.canceled,
}

STATUS_POR_TRANSFER_LOG = {
    "created": TransferStatus.created,
    "processing": TransferStatus.processing,
    "sending": TransferStatus.processing,
    "success": TransferStatus.success,
    "failed": TransferStatus.failed,
    "canceled": TransferStatus.canceled,
}


class ReceiveEvent:
    def __init__(self, events, invoices, transfers, producer):
        self._events = events
        self._invoices = invoices
        self._transfers = transfers
        self._producer = producer

    def execute(self, event, payload: dict) -> bool:
        """Processa o evento autenticado. False se era reentrega (nada a fazer)."""
        event_id = str(event.id)
        subscription = getattr(event, "subscription", "") or ""

        log = getattr(event, "log", None)
        log_type = getattr(log, "type", "") or ""
        invoice = getattr(log, "invoice", None)
        invoice_id = str(invoice.id) if invoice is not None else None

        novo = self._events.insert_if_new(
            stark_event_id=event_id,
            subscription=subscription,
            log_type=log_type,
            payload=payload,
            stark_invoice_id=invoice_id,
        )
        if not novo:
            return False

        if subscription == SUBSCRIPTION_TRANSFER:
            logger.info("evento %s de transfer %s", event_id, log_type)
            self._tratar_transfer(event_id, log_type, getattr(log, "transfer", None))
            return True

        if subscription != SUBSCRIPTION_INVOICE:
            logger.warning("assinatura desconhecida '%s' — evento ignorado", subscription)
            self._events.mark_processed(event_id)
            return True

        # invoice.Log sempre traz o invoice; chegar aqui significa payload inesperado.
        # Fica sem `processed_at` de propósito, para deixar rastro de investigação.
        if invoice is None:
            logger.error("evento %s (%s) não trouxe invoice", event_id, log_type)
            return True

        if log_type == LOG_TYPE_CREDITED:
            logger.info("evento %s credita invoice %s", event_id, invoice.id)
            self._creditar(invoice)
            self._enfileirar(event_id, invoice)
            return True

        if log_type in LOG_TYPES_IGNORADOS:
            logger.debug("evento %s (%s) ignorado", event_id, log_type)
            self._events.mark_processed(event_id)
            return True

        status = STATUS_POR_LOG_TYPE.get(log_type)
        if status is None:
            logger.warning("log_type desconhecido '%s' — evento ignorado", log_type)
            self._events.mark_processed(event_id)
            return True

        self._invoices.update_status(invoice_id, status)
        logger.info("evento %s (%s) não gera Transfer", event_id, log_type)
        self._events.mark_processed(event_id)
        return True

    def _tratar_transfer(self, event_id: str, log_type: str, transfer) -> None:
        """Aplica o desfecho da Transfer. Nunca cria nem repete transferência."""
        if transfer is None:
            logger.error("evento %s de transfer não trouxe a transfer", event_id)
            return

        status = STATUS_POR_TRANSFER_LOG.get(log_type)
        if status is None:
            logger.warning(
                "log_type de transfer desconhecido '%s' — evento ignorado", log_type
            )
            self._events.mark_processed(event_id)
            return

        if status is TransferStatus.failed:
            # o dinheiro NÃO saiu. Fica registrado para a conciliação acusar a diferença;
            # repetir automaticamente exigiria saber o motivo da recusa.
            erros = [getattr(e, "code", str(e)) for e in (getattr(transfer, "errors", None) or [])]
            logger.error(
                "Transfer %s FALHOU no Stark: %s — dinheiro não foi transferido",
                transfer.id,
                erros or "sem detalhe",
            )

        self._transfers.update_status_by_stark_id(str(transfer.id), status)
        self._events.mark_processed(event_id)

    def _creditar(self, invoice) -> None:
        # o amount do evento de crédito já inclui multa e juros; nunca use o valor
        # emitido para calcular o que transferir
        self._invoices.mark_credited(
            str(invoice.id),
            amount_cents=invoice.amount,
            fee_cents=invoice.fee,
            transaction_ids=list(getattr(invoice, "transaction_ids", None) or []),
        )

    def _enfileirar(self, event_id: str, invoice) -> None:
        try:
            self._producer.publish(
                stark_invoice_id=str(invoice.id),
                amount=invoice.amount,
                fee=invoice.fee,
                event_id=event_id,
            )
        except Exception:
            # dual write: o INSERT foi commitado e o publish falhou. Não pode virar
            # 5xx — o Stark reentregaria. O Fallback (caso B1) recupera o órfão.
            logger.exception(
                "evento %s persistido mas NÃO publicado (invoice=%s) — Fallback recupera",
                event_id,
                invoice.id,
            )
            return

        self._events.mark_processed(event_id)
