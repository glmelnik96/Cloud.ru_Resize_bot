"""Figma MCP client (streamable HTTP) + module-level singleton holder.

We talk to the user's local Figma Desktop MCP at host.docker.internal:3845.
Auth comes free from the logged-in Desktop Figma; no token/recon needed.

Singleton pattern mirrors infra/phygital_client.py: started in
bot.app._post_init, stopped in _post_shutdown, accessed via get_client().
The graph node calls get_client() and falls back to PIL if it returns None
(MCP unreachable, FIGMA_MCP_URL empty, etc).

All MCP requests are serialised with an asyncio.Lock — a single Figma file
can only host one upload+set+export sequence at a time without race
conditions on intermediate state. The lock is global rather than per-frame
because the simpler shape is fine until parallel /new sessions exist.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any

import structlog

log = structlog.get_logger(__name__)


_client_handle: "FigmaMCPClient | None" = None


def _mcp_url() -> str:
    return os.environ.get("FIGMA_MCP_URL", "").strip()


class FigmaMCPClient:
    """Async MCP client wrapper. Owns the streamable-http session + lock."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._session: Any = None  # mcp.ClientSession; typed loosely to avoid heavy import at top

    async def connect(self) -> None:
        """Open the streamable-http transport and initialise the MCP session.
        Raises on connection failure — caller decides whether to fall back."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        stack = AsyncExitStack()
        read, write, _ = await stack.enter_async_context(streamablehttp_client(self._url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack = stack
        self._session = session

    async def close(self) -> None:
        """Tear down the MCP session + transport. Safe to call multiple times."""
        if self._stack is None:
            return
        try:
            await self._stack.aclose()
        except Exception as exc:  # noqa: BLE001 — close must not raise
            log.warning("figma_mcp_close_failed", error=str(exc))
        self._stack = None
        self._session = None


async def start_figma_mcp_client() -> FigmaMCPClient | None:
    """Bootstrap: if FIGMA_MCP_URL is empty, return None (graceful disable).
    Otherwise connect and cache the singleton. Returns None on connect failure
    too — the node will fall back to PIL."""
    global _client_handle
    if _client_handle is not None:
        return _client_handle
    url = _mcp_url()
    if not url:
        log.info("figma_mcp_disabled", reason="FIGMA_MCP_URL empty")
        return None
    client = FigmaMCPClient(url)
    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001 — boot must never crash on MCP
        log.warning("figma_mcp_unavailable", url=url, error=str(exc), error_type=type(exc).__name__)
        return None
    _client_handle = client
    log.info("figma_mcp_ready", url=url)
    return client


async def stop_figma_mcp_client() -> None:
    """Tear down the singleton. Safe when never started."""
    global _client_handle
    client = _client_handle
    _client_handle = None
    if client is None:
        return
    await client.close()


def get_client() -> "FigmaMCPClient | None":
    """Return the active client, or None if MCP is disabled / unreachable."""
    return _client_handle
