"""Seções 2 e 3 do testes.md — dedupe e roteamento de eventos."""
from app.adapters.db.models import InvoiceStatus, TransferStatus
from app.services.receive_event import ReceiveEvent

from .conftest import InvoiceFake, ProducerFake, evento_invoice, evento_transfer


def servico(repos, producer):
    return ReceiveEvent(repos.events, repos.invoices, repos.transfers, producer)


def semear(repos, stark_id="inv-1"):
    invoice = InvoiceFake(stark_id)
    repos.invoices.save_all([invoice])
    return invoice


# --- 2. Idempotência -------------------------------------------------------------

def test_mesmo_evento_processado_duas_vezes_publica_uma_vez(repos, producer):
    semear(repos)
    srv = servico(repos, producer)
    ev = evento_invoice("ev-1", "credited", "inv-1", amount=10_200, fee=200)

    assert srv.execute(ev, {}) is True
    assert srv.execute(ev, {}) is False, "a reentrega deve ser reconhecida"
    assert len(producer.publicados) == 1


def test_dois_eventos_diferentes_para_a_mesma_invoice_publicam_duas_vezes(repos, producer):
    """A dedupe por evento não cobre este caso — quem cobre é o external_id no worker."""
    semear(repos)
    srv = servico(repos, producer)
    srv.execute(evento_invoice("ev-1", "credited", "inv-1"), {})
    srv.execute(evento_invoice("ev-2", "credited", "inv-1"), {})
    assert len(producer.publicados) == 2


def test_evento_reentregue_nao_regrava_o_payload(repos, producer):
    semear(repos)
    srv = servico(repos, producer)
    srv.execute(evento_invoice("ev-1", "credited", "inv-1"), {"versao": 1})
    srv.execute(evento_invoice("ev-1", "credited", "inv-1"), {"versao": 2})
    assert repos.events.eventos["ev-1"]["payload"] == {"versao": 1}


# --- 3. Roteamento ---------------------------------------------------------------

def test_credited_enfileira_e_registra_o_credito(repos, producer):
    invoice = semear(repos)
    srv = servico(repos, producer)

    srv.execute(evento_invoice("ev-1", "credited", "inv-1", amount=10_200, fee=200,
                               transaction_ids=["tx-1"]), {})

    assert len(producer.publicados) == 1
    assert producer.publicados[0]["amount"] == 10_200
    assert producer.publicados[0]["fee"] == 200
    assert invoice.credited_at is not None
    assert invoice.credited_amount_cents == 10_200
    assert invoice.transaction_ids == ["tx-1"]


def test_credited_grava_status_paid_nao_credited(repos, producer):
    """`credited` é tipo de log; o status que o Stark reporta é `paid`."""
    invoice = semear(repos)
    servico(repos, producer).execute(evento_invoice("ev-1", "credited", "inv-1"), {})
    assert invoice.status is InvoiceStatus.paid


def test_paid_atualiza_status_e_nao_enfileira(repos, producer):
    invoice = semear(repos)
    servico(repos, producer).execute(evento_invoice("ev-1", "paid", "inv-1"), {})
    assert invoice.status is InvoiceStatus.paid
    assert producer.publicados == []


def test_overdue_e_expired_nao_enfileiram(repos, producer):
    invoice = semear(repos)
    srv = servico(repos, producer)
    srv.execute(evento_invoice("ev-1", "overdue", "inv-1"), {})
    assert invoice.status is InvoiceStatus.overdue
    srv.execute(evento_invoice("ev-2", "expired", "inv-1"), {})
    assert invoice.status is InvoiceStatus.expired
    assert producer.publicados == []


def test_created_e_ignorado_para_status(repos, producer):
    """O issuer já gravou na emissão; um `created` atrasado só regrediria o estado."""
    invoice = semear(repos)
    invoice.status = InvoiceStatus.paid
    servico(repos, producer).execute(evento_invoice("ev-1", "created", "inv-1"), {})
    assert invoice.status is InvoiceStatus.paid
    assert "ev-1" in repos.events.processados()


