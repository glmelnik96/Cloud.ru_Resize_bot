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
RUN uv pip install --system -r pyproject.toml

RUN playwright install --with-deps chromium

COPY bot/ ./bot/
COPY llm/ ./llm/
COPY graph/ ./graph/
COPY prompts/ ./prompts/

RUN mkdir -p /data/user_data /data/traces

CMD ["python", "-m", "bot.app"]
