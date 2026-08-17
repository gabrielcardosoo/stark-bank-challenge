"""Publica no tópico `invoices.credited`.

A key da mensagem é o id do invoice: garante que todas as mensagens de um mesmo
invoice caiam na mesma partição, e portanto sejam consumidas em ordem.
"""
import json
import logging

from confluent_kafka import Producer

from app.adapters.redpanda.config import RedpandaConfig

logger = logging.getLogger(__name__)


class CreditedInvoiceProducer:
    def __init__(self, config: RedpandaConfig | None = None):
        self._config = config or RedpandaConfig()
        self._producer = Producer({
            "bootstrap.servers": self._config.bootstrap_servers,
            # espera o ack de todas as réplicas antes de considerar entregue
            "acks": "all",
            "enable.idempotence": True,
        })
        logger.info(
            "producer pronto (topic=%s, servers=%s)",
            self._config.topic,
            self._config.bootstrap_servers,
        )

    def publish(self, *, stark_invoice_id: str, amount: int, fee: int, event_id: str) -> None:
        """Enfileira o crédito para o Worker criar a Transfer.

        Publica o id do invoice, não o id do banco: a mensagem não fica acoplada a
        chaves internas, e o Worker resolve a linha por `stark_invoice_id`.
        """
        mensagem = {
            "stark_invoice_id": stark_invoice_id,
            "amount": amount,
            "fee": fee,
            "event_id": event_id,
        }
        self._producer.produce(
            topic=self._config.topic,
            key=stark_invoice_id.encode("utf-8"),
            value=json.dumps(mensagem).encode("utf-8"),
            on_delivery=self._on_delivery,
        )
        # flush aqui, e não em background: o webhook precisa saber se a mensagem saiu
        # antes de marcar o evento como processado
        pendentes = self._producer.flush(timeout=10)
        if pendentes:
            raise RuntimeError(
                f"{pendentes} mensagem(ns) não confirmada(s) pelo Kafka "
                f"(invoice={stark_invoice_id})"
            )

    @staticmethod
    def _on_delivery(err, msg) -> None:
        if err is not None:
            logger.error("falha ao publicar no Kafka: %s", err)
        else:
            logger.info(
                "publicado em %s[%d] offset=%d key=%s",
                msg.topic(),
                msg.partition(),
                msg.offset(),
                msg.key().decode("utf-8"),
            )
