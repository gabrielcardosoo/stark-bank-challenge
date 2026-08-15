from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.adapters.db.models import WebhookEvent


class WebhookEventRepository:
    def __init__(self, session: Session):
        self._session = session

    def insert_if_new(
        self,
        *,
        stark_event_id: str,
        subscription: str,
        log_type: str,
        payload: dict,
    ) -> bool:
        """Persiste o evento. Devolve False se já tinha sido recebido.

        Primeira barreira contra reentrega: rejeita a duplicata antes de publicar no
        Kafka ou acordar o Worker. Não é a garantia final — essa é o `external_id` em
        `transfers` —, mas custa um INSERT.
        """
        stmt = (
            pg_insert(WebhookEvent)
            .values(
                stark_event_id=stark_event_id,
                subscription=subscription,
                log_type=log_type,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["stark_event_id"])
            .returning(WebhookEvent.id)
        )
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def exists(self, stark_event_id: str) -> bool:
        return (
            self._session.scalar(
                select(WebhookEvent.id).where(
                    WebhookEvent.stark_event_id == stark_event_id
                )
            )
            is not None
        )

    def mark_processed(self, stark_event_id: str) -> None:
        self._session.execute(
            update(WebhookEvent)
            .where(WebhookEvent.stark_event_id == stark_event_id)
            .values(processed_at=datetime.now(timezone.utc))
        )
