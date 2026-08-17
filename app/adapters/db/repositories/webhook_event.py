import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.adapters.db.models import WebhookEvent

logger = logging.getLogger(__name__)


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
        stark_invoice_id: str | None = None,
    ) -> bool:
        """Persiste o evento. Devolve False se já tinha sido recebido."""
        stmt = (
            pg_insert(WebhookEvent)
            .values(
                stark_event_id=stark_event_id,
                subscription=subscription,
                log_type=log_type,
                payload=payload,
                stark_invoice_id=stark_invoice_id,
            )
            .on_conflict_do_nothing(index_elements=["stark_event_id"])
            .returning(WebhookEvent.id)
        )
        novo = self._session.execute(stmt).scalar_one_or_none() is not None

        if novo:
            logger.info(
                "evento persistido stark_event_id=%s log_type=%s",
                stark_event_id,
                log_type,
            )
        else:
            logger.info(
                "IDEMPOTÊNCIA: evento %s já recebido — reentrega ignorada",
                stark_event_id,
            )
        return novo

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
