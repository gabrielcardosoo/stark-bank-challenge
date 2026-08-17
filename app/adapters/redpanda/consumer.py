"""Consome o tópico `invoices.credited`.

O commit do offset é **manual e explícito**: quem processou a mensagem chama `commit()`
depois de terminar. Com auto-commit, o offset avançaria em background e um crash entre
o avanço e o processamento perderia a mensagem — ou seja, um crédito nunca transferido.
"""
import json
import logging

from confluent_kafka import Consumer, KafkaError

from app.adapters.redpanda.config import RedpandaConfig

logger = logging.getLogger(__name__)


class CreditedInvoiceConsumer:
    def __init__(self, config: RedpandaConfig | None = None):
        self._config = config or RedpandaConfig()
        self._consumer = Consumer({
            "bootstrap.servers": self._config.bootstrap_servers,
            "group.id": self._config.group_id,
            # sem isto o offset avança sozinho a cada 5s, antes de o trabalho terminar
            "enable.auto.commit": False,
            # ao entrar no grupo pela primeira vez, começa do início da fila
            "auto.offset.reset": "earliest",
        })
        self._consumer.subscribe([self._config.topic])
        logger.info(
            "consumer pronto (topic=%s, group=%s, servers=%s)",
            self._config.topic,
            self._config.group_id,
            self._config.bootstrap_servers,
        )

    def messages(self, timeout: float = 1.0):
        """Itera indefinidamente sobre as mensagens já decodificadas.

        Bloqueia no broker esperando a próxima — não é polling: a mensagem chega em
        milissegundos após ser publicada, sem consulta ociosa.
        """
        while True:
            msg = self._consumer.poll(timeout)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("erro ao consumir: %s", msg.error())
                continue

            try:
                yield json.loads(msg.value())
            except (json.JSONDecodeError, UnicodeDecodeError):
                # poison message: sem o commit, ela voltaria para sempre e travaria a
                # partição. Descartar é a única saída — mas registrando o que se perdeu.
                logger.error(
                    "mensagem malformada descartada (offset=%d): %r",
                    msg.offset(),
                    msg.value(),
                )
                self.commit()

    def commit(self) -> None:
        """Confirma o offset. Só depois da Transfer criada e persistida."""
        self._consumer.commit(asynchronous=False)

    def close(self) -> None:
        """Sai do grupo de forma limpa, sem esperar o timeout de sessão."""
        self._consumer.close()

    def __enter__(self) -> "CreditedInvoiceConsumer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
