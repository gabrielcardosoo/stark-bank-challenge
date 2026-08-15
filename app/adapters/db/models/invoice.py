import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class InvoiceStatus(str, enum.Enum):
    """Espelha o ciclo de vida do Invoice no Stark Bank."""

    created = "created"
    paid = "paid"
    credited = "credited"
    overdue = "overdue"
    canceled = "canceled"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stark_invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_tax_id: Mapped[str] = mapped_column(String(20), nullable=False)

    # o que foi pedido na emissão
    nominal_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # o que efetivamente entrou — difere do nominal se houver multa ou juros
    credited_amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # taxa do Stark; só existe depois do crédito
    fee_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"),
        nullable=False,
        default=InvoiceStatus.created,
    )

    due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    credited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    transfers = relationship("Transfer", back_populates="invoice")

    # o Fallback procura invoices creditadas sem transfer
    __table_args__ = (Index("ix_invoices_status_credited_at", "status", "credited_at"),)

    def __repr__(self) -> str:
        return (
            f"<Invoice(stark_invoice_id='{self.stark_invoice_id}', "
            f"status='{self.status}', nominal={self.nominal_amount_cents})>"
        )