def test_log_type_desconhecido_nao_quebra(repos, producer):
    invoice = semear(repos)
    assert servico(repos, producer).execute(
        evento_invoice("ev-1", "tipo_que_o_stark_inventou", "inv-1"), {}) is True
    assert invoice.status is InvoiceStatus.created
    assert producer.publicados == []


def test_evento_sem_invoice_deixa_rastro_sem_marcar_processado(repos, producer):
    srv = servico(repos, producer)
    assert srv.execute(
        evento_invoice("ev-1", "credited", "inv-1", com_invoice=False), {}) is True
    assert repos.events.processados() == [], "fica pendente para investigação"


# --- ordem de chegada (decisão 6) ------------------------------------------------

def test_ordem_de_chegada_nao_muda_o_estado_final(repos, producer):
    """Medido no sandbox: `credited` chega antes de `paid` em 4 de 11 invoices."""
    inv_a = semear(repos, "inv-A")
    inv_b = semear(repos, "inv-B")
    srv = servico(repos, producer)

    srv.execute(evento_invoice("a1", "credited", "inv-A", amount=10_200, fee=200), {})
    srv.execute(evento_invoice("a2", "paid", "inv-A"), {})

    srv.execute(evento_invoice("b1", "paid", "inv-B"), {})
    srv.execute(evento_invoice("b2", "credited", "inv-B", amount=10_200, fee=200), {})

    for invoice in (inv_a, inv_b):
        assert invoice.status is InvoiceStatus.paid
        assert invoice.credited_at is not None
        assert invoice.credited_amount_cents == 10_200


def test_created_atrasado_nao_apaga_o_credito(repos, producer):
    invoice = semear(repos)
    srv = servico(repos, producer)
    srv.execute(evento_invoice("ev-1", "credited", "inv-1"), {})
    srv.execute(evento_invoice("ev-2", "created", "inv-1"), {})
    assert invoice.credited_at is not None
    assert invoice.status is InvoiceStatus.paid


# --- assinatura `transfer` (decisão 7) -------------------------------------------

def test_transfer_success_atualiza_o_status_local(repos, producer):
    semear(repos)
    repos.transfers.insert_pending("transfer-inv-1", 1, 10_000)
    repos.transfers.mark_created("transfer-inv-1", "stark-tr-9")

    servico(repos, producer).execute(evento_transfer("ev-1", "success", "stark-tr-9"), {})
    assert repos.transfers.linhas["transfer-inv-1"].status is TransferStatus.success


def test_transfer_failed_marca_falha_e_nao_republica(repos, producer):
    """Aceita pelo Stark não é dinheiro entregue."""
    semear(repos)
    repos.transfers.insert_pending("transfer-inv-1", 1, 10_000)
    repos.transfers.mark_created("transfer-inv-1", "stark-tr-9")

    servico(repos, producer).execute(
        evento_transfer("ev-1", "failed", "stark-tr-9",
                        errors=[type("E", (), {"code": "Duplicated transfer"})()]), {})

    assert repos.transfers.linhas["transfer-inv-1"].status is TransferStatus.failed
    assert producer.publicados == []


def test_transfer_de_outra_origem_nao_quebra(repos, producer):
    assert servico(repos, producer).execute(
        evento_transfer("ev-1", "success", "nao-e-nossa"), {}) is True


def test_assinatura_desconhecida_e_ignorada(repos, producer):
    ev = evento_invoice("ev-1", "paid", "inv-1")
    ev.subscription = "boleto"
    assert servico(repos, producer).execute(ev, {}) is True
    assert producer.publicados == []


# --- 7. Falha ao publicar --------------------------------------------------------

def test_falha_ao_publicar_nao_levanta_e_deixa_para_o_fallback(repos):
    """Se levantasse, o endpoint devolveria 500 e o Stark reentregaria sem necessidade."""
    invoice = semear(repos)
    quebrado = ProducerFake(falha=True)

    assert servico(repos, quebrado).execute(
        evento_invoice("ev-1", "credited", "inv-1"), {}) is True

    assert invoice.credited_at is not None, "o crédito fica registrado"
    assert repos.events.processados() == [], "sem processed_at: o Fallback recupera"
