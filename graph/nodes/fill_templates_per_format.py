"""fill_templates_per_format node — M3.3 PIL composer.

For each slug in brief.formats:
  - Look up the spec in config/templates.json,
  - Compose canvas via infra.composer (hero + slogan + cta + age_rating),
  - Save PNG to /data/renders/, append {format, path} to rendered_files.

Per-format try/except: one bad slug never kills the rest. Slugs missing
from the manifest are skipped with a warning (parse_brief is supposed to
emit only whitelisted slugs, but we are defensive here).

Contract unchanged from M3.0/M3.2:
  state.brief.formats × state.image (hero) × state.winner (text) →
  state.rendered_files = [{format, path}, ...]
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import structlog

from graph.state import AdBrief, GeneratedImage, GraphState, MessageCandidate
from infra.composer import compose
from infra.template_manifest import TemplateManifest, load_manifest

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RENDER_DIR = Path("/data/renders")
_MANIFEST_PATH = _REPO_ROOT / "config" / "templates.json"
_DEFAULT_FORMAT = "banner_300x250"


async def fill_templates_per_format(state: GraphState) -> dict:
    session_id = state.get("session_id") or "nosession"

    brief_raw = state.get("brief")
    image_raw = state.get("image")
    winner_raw = state.get("winner")
    if image_raw is None:
        raise ValueError("fill_templates_per_format: state.image is None")
    if winner_raw is None:
        raise ValueError("fill_templates_per_format: state.winner is None")

    brief = _coerce(brief_raw, AdBrief, "brief") if brief_raw else None
    image = _coerce(image_raw, GeneratedImage, "image")
    winner = _coerce(winner_raw, MessageCandidate, "winner")

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

    out: list[dict] = []
    start = datetime.utcnow()

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
        fmt_start = datetime.utcnow()
        try:
            canvas = compose(
                spec,
                hero=hero_bytes,
                texts=texts,
                assets_root=_REPO_ROOT,
            )
            path = _RENDER_DIR / f"{session_id}_{fmt}_{ts}.png"
            canvas.save(path, format="PNG", optimize=True)
            out.append({"format": fmt, "path": str(path)})
            log.info(
                "compose_format_ok",
                session_id=session_id,
                slug=fmt,
                latency_ms=int(
                    (datetime.utcnow() - fmt_start).total_seconds() * 1000
                ),
            )
        except Exception as exc:  # noqa: BLE001 — single-format isolation
            log.error(
                "compose_format_error",
                session_id=session_id,
                slug=fmt,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    log.info(
        "compose_node_done",
        session_id=session_id,
        n_total=len(out),
        n_requested=len(formats),
        node_latency_ms=int((datetime.utcnow() - start).total_seconds() * 1000),
    )
    return {"rendered_files": out}


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
