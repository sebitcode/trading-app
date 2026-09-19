FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN pip install --no-cache-dir "uv==0.11.21"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev \
    && mkdir -p /app/data \
    && chown -R 10001:10001 /app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "crypto_orchestrator.api:app", "--host", "0.0.0.0", "--port", "8000"]
