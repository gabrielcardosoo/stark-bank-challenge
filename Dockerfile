FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN pip install --no-cache-dir poetry

# dependências antes do código: só reinstala quando o lock muda
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

COPY app ./app
COPY scripts ./scripts

# nenhum CMD padrão: cada serviço do compose declara o seu entrypoint
CMD ["python", "-c", "print('escolha um entrypoint: issuer, api, worker ou reconciler')"]
