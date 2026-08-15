from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.db.models import Invoice, InvoiceStatus, Transfer


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
        self, stark_invoice_id: str, amount_cents: int, fee_cents: int
    ) -> None:
        """Registra o que efetivamente entrou — pode diferir do nominal por multa/juros."""
        self._session.execute(
            update(Invoice)
            .where(Invoice.stark_invoice_id == stark_invoice_id)
            .values(
                status=InvoiceStatus.credited,
                credited_amount_cents=amount_cents,
                fee_cents=fee_cents,
                credited_at=datetime.now(timezone.utc),
            )
        )

    def credited_without_transfer(self, older_than: timedelta) -> list[Invoice]:
        """Caso B1 do Fallback: creditadas que ninguém reivindicou ainda.

        A carência evita disputar com o Worker um invoice que chegou agora e está
        sendo processado neste momento.
        """
        cutoff = datetime.now(timezone.utc) - older_than
        return list(
            self._session.scalars(
                select(Invoice)
                .outerjoin(Transfer, Transfer.invoice_id == Invoice.id)
                .where(
                    Invoice.status == InvoiceStatus.credited,
                    Invoice.credited_at < cutoff,
                    Transfer.id.is_(None),
                )
            )
        )
