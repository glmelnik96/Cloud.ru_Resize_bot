"""fill_templates_per_format node — M3.3 PIL composer.

For each slug in brief.formats:
  - Look up the spec in config/templates.json,
  - Compose canvas via infra.composer (hero + slogan + cta + age_rating),
  - Save PNG to /data/renders/, append {format, path} to rendered_files.

Per-format try/except: one bad slug never kills the rest. Slugs missing
from the manifest are skipped with a warning (parse_brief is supposed to
emit only whitelisted slugs, but we are defensive here).

Contract:
  state.brief.formats × state.image (hero) × top-ranked proposition (text,
  via chosen_candidate) → state.rendered_files = [{format, path}, ...]
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import structlog

from graph.nodes import chosen_candidate
from graph.state import AdBrief, GeneratedImage, GraphState
from infra.composer import compose
from infra.template_manifest import TemplateManifest, load_manifest

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Default to the Docker bot's /data/renders; App3 overrides via RENDERS_DIR.
_RENDER_DIR = Path(os.environ.get("RENDERS_DIR", "/data/renders"))
_MANIFEST_PATH = _REPO_ROOT / "config" / "templates.json"
_DEFAULT_FORMAT = "banner_300x250"


async def fill_templates_per_format(state: GraphState) -> dict:
    session_id = state.get("session_id") or "nosession"

    brief_raw = state.get("brief")
    image_raw = state.get("image")
    if image_raw is None:
        raise ValueError("fill_templates_per_format: state.image is None")

    brief = _coerce(brief_raw, AdBrief, "brief") if brief_raw else None
    image = _coerce(image_raw, GeneratedImage, "image")
    winner = chosen_candidate(state)

    formats = (brief.formats if brief else []) or [_DEFAULT_FORMAT]
    age_rating = brief.age_rating if brief else "0+"

    _RENDER_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(_MANIFEST_PATH)
    hero_bytes = Path(image.local_path).read_bytes()
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    texts = {
        "slogan": winner.slogan,
        "cta": winner.cta,
        "age_rating": age_rating,
    }

    start = datetime.utcnow()

    # Filter unknown slugs first, then render the known ones in parallel:
    # compose() is CPU-bound PIL work, so each format goes to a worker
    # thread via asyncio.to_thread and the event loop stays responsive.
    # Hero is passed as bytes — each thread opens its own PIL.Image from
    # them, so no Image object is shared across threads. gather() keeps
    # the result order equal to the formats order.
    known: list[str] = []
    for fmt in formats:
        spec = manifest.templates.get(fmt)
        if spec is None:
            log.warning(
                "compose_unknown_slug",
                session_id=session_id,
                slug=fmt,
                known=list(manifest.templates),
            )
            continue
        known.append(fmt)

    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                _render_one_sync,
                manifest.templates[fmt],
                fmt,
                hero_bytes,
                texts,
                session_id,
                ts,
            )
            for fmt in known
        )
    )
    out: list[dict] = [r for r in results if r is not None]

    log.info(
        "compose_node_done",
        session_id=session_id,
        n_total=len(out),
        n_requested=len(formats),
        node_latency_ms=int((datetime.utcnow() - start).total_seconds() * 1000),
    )
    return {"rendered_files": out}


def _render_one_sync(
    spec,
    fmt: str,
    hero_bytes: bytes,
    texts: dict[str, str],
    session_id: str,
    ts: str,
) -> dict | None:
    """Compose + save one format. Runs in a worker thread.

    Returns {format, path} on success, None on failure (per-format
    isolation: one bad slug never kills the rest).
    """
    fmt_start = datetime.utcnow()
    try:
        canvas = compose(
            spec,
            hero=hero_bytes,
            texts=texts,
            assets_root=_REPO_ROOT,
            slug=fmt,
        )
        path = _RENDER_DIR / f"{session_id}_{fmt}_{ts}.png"
        canvas.save(path, format="PNG", optimize=True)
        log.info(
            "compose_format_ok",
            session_id=session_id,
            slug=fmt,
            latency_ms=int(
                (datetime.utcnow() - fmt_start).total_seconds() * 1000
            ),
        )
        return {"format": fmt, "path": str(path)}
    except Exception as exc:  # noqa: BLE001 — single-format isolation
        log.error(
            "compose_format_error",
            session_id=session_id,
            slug=fmt,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
