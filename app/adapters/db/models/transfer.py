import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class TransferStatus(str, enum.Enum):
    """`pending` nasce antes da chamada ao Stark; `created` confirma que ela saiu."""

    pending = "pending"
    created = "created"
    failed = "failed"


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # nulo até o Stark confirmar: a linha nasce antes da chamada
    stark_transfer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # a barreira contra pagamento em dobro
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoices.id"), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus, name="transfer_status"),
        nullable=False,
        default=TransferStatus.pending,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    invoice = relationship("Invoice", back_populates="transfers")

    # o Fallback varre pendentes há mais de N minutos
    __table_args__ = (Index("ix_transfers_status_created_at", "status", "created_at"),)

    def __repr__(self) -> str:
        return (
            f"<Transfer(external_id='{self.external_id}', "
            f"status='{self.status}', amount={self.amount_cents})>"
        )
