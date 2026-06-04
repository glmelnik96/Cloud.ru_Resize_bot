"""Phygital+ client bootstrap + singleton holder.

Why a separate module:
  - Vendored phygital_vendor/ uses internal imports `from client.api import ...`
    and `from workflows.X import ...`. We prepend phygital_vendor/ to sys.path
    here (once) so those resolve without touching vendor code.
  - PhygitalClient itself is an async-context-manager (opens httpx.AsyncClient
    in __aenter__, closes in __aexit__). For a long-lived TG bot we open it
    once at startup, hold the handle in bot_data, close at shutdown — same
    pattern we already use for static-HTTP / tunnel in M3.0 Direction 2.
  - PHYGITAL_ENABLED=false toggles the whole integration off: get_client()
    returns None and generate_image falls back to the M3.0 PIL stub. Useful
    for CI / when session.json isn't bootstrapped yet.

Bootstrap of session.json:
  Phygital-bot keeps its SuperTokens session at storage/session.json. In our
  deployment we mount /data/phygital_storage as a docker volume and tell vendor
  via PHYGITAL_STORAGE_DIR=/data/phygital_storage. Operator copies the live
  session.json into that volume once (host: `docker cp Phygital-bot/storage/
  session.json resize-bot:/data/phygital_storage/session.json`); after that
  SessionManager.refresh() keeps it alive.

  If session.json is missing or has no access_token, get_client() returns None
  and logs a clear warning — bot keeps running on the stub.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Resolve vendor dir relative to this file: /app/infra/phygital_client.py
# → /app/phygital_vendor.
_VENDOR_DIR = Path(__file__).resolve().parent.parent / "phygital_vendor"


def _bootstrap_sys_path() -> None:
    """Prepend phygital_vendor/ to sys.path so `from client.api import ...`
    inside vendor resolves. Idempotent."""
    p = str(_VENDOR_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


# A module-level singleton: opened PhygitalClient or None if disabled/unbootstrapped.
_client_handle: Any = None
_session_manager: Any = None


def is_enabled() -> bool:
    """PHYGITAL_ENABLED=false (case-insensitive) disables the integration."""
    return os.environ.get("PHYGITAL_ENABLED", "true").lower() not in ("false", "0", "no", "off")


async def start_phygital_client() -> Any | None:
    """Load session.json, construct PhygitalClient, open httpx AsyncClient,
    return the open handle. Returns None if disabled or session unavailable.

    Idempotent: subsequent calls return the cached handle.
    """
    global _client_handle, _session_manager

    if _client_handle is not None:
        return _client_handle

    if not is_enabled():
        log.info("phygital_disabled_by_env", reason="PHYGITAL_ENABLED=false")
        return None

    _bootstrap_sys_path()

    # Late imports — only after sys.path is patched.
    from client.api import PhygitalClient  # type: ignore[import-not-found]
    from client.config import settings  # type: ignore[import-not-found]
    from client.session import SessionManager  # type: ignore[import-not-found]

    storage_path: Path = settings.session_file
    if not storage_path.exists():
        log.warning(
            "phygital_session_missing",
            path=str(storage_path),
            hint=(
                "Bootstrap: copy Phygital-bot/storage/session.json into the "
                "phygital_storage docker volume. Falling back to stub image."
            ),
        )
        return None

    mgr = SessionManager(storage_path)
    session = mgr.load()
    if session is None or not session.access_token:
        log.warning(
            "phygital_session_invalid",
            path=str(storage_path),
            hint="No st-access-token in session.json. Falling back to stub.",
        )
        return None

    client = PhygitalClient(session, session_manager=mgr)
    try:
        await client.__aenter__()
    except Exception as exc:
        log.warning("phygital_client_open_failed", error=str(exc))
        return None

    _client_handle = client
    _session_manager = mgr
    log.info(
        "phygital_client_ready",
        session_path=str(storage_path),
        access_token_ttl_s=session.jwt_ttl_seconds(),
    )
    return client


async def stop_phygital_client() -> None:
    """Close the held PhygitalClient (httpx.AsyncClient.aclose under the hood)."""
    global _client_handle, _session_manager
    if _client_handle is None:
        return
    try:
        await _client_handle.__aexit__(None, None, None)
    except Exception as exc:
        log.warning("phygital_client_close_failed", error=str(exc))
    _client_handle = None
    _session_manager = None


def get_client() -> Any | None:
    """Return the active PhygitalClient handle, or None if integration is off /
    not bootstrapped. Callers (generate_image) check None and fall back to stub.
    """
    return _client_handle
