"""Emite um lote de invoices. Roda a cada 3 horas, 8 vezes em 24h.

Execução pontual: monta as dependências, dispara o service e termina.
"""
import logging
import sys

from app.adapters.db.connector import session_scope
from app.adapters.db.repositories import InvoiceRepository
from app.adapters.stark import StarkClient
from app.business_rules import PeopleGenerator
from app.logger import Logger
from app.services.issue_invoices import IssueInvoices

logger = logging.getLogger(__name__)


def main() -> int:
    Logger(file="logs/issuer.log")
    logger.info("issuer iniciado")

    try:
        stark = StarkClient()
        with session_scope() as session:
            IssueInvoices(
                stark=stark,
                invoices_database=InvoiceRepository(session),
                people=PeopleGenerator(),
            ).execute()
    except Exception:
        # sem isto, o cron falha silenciosamente e o lote some sem rastro
        logger.exception("issuer falhou")
        return 1

    logger.info("issuer concluído")
    return 0


if __name__ == "__main__":
    sys.exit(main())
