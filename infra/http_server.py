"""Local static HTTP server for serving generated images & rendered PNGs.

Why this exists:
- Figma Make's createImageAsync (and Phygital's reference-image upload via URL)
  needs a *public* HTTPS URL, not a container path. We serve files from /data
  locally and expose them through a Cloudflare Tunnel (see infra/tunnel.py).

Design:
- Stdlib http.server (ThreadingHTTPServer) in a background thread — no new deps,
  no event-loop entanglement with PTB's asyncio.
- Read-only: only GET on whitelisted prefixes /images/ and /renders/.
- Path traversal blocked: resolved path must stay inside the served root.

Env:
- HTTP_BIND (default 0.0.0.0)
- HTTP_PORT (default 8088)
- HTTP_ROOT (default /data) — must contain `images/` and `renders/` subdirs.

Caveat:
- This is a development-grade static server. Do not expose without the tunnel
  (it has zero auth). Cloudflare Tunnel adds the public hostname; access
  control to that hostname is a separate concern (M3.1+).
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_ALLOWED_PREFIXES = ("/images/", "/renders/", "/zips/")
_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".zip": "application/zip",
}


class _Handler(BaseHTTPRequestHandler):
    root: Path = Path("/data")  # overridden per-server in start_static_server

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # silence stdout spam; structlog records on actual serve below
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not any(path.startswith(p) for p in _ALLOWED_PREFIXES):
            self.send_error(404, "not found")
            return
        rel = path.lstrip("/")
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            self.send_error(403, "forbidden")
            return
        if not target.is_file():
            self.send_error(404, "not found")
            return
        ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        try:
            data = target.read_bytes()
        except OSError:
            self.send_error(500, "read failed")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)
        log.info("http_serve", path=path, bytes=len(data))


def start_static_server() -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    """Start the static server in a daemon thread. Returns (server, thread, port)."""
    bind = os.environ.get("HTTP_BIND", "0.0.0.0")
    port = int(os.environ.get("HTTP_PORT", "8088"))
    root = Path(os.environ.get("HTTP_ROOT", "/data"))
    root.mkdir(parents=True, exist_ok=True)

    handler_cls = type("_BoundHandler", (_Handler,), {"root": root})
    server = ThreadingHTTPServer((bind, port), handler_cls)
    thread = threading.Thread(
        target=server.serve_forever,
        name="static-http",
        daemon=True,
    )
    thread.start()
    log.info("http_server_started", bind=bind, port=port, root=str(root))
    return server, thread, port


def stop_static_server(server: ThreadingHTTPServer) -> None:
    try:
        server.shutdown()
        server.server_close()
        log.info("http_server_stopped")
    except Exception as exc:  # noqa: BLE001
        log.warning("http_server_stop_failed", error=str(exc))
