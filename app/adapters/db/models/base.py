from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def create_tables(engine) -> None:
    """Cria as tabelas que ainda não existem.

    Substitui as migrations: o schema nasce uma vez e não muda durante a execução.
    Alterar uma coluna depois exige dropar e recriar — sem custo enquanto não há
    dado que importe preservar.
    """
    Base.metadata.create_all(engine)
