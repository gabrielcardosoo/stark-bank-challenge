"""Seção 6 do testes.md — a rede de segurança."""
from datetime import timedelta

from app.adapters.db.models import TransferStatus
from app.services.receive_event import ReceiveEvent
from app.services.reconcile import Reconcile

from .conftest import InvoiceFake, StarkFake, evento_invoice

SEM_CARENCIA = timedelta(0)


def servico(repos, stark, producer):
    return Reconcile(
        stark=stark, invoices=repos.invoices, transfers=repos.transfers,
        producer=producer,
        receive_event=ReceiveEvent(repos.events, repos.invoices, repos.transfers, producer),
        serializar=lambda e: {"id": str(e.id)},
    )


def semear_creditada(repos, stark_id="inv-1", amount=10_000, fee=0):
    invoice = InvoiceFake(stark_id)
    repos.invoices.save_all([invoice])
    repos.invoices.mark_credited(stark_id, amount_cents=amount, fee_cents=fee)
    return invoice


# --- Caso A: o evento nunca chegou ------------------------------------------------

def test_caso_a_processa_o_evento_perdido_e_marca_entregue(repos, producer):
    """Só a API do Stark sabe que esse evento existiu."""
    invoice = InvoiceFake("inv-1")
    repos.invoices.save_all([invoice])
    perdido = evento_invoice("ev-perdido", "credited", "inv-1", amount=10_000)

    resultado = servico(repos, StarkFake(nao_entregues=[perdido]), producer).execute(SEM_CARENCIA)

    assert resultado["A"] == 1
    assert invoice.credited_at is not None
    assert len(producer.publicados) >= 1


def test_caso_a_so_marca_entregue_apos_processar(repos, producer):
    """Se falhar antes, o evento volta na próxima rodada — nada se perde."""
    invoice = InvoiceFake("inv-1")
    repos.invoices.save_all([invoice])
    stark = StarkFake(nao_entregues=[evento_invoice("ev-1", "credited", "inv-1")])

    servico(repos, stark, producer).execute(SEM_CARENCIA)
    assert stark.marcados_entregues == ["ev-1"]


def test_caso_a_falha_no_processamento_nao_marca_entregue(repos):
    class ProducerQuebrado:
        def publish(self, **kw):
            raise RuntimeError("fila fora")

    class ReceiveQueQuebra:
        def execute(self, event, payload):
            raise RuntimeError("erro ao processar")

    stark = StarkFake(nao_entregues=[evento_invoice("ev-1", "credited", "inv-1")])
    rec = Reconcile(stark=stark, invoices=repos.invoices, transfers=repos.transfers,
                    producer=ProducerQuebrado(), receive_event=ReceiveQueQuebra(),
                    serializar=lambda e: {})

    assert rec.execute(SEM_CARENCIA)["A"] == 0
    assert stark.marcados_entregues == []


def test_caso_a_reaproveita_a_dedupe_do_webhook(repos, producer):
    """Se o evento já tinha chegado, o Fallback não duplica trabalho."""
    invoice = InvoiceFake("inv-1")
    repos.invoices.save_all([invoice])
    repos.events.insert_if_new(stark_event_id="ev-1", subscription="invoice",
                               log_type="credited", payload={})

    stark = StarkFake(nao_entregues=[evento_invoice("ev-1", "credited", "inv-1")])
    servico(repos, stark, producer).execute(SEM_CARENCIA)

    assert producer.publicados == [], "reentrega reconhecida pelo stark_event_id"


# --- Caso B1: creditada sem ninguém reivindicar -----------------------------------

def test_b1_republica_a_creditada_sem_transfer(repos, stark, producer):
    semear_creditada(repos)
    assert servico(repos, stark, producer).execute(SEM_CARENCIA)["B1"] == 1
    assert producer.publicados[0]["stark_invoice_id"] == "inv-1"


def test_b1_ignora_a_que_ja_tem_transfer(repos, stark, producer):
    invoice = semear_creditada(repos)
    repos.transfers.insert_pending("transfer-inv-1", invoice.id, 10_000)

    assert servico(repos, stark, producer).execute(SEM_CARENCIA)["B1"] == 0


def test_b1_ignora_a_que_nao_foi_creditada(repos, stark, producer):
    repos.invoices.save_all([InvoiceFake("inv-nao-paga")])
    assert servico(repos, stark, producer).execute(SEM_CARENCIA)["B1"] == 0


# --- Caso B2: reivindicada, desfecho desconhecido ---------------------------------

def test_b2_confirma_quando_a_transfer_existe_no_stark(repos, producer):
    invoice = semear_creditada(repos)
    repos.transfers.insert_pending("transfer-inv-1", invoice.id, 10_000)
    ja_existe = type("T", (), {"id": "stark-tr-7", "status": "success"})()

    resultado = servico(repos, StarkFake(transfer_existente=ja_existe), producer).execute(SEM_CARENCIA)

    assert resultado["B2"] == 1
    linha = repos.transfers.linhas["transfer-inv-1"]
    assert linha.status is TransferStatus.created
    assert linha.stark_transfer_id == "stark-tr-7"


def test_b2_libera_quando_a_transfer_nao_existe(repos, stark, producer):
    """Sem transfer no Stark, a chamada nunca saiu: libera para refazer."""
    invoice = semear_creditada(repos)
    repos.transfers.insert_pending("transfer-inv-1", invoice.id, 10_000)

    assert servico(repos, stark, producer).execute(SEM_CARENCIA)["B2"] == 1
    assert "transfer-inv-1" not in repos.transfers.linhas


def test_b2_nunca_cria_transfer(repos, stark, producer):
    """O Fallback só reinjeta trabalho — quem gasta é o Worker."""
    invoice = semear_creditada(repos)
    repos.transfers.insert_pending("transfer-inv-1", invoice.id, 10_000)

    servico(repos, stark, producer).execute(SEM_CARENCIA)
    assert stark.criadas == []


# --- Carência ---------------------------------------------------------------------

def test_carencia_protege_trabalho_em_andamento(repos, stark, producer):
    """Uma linha `pending` de 10 segundos não é órfã — é trabalho acontecendo."""
    invoice = semear_creditada(repos)
    repos.transfers.insert_pending("transfer-inv-1", invoice.id, 10_000)

    resultado = servico(repos, stark, producer).execute(timedelta(minutes=5))

    assert resultado["B1"] == 0
    assert resultado["B2"] == 0
    assert "transfer-inv-1" in repos.transfers.linhas


# --- Idempotência da reconciliação -------------------------------------------------

def test_rodar_duas_vezes_nao_gera_transfer_extra(repos, stark, producer):
    semear_creditada(repos)
    srv = servico(repos, stark, producer)
    srv.execute(SEM_CARENCIA)
    srv.execute(SEM_CARENCIA)
    assert stark.criadas == []
