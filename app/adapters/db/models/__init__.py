"""Todos os modelos precisam ser importados aqui.

O `create_tables` só cria as tabelas registradas em `Base.metadata`, e o registro
acontece no import da classe. Um modelo que não aparece nesta lista simplesmente
não é criado no banco.
"""
from .base import Base, create_tables
from .invoice import Invoice, InvoiceStatus
from .transfer import Transfer, TransferStatus
from .webhook_event import WebhookEvent

__all__ = [
    "Base",
    "create_tables",
    "Invoice",
    "InvoiceStatus",
    "Transfer",
    "TransferStatus",
    "WebhookEvent",
]
