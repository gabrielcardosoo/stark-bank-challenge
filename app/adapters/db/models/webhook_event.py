from datetime import datetime

from sqlalchemy import DateTime, BigInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class WebhookEvent(Base):
    """O que aconteceu, em oposição ao estado atual guardado em `invoices`.

    Guarda o payload cru como evidência, permite reprocessar sem depender de
    reentrega do Stark, e é o que distingue "o evento nunca chegou" de "chegou e o
    processamento falhou".
    """

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # a primeira barreira contra reentrega
    stark_event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    subscription: Mapped[str] = mapped_column(String(50), nullable=False)
    log_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookEvent(stark_event_id='{self.stark_event_id}', "
            f"log_type='{self.log_type}')>"
        )
