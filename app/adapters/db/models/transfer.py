import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class TransferStatus(str, enum.Enum):
    """Ciclo de vida da Transfer.

    `pending` é nosso: a linha nasce antes da chamada ao Stark. Os demais espelham o
    `transfer.status` reportado pelos eventos de webhook.

    Aceita pelo Stark **não** significa dinheiro entregue: `created` é aceitação e
    `success` é conclusão. Uma Transfer pode ir de `created` a `failed` — foi o que
    aconteceu com uma marcada como *Duplicated transfer*.
    """

    pending = "pending"
    created = "created"
    processing = "processing"
    success = "success"
    failed = "failed"
    canceled = "canceled"


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # nulo até o Stark confirmar: a linha nasce antes da chamada
    stark_transfer_id: Mapped[str | None] = mapped_column(String(64))
    # a barreira contra pagamento em dobro
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoices.id")
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger)

    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus, name="transfer_status"),
        default=TransferStatus.pending,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    invoice = relationship("Invoice", back_populates="transfers")

    # o Fallback varre pendentes há mais de N minutos
    __table_args__ = (Index("ix_transfers_status_created_at", "status", "created_at"),)

    def __repr__(self) -> str:
        return (
            f"<Transfer(external_id='{self.external_id}', "
            f"status='{self.status}', amount={self.amount_cents})>"
        )
