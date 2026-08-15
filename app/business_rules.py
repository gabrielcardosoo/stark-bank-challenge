"""Regras puras: mesma entrada, mesma saída, sem I/O.

Nada aqui importa `starkbank` nem o ORM. É o que permite testar as decisões mais
críticas do sistema sem banco, sem rede e sem mock.
"""
import random

from faker import Faker

_faker = Faker("pt_BR")

MIN_INVOICES_POR_LOTE = 8
MAX_INVOICES_POR_LOTE = 12


def net_amount(amount: int, fee: int) -> int:
    """Valor a transferir, em centavos: o que entrou menos a taxa do Stark."""
    return amount - fee


def is_transferable(net: int) -> bool:
    """Não faz sentido criar Transfer de valor zero ou negativo."""
    return net > 0


def transfer_external_id(invoice_id: str) -> str:
    """Determinístico por invoice: é o que impede a Transfer duplicada.

    Se este valor variar entre chamadas, toda a proteção contra pagamento em dobro
    deixa de existir.
    """
    return f"invoice-{invoice_id}"


class PeopleGenerator:
    """Gera os pagadores fictícios das invoices.

    O tax_id precisa ter dígito verificador válido — o sandbox rejeita CPF inválido.
    """

    def __init__(
        self,
        minimo: int = MIN_INVOICES_POR_LOTE,
        maximo: int = MAX_INVOICES_POR_LOTE,
        min_amount_cents: int = 1_000,
        max_amount_cents: int = 500_000,
    ):
        self._minimo = minimo
        self._maximo = maximo
        self._min_amount = min_amount_cents
        self._max_amount = max_amount_cents

    def batch_size(self) -> int:
        """Quantas invoices emitir neste lote."""
        return random.randint(self._minimo, self._maximo)

    def person(self) -> dict:
        return {
            "name": _faker.name(),
            "tax_id": _faker.cpf(),
            "amount": random.randint(self._min_amount, self._max_amount),
        }

    def batch(self) -> list[dict]:
        return [self.person() for _ in range(self.batch_size())]
