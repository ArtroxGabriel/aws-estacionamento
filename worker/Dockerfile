ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.12

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

FROM python:${PYTHON_VERSION}-slim

# Binário do Tesseract (o pytesseract só chama o executável) + dados em inglês,
# suficientes para o alfabeto A-Z/0-9 das placas. O tini roda como PID 1: sem
# ele o kernel descarta um SIGTERM que chegue antes de o Python instalar seus
# handlers, e o `docker stop` espera o timeout inteiro.
RUN apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr tini && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin worker

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY config.py worker.py ./
COPY ocr ./ocr
COPY storage ./storage

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001

# O Poller trata SIGTERM/SIGINT (Req 16): interrompe o long poll na hora,
# termina a mensagem em processamento e devolve o resto do lote à fila.
STOPSIGNAL SIGTERM

ENTRYPOINT ["tini", "--", "python", "worker.py"]
