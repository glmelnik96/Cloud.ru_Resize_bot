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
import json
import os
import re
from contextlib import AsyncExitStack
from typing import Any

import structlog

log = structlog.get_logger(__name__)


_client_handle: FigmaMCPClient | None = None


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

    async def upload_hero(self, *, file_key: str, node_id: str, png_bytes: bytes) -> None:
        """Upload hero PNG and bind as fill on `node_id`.

        Figma's upload_assets is a two-phase protocol: MCP returns a
        short-lived presigned URL, and the caller POSTs the raw image bytes
        to it. We do NOT pass bytes through MCP — that would force JSON
        encoding of binary and fail with UnicodeDecodeError on the PNG
        magic byte (0x89). Serialised with the client lock so concurrent
        sessions can't interleave.
        """
        import httpx

        async with self._lock:
            assert self._session is not None, "FigmaMCPClient.connect() not called"
            result = await self._session.call_tool(
                "upload_assets",
                {
                    "fileKey": file_key,
                    "count": 1,
                    "nodeId": node_id,
                    "scaleMode": "FILL",
                },
            )
        upload_url = _extract_url(result)
        if not upload_url:
            raise RuntimeError(f"figma_upload_no_url: result={result!r}")
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
            resp = await http.post(
                upload_url,
                content=png_bytes,
                headers={"Content-Type": "image/png"},
            )
            resp.raise_for_status()

    async def set_texts(
        self,
        *,
        file_key: str,
        replacements: list[tuple[str, str]],
    ) -> None:
        """Replace `.characters` on each (node_id, text) pair in one JS call.

        Empty replacements list is a no-op (no MCP round-trip).
        """
        if not replacements:
            return
        # Build one JS snippet that loads each node and writes characters.
        # Both node_id and text are escaped for "..." JS string literals
        # (backslash first to avoid double-escaping the quotes we add next).
        lines = []
        for node_id, text in replacements:
            esc_id = node_id.replace("\\", "\\\\").replace('"', '\\"')
            esc = (
                text.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\r", "")
            )
            lines.append(
                f'{{ const n = figma.getNodeById("{esc_id}"); '
                f'if (n && "characters" in n) {{ n.characters = "{esc}"; }} }}'
            )
        code = "\n".join(lines)
        async with self._lock:
            assert self._session is not None, "FigmaMCPClient.connect() not called"
            await self._session.call_tool(
                "use_figma",
                {
                    "fileKey": file_key,
                    "code": code,
                    # `description` is required by the MCP tool schema.
                    "description": "Resize_bot: set slogan/cta characters on template text nodes",
                },
            )

    async def export_frame(
        self, *, file_key: str, node_id: str, max_dim: int
    ) -> bytes:
        """Request a PNG screenshot of `node_id` and return its bytes.

        MCP returns a short-lived URL; we follow it with httpx (30s timeout)
        and hand back the raw bytes for the node to write to disk.
        """
        import httpx

        async with self._lock:
            assert self._session is not None, "FigmaMCPClient.connect() not called"
            result = await self._session.call_tool(
                "get_screenshot",
                {"fileKey": file_key, "nodeId": node_id, "maxDimension": max_dim},
            )
        url = _extract_url(result)
        if not url:
            raise RuntimeError(f"figma_export_no_url: result={result!r}")
        # follow_redirects=True — Figma's CDN URL routinely 302s to the S3 origin.
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            return resp.content


_URL_RE = re.compile(r'https?://[^\s\'"<>)\]]+')


def _extract_url(result: Any) -> str | None:
    """Pull a URL out of an MCP tool result.

    Real MCP CallToolResult is a Pydantic model whose `content` is a list
    of TextContent (or similar). Each TextContent has `.text` carrying the
    payload — for upload_assets that's a JSON blob with an upload URL, for
    get_screenshot a human-readable string with the short-lived URL and a
    curl snippet. We handle both, plus the legacy dict shape the unit
    tests use ({'url': ...} / {'content': [{'url': ...}]}). Returns None
    on miss — caller raises.
    """
    # 1) Direct dict shape (legacy fakes used in tests).
    if isinstance(result, dict):
        if isinstance(result.get("url"), str):
            return result["url"]
        if isinstance(result.get("uploadUrl"), str):
            return result["uploadUrl"]
        content = result.get("content")
    else:
        # 2) Pydantic-style CallToolResult.content
        content = getattr(result, "content", None)

    if not isinstance(content, list):
        return None

    for block in content:
        # 2a) Dict-shaped block carrying a URL directly (legacy fakes).
        if isinstance(block, dict):
            for key in ("url", "uploadUrl"):
                v = block.get(key)
                if isinstance(v, str):
                    return v
        # 2b) TextContent-style block — pull JSON or regex a URL out of .text
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if not isinstance(text, str):
            continue
        # Try JSON-decoded payload first (upload_assets returns structured).
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        url = _scan_dict_for_url(data)
        if url:
            return url
        # Plain-text fallback (get_screenshot embeds the URL in prose).
        m = _URL_RE.search(text)
        if m:
            return m.group(0)
    return None


def _scan_dict_for_url(data: Any) -> str | None:
    """Walk a JSON-decoded MCP payload looking for an upload/screenshot URL.
    Handles the common keys we've seen: 'url', 'uploadUrl', 'urls[]',
    'uploads[].uploadUrl', 'assets[].url'."""
    if not isinstance(data, dict):
        return None
    for key in ("url", "uploadUrl", "screenshotUrl", "downloadUrl"):
        v = data.get(key)
        if isinstance(v, str):
            return v
    urls = data.get("urls")
    if isinstance(urls, list) and urls and isinstance(urls[0], str):
        return urls[0]
    for list_key in ("uploads", "assets"):
        lst = data.get(list_key)
        if isinstance(lst, list) and lst and isinstance(lst[0], dict):
            for key in ("uploadUrl", "url"):
                v = lst[0].get(key)
                if isinstance(v, str):
                    return v
    return None


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
    # Hard 5s ceiling: if Figma Desktop is closed or the MCP handshake stalls
    # (TCP accepted but no init reply), we must not block app boot. Without
    # this wait_for, a hung session.initialize() takes down the whole bot
    # because PTB's network loop cancels it and CancelledError doesn't
    # inherit from Exception.
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)
    except (Exception, asyncio.TimeoutError) as exc:  # noqa: BLE001 — boot must never crash on MCP
        log.warning("figma_mcp_unavailable", url=url, error=str(exc), error_type=type(exc).__name__)
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass
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


def get_client() -> FigmaMCPClient | None:
    """Return the active client, or None if MCP is disabled / unreachable."""
    return _client_handle
