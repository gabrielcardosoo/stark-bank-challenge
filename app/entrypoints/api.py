"""Endpoint HTTP que recebe os callbacks do Stark Bank.

Só cuida de HTTP e de autenticação: extrai o body cru, valida a assinatura e traduz o
resultado em código de status. Regra de negócio fica no `ReceiveEvent`.
"""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from starkbank.error import InvalidSignatureError

from app.adapters.db.connector import session_scope
from app.adapters.db.repositories import (
    InvoiceRepository,
    TransferRepository,
    WebhookEventRepository,
)
from app.adapters.redpanda import CreditedInvoiceProducer
from app.adapters.stark import StarkClient, StarkSignatureVerifier
from app.logger import Logger
from app.services.receive_event import ReceiveEvent

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "Digital-Signature"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Logger(file="logs/api.log")
    logger.info("api iniciando")

    app.state.producer = CreditedInvoiceProducer()
    app.state.verifier = StarkSignatureVerifier(StarkClient()._project)

    logger.info("api pronta")
    yield
    logger.info("api encerrando")


app = FastAPI(title="Stark Bank Challenge — Webhook", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/stark")
async def receive_webhook(request: Request) -> Response:
    # body CRU: request.json() re-serializado muda os bytes e a assinatura não bate
    raw_body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)

    try:
        event = request.app.state.verifier.parse(raw_body, signature)
        payload = json.loads(raw_body)

    except (InvalidSignatureError, ValueError) as erro:
        logger.warning("webhook rejeitado: %s", erro)
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    try:
        with session_scope() as session:
            processado = ReceiveEvent(
                events=WebhookEventRepository(session),
                invoices=InvoiceRepository(session),
                transfers=TransferRepository(session),
                producer=request.app.state.producer,
            ).execute(event, payload)

    except Exception:
        logger.exception("webhook falhou por erro interno — Stark deve reentregar")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if not processado:
        logger.debug("evento %s era reentrega", event.id)
        
    return Response(status_code=status.HTTP_200_OK)
