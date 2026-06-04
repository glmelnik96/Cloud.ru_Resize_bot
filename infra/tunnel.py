"""Cloudflare Tunnel manager — wraps `cloudflared` as a subprocess.

Why:
- Local static HTTP (infra/http_server.py) needs a public HTTPS hostname so
  Figma Make / Phygital can fetch generated images by URL. Cloudflare Tunnel
  gives a free `*.trycloudflare.com` quick-tunnel without account, or a named
  tunnel via TUNNEL_TOKEN.

Modes:
- Quick tunnel (default if no token): `cloudflared tunnel --url http://localhost:PORT`
  Output line "https://xxx-yyy-zzz.trycloudflare.com" is parsed and used as
  the public base URL.
- Named tunnel: when TUNNEL_TOKEN env is set, `cloudflared tunnel run --token …`.
  Then PUBLIC_BASE_URL must be provided directly (the hostname is configured
  in Cloudflare Zero Trust dashboard).

Lifecycle:
- start_tunnel() spawns the subprocess and waits for the URL line (quick mode)
  or returns env-provided URL immediately (named mode). Times out after
  TUNNEL_BOOT_TIMEOUT (default 30s).
- stop_tunnel() terminates the process.

Caveat:
- `cloudflared` binary must be on PATH inside the container. M3.0 scaffolding
  only — actual install (apt or static binary) is wired in Dockerfile when
  we move from stubs to real Phygital adapter.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)

_QUICK_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


@dataclass
class TunnelHandle:
    process: asyncio.subprocess.Process | None
    public_url: str
    mode: str  # quick | named | disabled


async def start_tunnel(local_port: int) -> TunnelHandle:
    """Start cloudflared or return a disabled handle.

    Env:
      TUNNEL_MODE: quick | named | disabled (default: disabled in M3.0 scaffold)
      TUNNEL_TOKEN: required if mode=named
      PUBLIC_BASE_URL: required if mode=named (the routed hostname)
      TUNNEL_BOOT_TIMEOUT: seconds (default 30)
    """
    mode = os.environ.get("TUNNEL_MODE", "disabled").lower()
    if mode == "disabled":
        log.info("tunnel_disabled", note="set TUNNEL_MODE=quick or named to enable")
        return TunnelHandle(process=None, public_url="", mode="disabled")

    if mode == "named":
        token = os.environ.get("TUNNEL_TOKEN", "")
        public = os.environ.get("PUBLIC_BASE_URL", "")
        if not token or not public:
            raise RuntimeError(
                "tunnel mode=named requires TUNNEL_TOKEN and PUBLIC_BASE_URL"
            )
        proc = await asyncio.create_subprocess_exec(
            "cloudflared",
            "tunnel",
            "run",
            "--token",
            token,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        log.info("tunnel_started", mode="named", public_url=public, pid=proc.pid)
        return TunnelHandle(process=proc, public_url=public.rstrip("/"), mode="named")

    if mode == "quick":
        timeout = float(os.environ.get("TUNNEL_BOOT_TIMEOUT", "30"))
        proc = await asyncio.create_subprocess_exec(
            "cloudflared",
            "tunnel",
            "--url",
            f"http://localhost:{local_port}",
            "--no-autoupdate",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        url = await _wait_for_quick_url(proc, timeout)
        if not url:
            proc.terminate()
            raise RuntimeError("cloudflared did not emit a trycloudflare URL in time")
        log.info("tunnel_started", mode="quick", public_url=url, pid=proc.pid)
        return TunnelHandle(process=proc, public_url=url.rstrip("/"), mode="quick")

    raise ValueError(f"unknown TUNNEL_MODE={mode!r}")


async def _wait_for_quick_url(
    proc: asyncio.subprocess.Process, timeout: float
) -> str | None:
    assert proc.stdout is not None
    end = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = end - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None
        try:
            line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        if not line_bytes:
            return None
        line = line_bytes.decode("utf-8", errors="replace")
        log.debug("cloudflared_stdout", line=line.rstrip())
        m = _QUICK_URL_RE.search(line)
        if m:
            return m.group(0)


async def stop_tunnel(handle: TunnelHandle) -> None:
    if handle.process is None:
        return
    try:
        handle.process.terminate()
        try:
            await asyncio.wait_for(handle.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            handle.process.kill()
        log.info("tunnel_stopped", mode=handle.mode)
    except Exception as exc:  # noqa: BLE001
        log.warning("tunnel_stop_failed", error=str(exc))
