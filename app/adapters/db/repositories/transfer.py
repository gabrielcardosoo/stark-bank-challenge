import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.adapters.db.models import Transfer, TransferStatus

logger = logging.getLogger(__name__)


class TransferRepository:
    def __init__(self, session: Session):
        self._session = session

    def insert_pending(
        self, external_id: str, invoice_id: int, amount_cents: int
    ) -> bool:
        """Reivindica o trabalho antes de chamar o Stark.

        Devolve False se a linha já existe — outro worker pegou, ou a Transfer já foi
        feita. É este INSERT, e não a checagem prévia, que funciona como mutex: o
        `ON CONFLICT DO NOTHING` resolve a corrida dentro do Postgres, sem depender de
        garantia da API remota.
        """
        stmt = (
            pg_insert(Transfer)
            .values(
                external_id=external_id,
                invoice_id=invoice_id,
                amount_cents=amount_cents,
                status=TransferStatus.pending,
            )
            .on_conflict_do_nothing(index_elements=["external_id"])
            .returning(Transfer.id)
        )
        reivindicou = self._session.execute(stmt).scalar_one_or_none() is not None

        if reivindicou:
            logger.info(
                "transfer reivindicada external_id=%s amount=%d", external_id, amount_cents
            )
        else:
            # a prova de que a idempotência funcionou: uma duplicata foi barrada
            logger.info(
                "IDEMPOTÊNCIA: external_id=%s já reivindicado — nenhuma Transfer duplicada",
                external_id,
            )
        return reivindicou

    def mark_created(self, external_id: str, stark_transfer_id: str) -> None:
        """Confirma que a Transfer saiu no Stark."""
        self._session.execute(
            update(Transfer)
            .where(Transfer.external_id == external_id)
            .values(
                stark_transfer_id=stark_transfer_id,
                status=TransferStatus.created,
                confirmed_at=datetime.now(timezone.utc),
            )
        )
        logger.info(
            "transfer confirmada external_id=%s stark_id=%s",
            external_id,
            stark_transfer_id,
        )

    def update_status_by_stark_id(
        self, stark_transfer_id: str, status: TransferStatus
    ) -> bool:
        """Aplica o status reportado pelo webhook de `transfer`.

        Devolve False se a Transfer não é nossa — pode acontecer se alguém criar uma
        pela interface do Stark, e não é erro.
        """
        resultado = self._session.execute(
            update(Transfer)
            .where(Transfer.stark_transfer_id == stark_transfer_id)
            .values(status=status)
        )
        if resultado.rowcount == 0:
            logger.warning(
                "webhook de transfer %s não corresponde a nenhuma linha nossa",
                stark_transfer_id,
            )
            return False

        nivel = logging.ERROR if status is TransferStatus.failed else logging.INFO
        logger.log(
            nivel,
            "transfer %s -> %s",
            stark_transfer_id,
            status.value,
        )
        return True

    def exists(self, external_id: str) -> bool:
        return (
            self._session.scalar(
                select(Transfer.id).where(Transfer.external_id == external_id)
            )
            is not None
        )

    def get_by_external_id(self, external_id: str) -> Transfer | None:
        return self._session.scalar(
            select(Transfer).where(Transfer.external_id == external_id)
        )

    def pending(self, older_than: timedelta) -> list[Transfer]:
        """Caso B2 do Fallback: reivindicadas, mas sem confirmação de que saíram."""
        cutoff = datetime.now(timezone.utc) - older_than
        pendentes = list(
            self._session.scalars(
                select(Transfer).where(
                    Transfer.status == TransferStatus.pending,
                    Transfer.created_at < cutoff,
                )
            )
        )
        if pendentes:
            logger.warning(
                "caso B2: %d transfer(s) pendentes há mais de %s", len(pendentes), older_than
            )
        return pendentes

    def release(self, external_id: str) -> None:
        """Libera uma pending que se confirmou não ter saído no Stark.

        Apaga a linha em vez de marcá-la `failed` porque o `external_id` é
        determinístico: uma linha `failed` bloquearia a nova tentativa pelo UNIQUE.
        O invoice volta a aparecer no caso B1, que reinjeta o trabalho.
        """
        self._session.execute(
            delete(Transfer).where(
                Transfer.external_id == external_id,
                Transfer.status == TransferStatus.pending,
            )
        )
        logger.warning(
            "transfer liberada external_id=%s — não saiu no Stark, será refeita",
            external_id,
        )
