import logging

from app.adapters.db.models import InvoiceStatus

logger = logging.getLogger(__name__)

LOG_TYPE_CREDITED = "credited"

LOG_TYPES_IGNORADOS = {"created"}

# log.type do Stark -> status na nossa tabela.
STATUS_POR_LOG_TYPE = {
    "paid": InvoiceStatus.paid,
    "overdue": InvoiceStatus.overdue,
    "expired": InvoiceStatus.expired,
    "canceled": InvoiceStatus.canceled,
}


class ReceiveEvent:
    def __init__(self, events, invoices, producer):
        self._events = events
        self._invoices = invoices
        self._producer = producer

    def execute(self, event, payload: dict) -> bool:
        """Processa o evento autenticado. False se era reentrega (nada a fazer).

        `payload` é o corpo cru já convertido em dict — guardado como evidência.
        Falha ao publicar na fila NÃO levanta exceção: o evento está persistido e o
        Fallback recupera. Levantar viraria 500 e o Stark reentregaria sem necessidade.
        """
        event_id = str(event.id)

        log = getattr(event, "log", None)
        log_type = getattr(log, "type", "") or ""
        invoice = getattr(log, "invoice", None)
        invoice_id = str(invoice.id) if invoice is not None else None

        novo = self._events.insert_if_new(
            stark_event_id=event_id,
            subscription=getattr(event, "subscription", "") or "",
            log_type=log_type,
            payload=payload,
            stark_invoice_id=invoice_id,
        )
        if not novo:
            return False

        if invoice is None:
            logger.error("evento %s (%s) não trouxe invoice", event_id, log_type)
            return True

        if log_type == LOG_TYPE_CREDITED:
            logger.info("evento %s (%s) credita invoice %s", event_id, log_type, invoice.id)
            self._creditar(event_id, invoice)
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

    def _creditar(self, event_id: str, invoice) -> None:
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
