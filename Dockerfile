FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app

COPY pyproject.toml ./
# Bot needs the core deps + the [bot] group (PTB + Redis checkpointer).
RUN uv pip install --system -r pyproject.toml --group bot

COPY bot/ ./bot/
COPY llm/ ./llm/
COPY graph/ ./graph/
COPY agents/ ./agents/
COPY prompts/ ./prompts/
COPY infra/ ./infra/
COPY config/ ./config/
COPY assets/ ./assets/

RUN mkdir -p /data/traces /data/heroes /data/renders /data/zips

CMD ["python", "-m", "bot.app"]
