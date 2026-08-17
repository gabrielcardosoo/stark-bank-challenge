import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.db.models import Invoice, InvoiceStatus, Transfer

logger = logging.getLogger(__name__)


class InvoiceRepository:
    def __init__(self, session: Session):
        self._session = session

    def save_all(self, invoices: list[Invoice]) -> list[Invoice]:
        self._session.add_all(invoices)
        self._session.flush()
        return invoices

    def get_by_stark_id(self, stark_invoice_id: str) -> Invoice | None:
        return self._session.scalar(
            select(Invoice).where(Invoice.stark_invoice_id == stark_invoice_id)
        )

    def update_status(self, stark_invoice_id: str, status: InvoiceStatus) -> None:
        self._session.execute(
            update(Invoice)
            .where(Invoice.stark_invoice_id == stark_invoice_id)
            .values(status=status)
        )

    def mark_credited(
        self,
        stark_invoice_id: str,
        amount_cents: int,
        fee_cents: int,
        transaction_ids: list[str] | None = None,
    ) -> None:
        """Registra o crédito: o que entrou, a taxa e o instante.

        `status` vai para `paid` porque é isso que o Stark reporta no evento de crédito.
        Escrevendo o mesmo valor que o evento `paid` escreveria, a ordem de chegada dos
        dois deixa de importar — os dois convergem para o mesmo estado final.
        """
        self._session.execute(
            update(Invoice)
            .where(Invoice.stark_invoice_id == stark_invoice_id)
            .values(
                status=InvoiceStatus.paid,
                credited_amount_cents=amount_cents,
                fee_cents=fee_cents,
                transaction_ids=transaction_ids,
                credited_at=datetime.now(timezone.utc),
            )
        )
        logger.info(
            "invoice creditada stark_id=%s amount=%d fee=%d liquido=%d",
            stark_invoice_id,
            amount_cents,
            fee_cents,
            amount_cents - fee_cents,
        )

    def credited_without_transfer(self, older_than: timedelta) -> list[Invoice]:
        """Caso B1 do Fallback: creditadas que ninguém reivindicou ainda.

        O critério é `credited_at`, não `status`: a coluna de status pode ser
        sobrescrita por um evento que chegue fora de ordem, `credited_at` não.

        A carência evita disputar com o Worker um invoice que chegou agora e está
        sendo processado neste momento.
        """
        cutoff = datetime.now(timezone.utc) - older_than
        orfas = list(
            self._session.scalars(
                select(Invoice)
                .outerjoin(Transfer, Transfer.invoice_id == Invoice.id)
                .where(
                    Invoice.credited_at.is_not(None),
                    Invoice.credited_at < cutoff,
                    Transfer.id.is_(None),
                )
            )
        )
        if orfas:
            logger.warning(
                "caso B1: %d invoice(s) creditadas sem transfer — reinjetando", len(orfas)
            )
        return orfas
