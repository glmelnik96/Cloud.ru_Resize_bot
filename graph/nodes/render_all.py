"""render_all node — packages all rendered per-format PNGs into a single ZIP.

Input:
  - GraphState.rendered_files (list[dict] from fill_templates_per_format)
  - GraphState.session_id
Output:
  - GraphState.rendered_zip_path (str, container path under /data/zips)

This is the terminal artifact the bot ships to TG via send_document.
"""

from __future__ import annotations

import asyncio
import os
import zipfile
from datetime import datetime
from pathlib import Path

import structlog

from graph.state import GraphState

log = structlog.get_logger(__name__)

# Default to the Docker bot's bind-mounted /data/zips; App3 (web sub-app on the
# VM, no /data) overrides via ZIPS_DIR so outputs land under its WorkingDirectory.
_ZIP_DIR = Path(os.environ.get("ZIPS_DIR", "/data/zips"))


async def render_all(state: GraphState) -> dict:
    session_id = state.get("session_id") or "nosession"
    files = state.get("rendered_files") or []
    if not files:
        raise ValueError("render_all: state.rendered_files is empty")

    _ZIP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    zip_path = _ZIP_DIR / f"{session_id}_{ts}.zip"

    # Compression is CPU-bound; build the archive in a worker thread so
    # the event loop is not blocked.
    await asyncio.to_thread(_build_zip_sync, zip_path, files)

    log.info(
        "render_all_ok",
        session_id=session_id,
        zip_path=str(zip_path),
        n_files=len(files),
        size_bytes=zip_path.stat().st_size,
    )
    return {"rendered_zip_path": str(zip_path)}


def _build_zip_sync(zip_path: Path, files: list[dict]) -> None:
    """Write the ZIP archive. Runs in a worker thread."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in files:
            src = Path(entry["path"])
            arcname = f"{entry['format']}{src.suffix}"
            zf.write(src, arcname=arcname)
