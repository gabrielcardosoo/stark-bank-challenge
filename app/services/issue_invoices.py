"""Emite o lote de invoices de uma execução do cron.

Não importa `starkbank` nem o ORM: recebe as dependências prontas do entrypoint.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.adapters.db.models import Invoice, InvoiceStatus

logger = logging.getLogger(__name__)

# vencimento curto para o sandbox pagar dentro da janela do desafio
DEFAULT_DUE = timedelta(hours=1)
DEFAULT_EXPIRATION = timedelta(hours=6)


class IssueInvoices:
    def __init__(self, 
        stark, invoices_database, people, 
        due: timedelta = DEFAULT_DUE, expiration: timedelta = DEFAULT_EXPIRATION):
        self._stark = stark
        self._invoices = invoices_database
        self._people = people
        self._due = due
        self._expiration = expiration

    def execute(self) -> list[Invoice]:
        pessoas = self._people.batch()
        logger.info("emitindo lote de %d invoices", len(pessoas))

        criadas = []
        for pessoa in pessoas:
            # uma falha isolada não pode abortar o lote inteiro
            try:
                criadas.extend(self._stark.create_invoices([self._payload(pessoa)]))
            except Exception:
                logger.exception("falha ao emitir invoice para %s", pessoa["tax_id"])

        salvas = self._invoices.save_all([self._to_row(i) for i in criadas])
        logger.info("%d de %d invoices emitidas", len(salvas), len(pessoas))
        return salvas

    def _payload(self, pessoa: dict) -> dict:
        return {
            "amount": pessoa["amount"],
            "name": pessoa["name"],
            "tax_id": pessoa["tax_id"],
            "due": datetime.now(timezone.utc) + self._due,
            "expiration": int(self._expiration.total_seconds()),
        }

    def _to_row(self, invoice) -> Invoice:
        return Invoice(
            stark_invoice_id=str(invoice.id),
            customer_name=invoice.name,
            customer_tax_id=invoice.tax_id,
            nominal_amount_cents=invoice.amount,
            status=InvoiceStatus.created,
            due=invoice.due,
        )
