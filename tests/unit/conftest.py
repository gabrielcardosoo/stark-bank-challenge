"""Fakes em memória: nenhum teste desta pasta toca banco, rede ou relógio real."""
import os

# Definido ANTES de importar qualquer módulo do app: o `connector` cria a engine no
# import. A URL é sintaticamente válida e nunca conecta — o SQLAlchemy é preguiçoso.
os.environ.setdefault("DATABASE_URL", "postgresql://teste:teste@localhost:5432/teste")
os.environ.setdefault("STARK_PROJECT_ID", "0")
os.environ.setdefault("STARK_PRIVATE_KEY_PATH", "/dev/null")
os.environ.setdefault("STARK_DESTINATION_ACCOUNT_PATH",
                      "app/adapters/stark/destination_account.json")

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.db.models import InvoiceStatus, TransferStatus


class InvoiceFake:
    """Substitui o modelo do ORM sem depender do SQLAlchemy."""

    def __init__(self, stark_invoice_id, nominal=10_000, local_id=None):
        self.id = local_id or abs(hash(stark_invoice_id)) % 10_000
        self.stark_invoice_id = stark_invoice_id
        self.nominal_amount_cents = nominal
        self.credited_amount_cents = None
        self.fee_cents = None
        self.transaction_ids = None
        self.status = InvoiceStatus.created
        self.credited_at = None


class TransferFake:
    def __init__(self, external_id, invoice_id, amount):
        self.external_id = external_id
        self.invoice_id = invoice_id
        self.amount_cents = amount
        self.status = TransferStatus.pending
        self.stark_transfer_id = None
        self.created_at = datetime.now(timezone.utc)
        self.confirmed_at = None


class InvoiceRepoFake:
    def __init__(self, invoices=()):
        self.por_stark_id = {i.stark_invoice_id: i for i in invoices}
        self.transfers_repo = None  # ligado pelo fixture, para o B1

    def save_all(self, invoices):
        for i in invoices:
            self.por_stark_id[i.stark_invoice_id] = i
        return list(invoices)

    def get_by_stark_id(self, stark_invoice_id):
        return self.por_stark_id.get(stark_invoice_id)

    def update_status(self, stark_invoice_id, status):
        inv = self.por_stark_id.get(stark_invoice_id)
        if inv:
            inv.status = status

    def mark_credited(self, stark_invoice_id, amount_cents, fee_cents, transaction_ids=None):
        inv = self.por_stark_id.get(stark_invoice_id)
        if not inv:
            return
        inv.status = InvoiceStatus.paid
        inv.credited_amount_cents = amount_cents
        inv.fee_cents = fee_cents
        inv.transaction_ids = transaction_ids
        inv.credited_at = datetime.now(timezone.utc)

    def credited_without_transfer(self, older_than):
        corte = datetime.now(timezone.utc) - older_than
        com_transfer = {t.invoice_id for t in self.transfers_repo.linhas.values()}
        return [i for i in self.por_stark_id.values()
                if i.credited_at is not None
                and i.credited_at < corte
                and i.id not in com_transfer]


class TransferRepoFake:
    def __init__(self):
        self.linhas = {}

    def insert_pending(self, external_id, invoice_id, amount_cents):
        """Devolve False se já existe — é o mutex do ON CONFLICT DO NOTHING."""
        if external_id in self.linhas:
            return False
        self.linhas[external_id] = TransferFake(external_id, invoice_id, amount_cents)
        return True

    def mark_created(self, external_id, stark_transfer_id):
        linha = self.linhas[external_id]
        linha.status = TransferStatus.created
        linha.stark_transfer_id = stark_transfer_id
        linha.confirmed_at = datetime.now(timezone.utc)

    def update_status_by_stark_id(self, stark_transfer_id, status):
        for linha in self.linhas.values():
            if linha.stark_transfer_id == stark_transfer_id:
                linha.status = status
                return True
        return False

    def exists(self, external_id):
        return external_id in self.linhas

    def get_by_external_id(self, external_id):
        return self.linhas.get(external_id)

    def pending(self, older_than):
        corte = datetime.now(timezone.utc) - older_than
        return [t for t in self.linhas.values()
                if t.status is TransferStatus.pending and t.created_at < corte]

    def release(self, external_id):
        linha = self.linhas.get(external_id)
        if linha and linha.status is TransferStatus.pending:
            del self.linhas[external_id]


