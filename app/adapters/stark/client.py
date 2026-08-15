import starkbank
from app.adapters.stark.config import DestinationAccount, StarkConfig


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
        created = starkbank.transfer.create(
            [starkbank.Transfer(
                amount=amount,
                external_id=external_id,
                tags=[external_id],
                **self._destination.model_dump(),
            )],
            user=self._project,
        )
        return created[0]

    def find_transfer_by_external_id(self, external_id: str):
        """Devolve a Transfer criada com esse external_id, ou None se nunca saiu."""
        found = starkbank.transfer.query(tags=[external_id], limit=1, user=self._project)
        return next(iter(found), None)

    # --- Events ---
    def undelivered_events(self):
        """Eventos que o Stark tentou entregar e não conseguiu (caso A do Fallback)."""
        return starkbank.event.query(is_delivered=False, user=self._project)

    def mark_delivered(self, event_id: str):
        """Tira o evento da fila de não-entregues para ele não voltar a cada rodada."""
        return starkbank.event.update(event_id, is_delivered=True, user=self._project)
