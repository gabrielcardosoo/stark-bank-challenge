"""Conexão e sessão com o Postgres."""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.db.config import DatabaseConfig
from app.adapters.db.models import create_tables

_config = DatabaseConfig()

engine = create_engine(_config.database_url, pool_pre_ping=True, future=True)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commita ao sair sem erro, faz rollback em qualquer exceção."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database() -> None:
    """Cria as tabelas. Chamada explicitamente por um entrypoint, nunca no import."""
    create_tables(engine)
