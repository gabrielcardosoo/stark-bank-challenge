"""Configuração de log da aplicação.

Escreve em dois destinos com níveis independentes:
  - arquivo rotativo, detalhado, a partir de DEBUG — o registro para investigar depois
  - console (stdout), enxuto, a partir de INFO — o que o Docker recolhe

Instala os handlers no logger raiz. Os módulos continuam usando
`logging.getLogger(__name__)` e herdam esses destinos automaticamente.

Uso, uma vez por processo, no entrypoint:
    Logger(file="logs/issuer.log")

Nos módulos:
    logger = logging.getLogger(__name__)
    logger.info("lote emitido: %d invoices", total)
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(filename)s:%(lineno)d] %(message)s"
CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


class Logger:
    """Níveis possíveis: DEBUG, INFO, WARNING, ERROR, CRITICAL."""

    _configured = False

    def __init__(
        self,
        file: str = "logs/stark.log",
        console_level: str = "INFO",
        file_level: str = "DEBUG",
        max_bytes: int = 5_242_880,
        backup_count: int = 5,
    ):
        # chamar duas vezes duplicaria cada linha de log
        if Logger._configured:
            return

        path = Path(file)
        path.parent.mkdir(parents=True, exist_ok=True)

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)  # captura tudo; cada handler filtra o seu

        file_handler = RotatingFileHandler(
            filename=path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(self._level(file_level))
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))

        # stdout, não stderr: é o que o Docker recolhe como saída normal
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self._level(console_level))
        console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))

        root.addHandler(file_handler)
        root.addHandler(console_handler)

        # o SQLAlchemy é verboso demais para o nível DEBUG do arquivo
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

        self.file = str(path)
        self.console_level = console_level
        self.file_level = file_level
        Logger._configured = True

    @staticmethod
    def _level(name: str) -> int:
        return getattr(logging, name.upper(), logging.DEBUG)

    @staticmethod
    def get(name: str) -> logging.Logger:
        """Atalho para `logging.getLogger(name)`, preservando o nome do módulo."""
        return logging.getLogger(name)

    @classmethod
    def reset(cls) -> None:
        """Remove os handlers e permite reconfigurar. Útil em teste."""
        root = logging.getLogger()
        for handler in root.handlers[:]:
            handler.close()
            root.removeHandler(handler)
        cls._configured = False
