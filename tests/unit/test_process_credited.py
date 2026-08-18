"""Seção 2 do testes.md — o único ponto do sistema que move dinheiro."""
import pytest

from app.adapters.db.models import TransferStatus
from app.business_rules import transfer_external_id
from app.services.process_credited import ProcessCredited

from .conftest import InvoiceFake, StarkFake


def servico(repos, stark, commits=None):
    return ProcessCredited(repos.invoices, repos.transfers, stark,
                           commit=lambda: (commits or []).append(1))


def semear_creditada(repos, stark_id="inv-1", amount=10_200, fee=200):
    invoice = InvoiceFake(stark_id)
    repos.invoices.save_all([invoice])
    repos.invoices.mark_credited(stark_id, amount_cents=amount, fee_cents=fee)
    return invoice


def mensagem(stark_id="inv-1", amount=10_200, fee=200):
    return {"stark_invoice_id": stark_id, "amount": amount, "fee": fee, "event_id": "e"}


def test_caminho_feliz_cria_uma_transfer(repos, stark):
    semear_creditada(repos)
    assert servico(repos, stark).execute(mensagem()) is True

    assert len(stark.criadas) == 1
    assert stark.criadas[0]["amount"] == 10_000, "amount - fee"
    linha = repos.transfers.linhas[transfer_external_id("inv-1")]
    assert linha.status is TransferStatus.created


def test_mensagem_repetida_nao_chama_o_stark_de_novo(repos, stark):
    """Kafka entrega at-least-once: a mesma mensagem pode chegar duas vezes."""
    semear_creditada(repos)
    srv = servico(repos, stark)
    assert srv.execute(mensagem()) is True
    assert srv.execute(mensagem()) is False
    assert len(stark.criadas) == 1


def test_insert_pending_ja_existente_desiste_antes_de_chamar_o_stark(repos, stark):
    """É o mutex: quem perde a disputa não gasta dinheiro."""
    invoice = semear_creditada(repos)
    repos.transfers.insert_pending(transfer_external_id("inv-1"), invoice.id, 10_000)

    assert servico(repos, stark).execute(mensagem()) is False
    assert stark.criadas == []


def test_valor_persistido_e_o_mesmo_enviado_ao_stark(repos, stark):
    semear_creditada(repos, amount=50_000, fee=1_500)
    servico(repos, stark).execute(mensagem(amount=50_000, fee=1_500))

    linha = repos.transfers.linhas[transfer_external_id("inv-1")]
    assert linha.amount_cents == stark.criadas[0]["amount"] == 48_500


def test_liquido_zero_ou_negativo_nao_gera_transfer(repos, stark):
    semear_creditada(repos, amount=200, fee=200)
    assert servico(repos, stark).execute(mensagem(amount=200, fee=200)) is False
    assert stark.criadas == []
    assert repos.transfers.linhas == {}


def test_invoice_inexistente_e_descartada(repos, stark):
    assert servico(repos, stark).execute(mensagem("nao-existe")) is False
    assert stark.criadas == []


def test_commit_acontece_antes_da_chamada_ao_stark(repos):
    """A reivindicação precisa estar gravada antes de mover dinheiro."""
    semear_creditada(repos)
    ordem = []

    class StarkQueRegistra(StarkFake):
        def create_transfer(self, *, amount, external_id):
            ordem.append("stark")
            return super().create_transfer(amount=amount, external_id=external_id)

    srv = ProcessCredited(repos.invoices, repos.transfers, StarkQueRegistra(),
                          commit=lambda: ordem.append("commit"))
    srv.execute(mensagem())

    assert ordem == ["commit", "stark", "commit"]


def test_falha_do_stark_deixa_a_linha_pending(repos):
    """Sem confirmação, a linha fica para o Fallback (caso B2) resolver."""
    semear_creditada(repos)
    stark = StarkFake(falha_ao_criar=True)

    with pytest.raises(RuntimeError):
        servico(repos, stark).execute(mensagem())

    linha = repos.transfers.linhas[transfer_external_id("inv-1")]
    assert linha.status is TransferStatus.pending
    assert linha.stark_transfer_id is None


def test_transfer_ja_existente_no_stark_nao_cria_outra(repos):
    """O Stark não deduplica na criação: perguntar antes evita a segunda cobrança."""
    semear_creditada(repos)
    ja_existe = type("T", (), {"id": "stark-pre-existente", "status": "success"})()

    assert servico(repos, StarkFake(transfer_existente=ja_existe)).execute(mensagem()) is True

    linha = repos.transfers.linhas[transfer_external_id("inv-1")]
    assert linha.stark_transfer_id == "stark-pre-existente"


def test_external_id_enviado_ao_stark_e_o_derivado_da_invoice(repos, stark):
    semear_creditada(repos)
    servico(repos, stark).execute(mensagem())
    assert stark.criadas[0]["external_id"] == transfer_external_id("inv-1")
