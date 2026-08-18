"""Fallback: recupera o que o caminho normal deixou passar. Roda a cada 30 minutos.

Execução pontual — monta as dependências, reconcilia e termina.
"""
import logging
import sys

from starkcore.utils.api import api_json

from app.adapters.db.connector import session_scope
from app.adapters.db.repositories import (
    InvoiceRepository,
    TransferRepository,
    WebhookEventRepository,
)
from app.adapters.redpanda import CreditedInvoiceProducer
from app.adapters.stark import StarkClient
from app.logger import Logger
from app.services.receive_event import ReceiveEvent
from app.services.reconcile import Reconcile

logger = logging.getLogger(__name__)


def main() -> int:
    Logger(file="logs/reconciler.log")
    logger.info("fallback iniciado")

    try:
        stark = StarkClient()
        producer = CreditedInvoiceProducer()

        with session_scope() as session:
            events = WebhookEventRepository(session)
            invoices = InvoiceRepository(session)
            transfers = TransferRepository(session)

            resultado = Reconcile(
                stark=stark,
                invoices=invoices,
                transfers=transfers,
                producer=producer,
                receive_event=ReceiveEvent(events, invoices, transfers, producer),
                # converte o objeto do SDK no mesmo formato que o webhook guardaria
                serializar=api_json,
            ).execute()

    except Exception:
        logger.exception("fallback falhou")
        return 1

    total = sum(resultado.values())
    if total:
        logger.warning(
            "fallback recuperou: A=%d (evento perdido) B1=%d (sem transfer) "
            "B2=%d (pending)",
            resultado["A"],
            resultado["B1"],
            resultado["B2"],
        )
    else:
        logger.info("fallback: nada a recuperar")

    return 0


if __name__ == "__main__":
    sys.exit(main())
