"""AsyncRedisSaver factory for LangGraph checkpoints.

Single shared Redis Stack instance (the `redis` service in docker-compose).
We open the saver via `from_conn_string` async context manager and keep it
alive for the bot's lifetime — the underlying connection pool handles
reconnects.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

log = structlog.get_logger(__name__)


@asynccontextmanager
async def open_checkpointer(
    redis_url: str | None = None,
) -> AsyncIterator[AsyncRedisSaver]:
    """Yield an initialised AsyncRedisSaver. Sets up RedisJSON indexes on first use."""
    url = redis_url or os.environ.get("REDIS_URL", "redis://redis:6379/0")
    log.info("checkpointer_open", redis_url=url)
    async with AsyncRedisSaver.from_conn_string(url) as saver:
        await saver.asetup()
        yield saver
