"""Consome `invoices.credited` e cria as Transfers. Processo longo, fica no ar.

O offset é commitado **depois** de cada mensagem processada. Ver `ProcessCredited`
para a distinção entre falha antes e depois de reivindicar o trabalho.
"""
import logging
import signal
import sys

from app.adapters.db.connector import session_scope
from app.adapters.db.repositories import InvoiceRepository, TransferRepository
from app.adapters.redpanda import CreditedInvoiceConsumer
from app.adapters.stark import StarkClient
from app.logger import Logger
from app.services.process_credited import ProcessCredited

logger = logging.getLogger(__name__)


def _encerrar(signum, _frame):
    """Converte SIGTERM em KeyboardInterrupt.

    Sem isto, o `docker stop` mata o processo no meio do poll e o consumer só sai do
    grupo quando o broker expira a sessão — atrasando o rebalanceamento.
    """
    logger.info("sinal %s recebido, encerrando", signal.Signals(signum).name)
    raise KeyboardInterrupt


def main() -> int:
    Logger(file="logs/worker.log")
    signal.signal(signal.SIGTERM, _encerrar)
    logger.info("worker iniciado")

    try:
        stark = StarkClient()
    except Exception:
        logger.exception("worker não conseguiu iniciar")
        return 1

    processadas = 0
    try:
        with CreditedInvoiceConsumer() as consumer:
            for mensagem in consumer.messages():
                # uma transação por mensagem: um erro não contamina a próxima
                with session_scope() as session:
                    criou = ProcessCredited(
                        invoices=InvoiceRepository(session),
                        transfers=TransferRepository(session),
                        stark=stark,
                    ).execute(mensagem)

                # só depois do commit da transação — se o processo morrer antes daqui,
                # a mensagem volta e a idempotência absorve
                consumer.commit()
                processadas += 1
                if criou:
                    logger.info(
                        "Transfer criada para invoice %s", mensagem["stark_invoice_id"]
                    )
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("worker falhou")
        return 1

    logger.info("worker encerrado (%d mensagens processadas)", processadas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
