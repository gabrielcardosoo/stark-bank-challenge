import starkbank


class StarkSignatureVerifier:
    def __init__(self, project):
        self._user = project

    def parse(self, raw_body: bytes, signature: str):
        """Valida a assinatura sobre o body CRU e devolve o evento."""
        return starkbank.event.parse(
            content=raw_body.decode("utf-8"),
            signature=signature,
            user=self._user,
        )
