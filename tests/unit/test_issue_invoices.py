"""Seção 5 do testes.md — o emissor."""
from datetime import datetime, timedelta, timezone

from app.services.issue_invoices import DEFAULT_DUE, DEFAULT_EXPIRATION, IssueInvoices

from .conftest import StarkFake


class PeopleFake:
    """Gerador determinístico: os testes não dependem de sorteio."""

    def __init__(self, quantidade=10, tax_ids=None):
        self._pessoas = [
            {"name": f"Pessoa {i}", "tax_id": (tax_ids or {}).get(i, f"cpf-{i}"),
             "amount": 1_000 * (i + 1)}
            for i in range(quantidade)
        ]

    def batch(self):
        return list(self._pessoas)


def test_emite_todas_as_pessoas_do_lote(repos, stark):
    salvas = IssueInvoices(stark, repos.invoices, PeopleFake(10)).execute()
    assert len(salvas) == 10
    assert len(stark.invoices_criadas) == 10


def test_falha_parcial_nao_aborta_o_lote(repos):
    """3 de 10 falham; as outras 7 são criadas e persistidas."""
    stark = StarkFake(falha_ao_emitir={"cpf-2", "cpf-5", "cpf-7"})

    salvas = IssueInvoices(stark, repos.invoices, PeopleFake(10)).execute()

    assert len(salvas) == 7
    assert len(stark.invoices_criadas) == 7


def test_invoice_e_persistida_com_o_stark_invoice_id(repos, stark):
    salvas = IssueInvoices(stark, repos.invoices, PeopleFake(2)).execute()
    for invoice in salvas:
        assert invoice.stark_invoice_id.startswith("stark-inv-")
        assert repos.invoices.get_by_stark_id(invoice.stark_invoice_id) is not None


def test_payload_leva_due_no_futuro_e_expiration_em_segundos(repos, stark):
    IssueInvoices(stark, repos.invoices, PeopleFake(1)).execute()
    enviado = stark.invoices_criadas[0]

    assert enviado["due"] > datetime.now(timezone.utc)
    assert enviado["expiration"] == int(DEFAULT_EXPIRATION.total_seconds())
    assert isinstance(enviado["expiration"], int), "o Stark espera segundos, não timedelta"


def test_due_respeita_o_parametro_injetado(repos, stark):
    IssueInvoices(stark, repos.invoices, PeopleFake(1), due=timedelta(hours=3)).execute()
    enviado = stark.invoices_criadas[0]

    daqui_a_tres_horas = datetime.now(timezone.utc) + timedelta(hours=3)
    assert abs((enviado["due"] - daqui_a_tres_horas).total_seconds()) < 5


def test_default_due_nao_vence_imediatamente(repos, stark):
    """Um `due` curto demais faz o sandbox nunca pagar: ele não paga invoice vencida."""
    assert DEFAULT_DUE >= timedelta(minutes=30)


def test_payload_leva_os_campos_que_o_stark_exige(repos, stark):
    IssueInvoices(stark, repos.invoices, PeopleFake(1)).execute()
    assert set(stark.invoices_criadas[0]) >= {"amount", "name", "tax_id", "due", "expiration"}


def test_lote_totalmente_recusado_devolve_lista_vazia(repos):
    stark = StarkFake(falha_ao_emitir={f"cpf-{i}" for i in range(3)})
    assert IssueInvoices(stark, repos.invoices, PeopleFake(3)).execute() == []
