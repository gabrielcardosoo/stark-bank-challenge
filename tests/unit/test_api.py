"""Seção 1 do testes.md — a borda HTTP: 400 é da requisição, 500 é nosso."""
import json

import pytest
from fastapi.testclient import TestClient
from starkbank.error import InvalidSignatureError

import app.entrypoints.api as api

from .conftest import evento_invoice

CABECALHO = {"Digital-Signature": "valida"}


class VerifierFake:
    def parse(self, raw_body, signature):
        if not signature:
            raise InvalidSignatureError("header ausente")
        if signature != "valida":
            raise InvalidSignatureError("nao confere")
        return evento_invoice("ev-1", "credited", "inv-1")


class ReceiveFake:
    def __init__(self, erro=None, resultado=True):
        self.erro = erro
        self.resultado = resultado
        self.chamadas = 0

    def __call__(self, **kwargs):
        return self

    def execute(self, event, payload):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.resultado


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    """Sobe a app com o startup neutralizado: nada de Stark, fila, banco ou arquivo."""
    monkeypatch.setattr(api, "Logger", lambda **kw: None)
    monkeypatch.setattr(api, "CreditedInvoiceProducer", lambda: object())
    monkeypatch.setattr(api, "StarkClient", lambda: type("C", (), {"_project": None})())
    monkeypatch.setattr(api, "StarkSignatureVerifier", lambda p: VerifierFake())

    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c


def _sem_banco(monkeypatch, receive):
    """Troca o service e a sessão por fakes."""
    from contextlib import contextmanager

    @contextmanager
    def sessao_fake():
        yield object()

    monkeypatch.setattr(api, "session_scope", sessao_fake)
    monkeypatch.setattr(api, "ReceiveEvent", receive)
    monkeypatch.setattr(api, "WebhookEventRepository", lambda s: None)
    monkeypatch.setattr(api, "InvoiceRepository", lambda s: None)
    monkeypatch.setattr(api, "TransferRepository", lambda s: None)


def test_health_responde(cliente):
    assert cliente.get("/health").json() == {"status": "ok"}


def test_sem_header_devolve_400(cliente, monkeypatch):
    receive = ReceiveFake()
    _sem_banco(monkeypatch, receive)

    r = cliente.post("/webhooks/stark", content=b"{}")

    assert r.status_code == 400
    assert receive.chamadas == 0, "nada é processado quando a assinatura falha"


def test_assinatura_invalida_devolve_400(cliente, monkeypatch):
    receive = ReceiveFake()
    _sem_banco(monkeypatch, receive)

    r = cliente.post("/webhooks/stark", content=b"{}",
                     headers={"Digital-Signature": "forjada"})

    assert r.status_code == 400
    assert receive.chamadas == 0


def test_body_nao_json_devolve_400(cliente, monkeypatch):
    receive = ReceiveFake()
    _sem_banco(monkeypatch, receive)

    r = cliente.post("/webhooks/stark", content=b"isso-nao-e-json", headers=CABECALHO)

    assert r.status_code == 400
    assert receive.chamadas == 0


def test_evento_valido_devolve_200(cliente, monkeypatch):
    _sem_banco(monkeypatch, ReceiveFake())
    assert cliente.post("/webhooks/stark", content=b"{}", headers=CABECALHO).status_code == 200


def test_reentrega_tambem_devolve_200(cliente, monkeypatch):
    """Reentrega não é erro: 4xx faria o Stark insistir sem motivo."""
    _sem_banco(monkeypatch, ReceiveFake(resultado=False))
    assert cliente.post("/webhooks/stark", content=b"{}", headers=CABECALHO).status_code == 200


def test_falha_interna_devolve_500_para_o_stark_reentregar(cliente, monkeypatch):
    """400 aqui descartaria um crédito para sempre."""
    _sem_banco(monkeypatch, ReceiveFake(erro=RuntimeError("Postgres fora")))

    r = cliente.post("/webhooks/stark", content=b"{}", headers=CABECALHO)

    assert r.status_code == 500


def test_body_cru_chega_intacto_ao_verifier(cliente, monkeypatch):
    """request.json() re-serializado mudaria os bytes e quebraria a assinatura."""
    recebido = {}

    class VerifierQueGuarda(VerifierFake):
        def parse(self, raw_body, signature):
            recebido["raw"] = raw_body
            return super().parse(raw_body, signature)

    api.app.state.verifier = VerifierQueGuarda()
    _sem_banco(monkeypatch, ReceiveFake())

    corpo = b'{"b":2,   "a":1}'
    cliente.post("/webhooks/stark", content=corpo, headers=CABECALHO)

    assert recebido["raw"] == corpo
    assert recebido["raw"] != json.dumps(json.loads(corpo)).encode()
