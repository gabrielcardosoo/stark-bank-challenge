"""Configuração do adapter de fila.

Redpanda fala o protocolo do Kafka, por isso o cliente continua sendo o
`confluent_kafka` — o que muda é só o broker do outro lado.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedpandaConfig(BaseSettings):
    """Lê REDPANDA_BOOTSTRAP_SERVERS, REDPANDA_TOPIC e REDPANDA_GROUP_ID do .env."""

    model_config = SettingsConfigDict(
        env_prefix="REDPANDA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 29092 é o listener externo (host); dentro do compose é redpanda:9092
    bootstrap_servers: str = "localhost:29092"
    topic: str = "invoices.credited"
    group_id: str = "worker-transfer"
    # API de administração, usada só pelo script de setup
    admin_url: str = "http://localhost:9644"
