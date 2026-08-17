import logging

import starkbank
from starkbank.error import InvalidSignatureError

logger = logging.getLogger(__name__)


class StarkSignatureVerifier:
    def __init__(self, project):
        self._user = project

    def parse(self, raw_body: bytes, signature: str | None):
        """Valida a assinatura sobre o body CRU e devolve o evento.

        `raw_body` precisa ser o corpo exatamente como chegou. Se o framework fizer
        parse do JSON e o código re-serializar, os bytes mudam e a assinatura não bate.

        Levanta `InvalidSignatureError` quando a requisição não é autêntica. Qualquer
        outra exceção sobe intacta de propósito — ver nota abaixo.
        """
        if not signature:
            logger.warning("webhook sem header Digital-Signature — rejeitado")
            raise InvalidSignatureError("header Digital-Signature ausente")

        try:
            event = starkbank.event.parse(
                content=raw_body.decode("utf-8"),
                signature=signature,
                user=self._user,
            )
        except InvalidSignatureError:
            # payload forjado, ou body alterado depois de assinado
            logger.warning(
                "assinatura de webhook REJEITADA (%d bytes) — nenhum evento processado",
                len(raw_body),
            )
            raise

        logger.info(
            "evento %s recebido e assinatura validada (log_type=%s)",
            event.id,
            getattr(getattr(event, "log", None), "type", "?"),
        )
        return event