class WebhookEventRepoFake:
    def __init__(self):
        self.eventos = {}

    def insert_if_new(self, *, stark_event_id, subscription, log_type, payload,
                      stark_invoice_id=None):
        if stark_event_id in self.eventos:
            return False
        self.eventos[stark_event_id] = {
            "subscription": subscription, "log_type": log_type,
            "payload": payload, "stark_invoice_id": stark_invoice_id,
            "processed_at": None,
        }
        return True

    def exists(self, stark_event_id):
        return stark_event_id in self.eventos

    def mark_processed(self, stark_event_id):
        self.eventos[stark_event_id]["processed_at"] = datetime.now(timezone.utc)

    def processados(self):
        return [k for k, v in self.eventos.items() if v["processed_at"]]


class ProducerFake:
    def __init__(self, falha=False):
        self.publicados = []
        self.falha = falha

    def publish(self, **kwargs):
        if self.falha:
            raise RuntimeError("fila indisponível")
        self.publicados.append(kwargs)


class StarkFake:
    def __init__(self, transfer_existente=None, falha_ao_criar=False,
                 nao_entregues=(), falha_ao_emitir=None):
        self.criadas = []
        self.invoices_criadas = []
        self.marcados_entregues = []
        self._existente = transfer_existente
        self._falha = falha_ao_criar
        self._nao_entregues = list(nao_entregues)
        self._falha_ao_emitir = falha_ao_emitir or set()

    # --- transfers ---
    def find_transfer_by_external_id(self, external_id):
        return self._existente

    def create_transfer(self, *, amount, external_id):
        if self._falha:
            raise RuntimeError("API do Stark indisponível")
        self.criadas.append({"amount": amount, "external_id": external_id})
        return type("T", (), {"id": f"stark-tr-{len(self.criadas)}"})()

    # --- invoices ---
    def create_invoices(self, payloads):
        criadas = []
        for p in payloads:
            if p["tax_id"] in self._falha_ao_emitir:
                raise RuntimeError("recusado pelo Stark")
            self.invoices_criadas.append(p)
            criadas.append(type("I", (), {
                "id": f"stark-inv-{len(self.invoices_criadas)}",
                "name": p["name"], "tax_id": p["tax_id"],
                "amount": p["amount"], "due": p["due"],
            })())
        return criadas

    # --- events ---
    def undelivered_events(self):
        return self._nao_entregues

    def mark_delivered(self, event_id):
        self.marcados_entregues.append(event_id)


def evento_invoice(event_id, log_type, invoice_id, amount=10_000, fee=0,
                   transaction_ids=(), com_invoice=True):
    """Monta um evento com a mesma forma do que o SDK devolve."""
    invoice = None
    if com_invoice:
        invoice = type("I", (), {
            "id": invoice_id, "amount": amount, "fee": fee,
            "transaction_ids": list(transaction_ids),
        })()
    log = type("L", (), {"type": log_type, "invoice": invoice})()
    return type("E", (), {"id": event_id, "subscription": "invoice", "log": log})()


def evento_transfer(event_id, log_type, transfer_id, errors=()):
    transfer = type("T", (), {"id": transfer_id, "errors": list(errors)})()
    log = type("L", (), {"type": log_type, "transfer": transfer})()
    return type("E", (), {"id": event_id, "subscription": "transfer", "log": log})()


@pytest.fixture
def repos():
    """Trio de repositórios ligados entre si, como no banco real."""
    invoices = InvoiceRepoFake()
    transfers = TransferRepoFake()
    invoices.transfers_repo = transfers
    return type("Repos", (), {
        "invoices": invoices, "transfers": transfers,
        "events": WebhookEventRepoFake(),
    })()


@pytest.fixture
def producer():
    return ProducerFake()


@pytest.fixture
def stark():
    return StarkFake()
