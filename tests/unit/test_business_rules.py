"""Seções 4 e 5 do testes.md — funções puras, os testes mais baratos do projeto."""
import random
import re

import pytest
from faker import Faker

from app.business_rules import (
    MAX_INVOICES_POR_LOTE,
    MIN_INVOICES_POR_LOTE,
    PeopleGenerator,
    is_transferable,
    net_amount,
    transfer_external_id,
)


# --- 4. Cálculo do valor líquido -------------------------------------------------

def test_net_desconta_a_taxa():
    assert net_amount(10_200, 200) == 10_000


def test_net_com_taxa_zero_devolve_o_valor_cheio():
    assert net_amount(21_772, 0) == 21_772


def test_net_devolve_inteiro_nunca_float():
    resultado = net_amount(10_201, 3)
    assert isinstance(resultado, int)
    assert not isinstance(resultado, float)


@pytest.mark.parametrize("amount,fee", [(200, 200), (100, 500), (0, 0)])
def test_nao_transfere_valor_zero_ou_negativo(amount, fee):
    assert is_transferable(net_amount(amount, fee)) is False


def test_transfere_qualquer_valor_positivo():
    assert is_transferable(1) is True


# --- external_id -----------------------------------------------------------------

def test_external_id_e_deterministico():
    """Se variar entre chamadas, toda a proteção contra pagamento duplo cai."""
    assert transfer_external_id("123") == transfer_external_id("123")


def test_external_id_difere_por_invoice():
    assert transfer_external_id("123") != transfer_external_id("456")


def test_external_id_nao_usa_o_prefixo_invoice():
    """O Stark usa `invoice-{id}` internamente; colidir gera 'Duplicated transfer'."""
    assert not transfer_external_id("123").startswith("invoice-")


# --- 5. Emissor: quantidade e CPF -------------------------------------------------

def test_lote_fica_sempre_entre_8_e_12():
    random.seed(42)
    tamanhos = {PeopleGenerator().batch_size() for _ in range(500)}
    assert min(tamanhos) >= MIN_INVOICES_POR_LOTE
    assert max(tamanhos) <= MAX_INVOICES_POR_LOTE


def _cpf_valido(cpf: str) -> bool:
    """Validação independente do gerador: recalcula os dois dígitos."""
    numeros = re.sub(r"\D", "", cpf)
    if len(numeros) != 11 or len(set(numeros)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(numeros[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10 % 11) % 10
        if digito != int(numeros[tamanho]):
            return False
    return True


def test_cpf_gerado_tem_digito_verificador_valido():
    """O sandbox rejeita tax_id inválido — este teste é o que garante a emissão."""
    Faker.seed(42)
    gerador = PeopleGenerator()
    for _ in range(50):
        assert _cpf_valido(gerador.person()["tax_id"])


def test_a_validacao_de_cpf_do_teste_rejeita_invalidos():
    """Garante que o validador acima não aprova qualquer coisa."""
    assert not _cpf_valido("111.111.111-11")
    assert not _cpf_valido("123.456.789-00")


def test_pessoa_tem_os_campos_que_o_stark_exige():
    assert set(PeopleGenerator().person()) == {"name", "tax_id", "amount"}


def test_batch_gera_a_quantidade_sorteada():
    gerador = PeopleGenerator()
    lote = gerador.batch()
    assert MIN_INVOICES_POR_LOTE <= len(lote) <= MAX_INVOICES_POR_LOTE
