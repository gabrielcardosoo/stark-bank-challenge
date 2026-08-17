import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Index, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class InvoiceStatus(str, enum.Enum):
    """Espelha o `invoice.status` do Stark Bank — nada além disso """

    created = "created"
    paid = "paid"
    overdue = "overdue"
    expired = "expired"
    canceled = "canceled"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stark_invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    customer_name: Mapped[str] = mapped_column(String(255))
    customer_tax_id: Mapped[str] = mapped_column(String(20))

    # o que foi pedido na emissão
    nominal_amount_cents: Mapped[int] = mapped_column(BigInteger)
    # o que efetivamente entrou — difere do nominal se houver multa ou juros
    credited_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    # taxa do Stark; só existe depois do crédito
    fee_cents: Mapped[int | None] = mapped_column(BigInteger)

    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"),
        default=InvoiceStatus.created,
    )

    # transações no extrato geradas pelo crédito; só chegam no evento `credited`
    transaction_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # NULL enquanto o dinheiro não entrou. É este campo, e não `status`, que decide se
    # a invoice deve virar Transfer — imune à ordem de chegada dos eventos.
    credited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    transfers = relationship("Transfer", back_populates="invoice")

    __table_args__ = (Index("ix_invoices_credited_at", "credited_at"),)

    def __repr__(self) -> str:
        return (
            f"<Invoice(stark_invoice_id='{self.stark_invoice_id}', "
            f"status='{self.status}', nominal={self.nominal_amount_cents})>"
        )
