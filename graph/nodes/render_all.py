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
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

import structlog

from graph.provenance import build_provenance
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

    # Паспорт происхождения — best-effort: его сбой не роняет сборку ZIP.
    provenance: bytes | None = None
    try:
        prov = build_provenance(dict(state), rendered_files=files)
        provenance = json.dumps(prov, ensure_ascii=False, indent=2).encode("utf-8")
    except Exception as exc:  # noqa: BLE001 — паспорт опционален
        log.warning(
            "provenance_build_failed", session_id=session_id, error=str(exc)
        )

    # Compression is CPU-bound; build the archive in a worker thread so
    # the event loop is not blocked.
    await asyncio.to_thread(_build_zip_sync, zip_path, files, provenance)

    log.info(
        "render_all_ok",
        session_id=session_id,
        zip_path=str(zip_path),
        n_files=len(files),
        size_bytes=zip_path.stat().st_size,
    )
    return {"rendered_zip_path": str(zip_path)}


def _build_zip_sync(
    zip_path: Path, files: list[dict], provenance: bytes | None = None
) -> None:
    """Write the ZIP archive. Runs in a worker thread.

    Alpha layer artifacts (Block 1, 2026-07-10) go under layers/ so the
    top-level of the archive — and the results-dir *.png glob that feeds the
    web grid — stays composites-only. provenance.json (блок 4, 2026-08-07) —
    паспорт происхождения в корне архива."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if provenance is not None:
            zf.writestr("provenance.json", provenance)
        for entry in files:
            src = Path(entry["path"])
            arcname = f"{entry['format']}{src.suffix}"
            zf.write(src, arcname=arcname)
            for name, lpath in (entry.get("layers") or {}).items():
                lsrc = Path(lpath)
                if lsrc.exists():
                    zf.write(lsrc, arcname=f"layers/{entry['format']}_{name}{lsrc.suffix}")
