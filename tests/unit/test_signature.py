"""Seção 1 do testes.md — a assinatura é a única barreira contra payload forjado."""
import json

import pytest
import starkbank
from starkbank.error import InvalidSignatureError

from app.adapters.stark.signature import StarkSignatureVerifier

CORPO = b'{"event": {"id": "1"}}'


@pytest.fixture
def verifier():
    return StarkSignatureVerifier(project=object())


def test_header_ausente_levanta_invalid_signature(verifier):
    with pytest.raises(InvalidSignatureError):
        verifier.parse(CORPO, None)


def test_header_vazio_levanta_invalid_signature(verifier):
    with pytest.raises(InvalidSignatureError):
        verifier.parse(CORPO, "")


def test_assinatura_em_base64_invalido_e_rejeitada(verifier, monkeypatch):
    monkeypatch.setattr(starkbank.event, "parse",
                        lambda **kw: (_ for _ in ()).throw(
                            InvalidSignatureError("nao e base64")))
    with pytest.raises(InvalidSignatureError):
        verifier.parse(CORPO, "nao-e-base64-!!!")


def test_payload_adulterado_e_rejeitado(verifier, monkeypatch):
    """Prova que a assinatura é verificada contra o conteúdo, não apenas lida."""
    assinado = b'{"a": 1}'

    def parse_fake(*, content, signature, user):
        if content.encode() != assinado:
            raise InvalidSignatureError("conteudo nao confere")
        return type("E", (), {"id": "1", "log": None})()

    monkeypatch.setattr(starkbank.event, "parse", parse_fake)

    assert verifier.parse(assinado, "sig-valida").id == "1"
    with pytest.raises(InvalidSignatureError):
        verifier.parse(b'{"a": 2}', "sig-valida")


def test_falha_de_rede_nao_vira_invalid_signature(verifier, monkeypatch):
    """O parse busca a chave pública na API; queda de rede é problema NOSSO (500),
    não requisição inválida (400)."""
    monkeypatch.setattr(starkbank.event, "parse",
                        lambda **kw: (_ for _ in ()).throw(ConnectionError("rede fora")))

    with pytest.raises(ConnectionError):
        verifier.parse(CORPO, "sig")


def test_body_e_repassado_cru_sem_reserializar(verifier, monkeypatch):
    """Se o código re-serializasse o JSON, os bytes mudariam e a assinatura cairia."""
    recebido = {}

    def parse_fake(*, content, signature, user):
        recebido["content"] = content
        return type("E", (), {"id": "1", "log": None})()

    monkeypatch.setattr(starkbank.event, "parse", parse_fake)

    # espaçamento incomum: sobrevive só se não houver round-trip por json
    corpo = b'{"b":2,   "a":1}'
    verifier.parse(corpo, "sig")

    assert recebido["content"] == corpo.decode()
    assert recebido["content"] != json.dumps(json.loads(corpo))
