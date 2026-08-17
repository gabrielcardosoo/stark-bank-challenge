import logging

import starkbank
from app.adapters.stark.config import DestinationAccount, StarkConfig

logger = logging.getLogger(__name__)


class StarkClient:
    def __init__(self):
        self._config = StarkConfig()

        self._destination = DestinationAccount.from_file(
            self._config.destination_account_path
        )
        self._project = starkbank.Project(
            id=self._config.project_id,
            environment=self._config.environment,
            private_key=self._config.private_key,
        )
        logger.info(
            "cliente Stark pronto (project=%s, environment=%s)",
            self._config.project_id,
            self._config.environment,
        )

    # --- Invoices ---
    def create_invoices(self, invoices: list[dict]) -> list:
        """Emite um lote de invoices. Cada dict precisa de amount, tax_id e name."""
        return starkbank.invoice.create(
            [starkbank.Invoice(**invoice) for invoice in invoices],
            user=self._project,
        )

    def get_invoice(self, invoice_id: str):
        return starkbank.invoice.get(invoice_id, user=self._project)

    # --- Transfers ---
    def create_transfer(self, *, amount: int, external_id: str):
        """Cria a Transfer do valor líquido para a conta destino.

        O `external_id` também vai em `tags` porque `transfer.query()` não filtra por
        external_id — sem a tag, o Fallback não consegue descobrir se uma Transfer
        `pending` chegou a ser criada.
        """
        # o log vem ANTES da chamada: se ela falhar sem resposta, o rastro da tentativa
        # é a única pista de que dinheiro pode ter se movido
        logger.info(
            "chamando Stark para criar Transfer external_id=%s amount=%d",
            external_id,
            amount,
        )
        created = starkbank.transfer.create(
            [starkbank.Transfer(
                amount=amount,
                external_id=external_id,
                tags=[external_id],
                **self._destination.model_dump(),
            )],
            user=self._project,
        )
        transfer = created[0]
        logger.info(
            "Transfer criada no Stark external_id=%s stark_id=%s",
            external_id,
            transfer.id,
        )
        return transfer

    def find_transfer_by_external_id(self, external_id: str):
        """Devolve a Transfer criada com esse external_id, ou None se nunca saiu."""
        found = starkbank.transfer.query(tags=[external_id], limit=1, user=self._project)
        transfer = next(iter(found), None)
        logger.debug(
            "busca por external_id=%s: %s",
            external_id,
            f"encontrada (stark_id={transfer.id})" if transfer else "não existe no Stark",
        )
        return transfer

    # --- Events ---
    def undelivered_events(self):
        """Eventos que o Stark tentou entregar e não conseguiu (caso A do Fallback)."""
        events = list(starkbank.event.query(is_delivered=False, user=self._project))
        if events:
            logger.warning(
                "%d evento(s) não entregues pelo webhook — recuperando", len(events)
            )
        return events

    def mark_delivered(self, event_id: str):
        """Tira o evento da fila de não-entregues para ele não voltar a cada rodada."""
        logger.debug("marcando evento %s como entregue", event_id)
        return starkbank.event.update(event_id, is_delivered=True, user=self._project)
