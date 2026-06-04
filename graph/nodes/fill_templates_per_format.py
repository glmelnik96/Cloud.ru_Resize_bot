"""fill_templates_per_format node — STUB for M3.0 (Figma adapter in M3.1+).

Input:
  - GraphState.brief.formats (list[str], slugs like "tg_post_1080x1350")
  - GraphState.image (GeneratedImage.model_dump()) — approved hero
  - GraphState.winner (MessageCandidate) — text overlay source
Output:
  - GraphState.rendered_files: [{"format": slug, "path": local_png}, ...]

Why stub: real Figma MCP integration (mapping {{slogan}}/{{body}}/{{cta}} into
master frames and exporting per-format PNGs) is M3.2. M3.0 proves the topology
by compositing hero + text overlay locally so the ZIP step has real files.

Default formats if brief.formats is empty: ["tg_post_1080x1350"] — matches
the wizard's implicit default channel = tg_post.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import structlog
from PIL import Image, ImageDraw, ImageFont

from graph.state import AdBrief, GeneratedImage, GraphState, MessageCandidate

log = structlog.get_logger(__name__)

_RENDER_DIR = Path("/data/renders")
_DEFAULT_FORMAT = "tg_post_1080x1350"
_FORMAT_RE = re.compile(r"^(?P<channel>[a-z_]+)_(?P<w>\d+)x(?P<h>\d+)$")


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
    _RENDER_DIR.mkdir(parents=True, exist_ok=True)

    hero = Image.open(image.local_path).convert("RGB")
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")

    out: list[dict] = []
    for fmt in formats:
        w, h = _parse_size(fmt)
        canvas = _compose(hero, w, h, winner)
        path = _RENDER_DIR / f"{session_id}_{fmt}_{ts}.png"
        canvas.save(path, format="PNG", optimize=True)
        out.append({"format": fmt, "path": str(path)})

    log.info(
        "fill_templates_stub_ok",
        session_id=session_id,
        formats=[r["format"] for r in out],
        n=len(out),
    )
    return {"rendered_files": out}


def _parse_size(slug: str) -> tuple[int, int]:
    m = _FORMAT_RE.match(slug)
    if m:
        return int(m["w"]), int(m["h"])
    log.warning("fill_templates_unparseable_format", slug=slug, fallback="1080x1350")
    return 1080, 1350


def _compose(hero: Image.Image, w: int, h: int, winner: MessageCandidate) -> Image.Image:
    canvas = Image.new("RGB", (w, h), (245, 245, 245))
    # cover-fit hero into top 65% of canvas
    target_h = int(h * 0.65)
    aspect = hero.width / hero.height
    target_w = int(target_h * aspect)
    if target_w < w:
        target_w = w
        target_h = int(w / aspect)
    hero_resized = hero.resize((target_w, target_h), Image.LANCZOS)
    ox = (w - target_w) // 2
    canvas.paste(hero_resized, (ox, 0))

    draw = ImageDraw.Draw(canvas)
    try:
        font_lg = ImageFont.truetype("DejaVuSans-Bold.ttf", max(28, w // 28))
        font_md = ImageFont.truetype("DejaVuSans.ttf", max(20, w // 40))
        font_sm = ImageFont.truetype("DejaVuSans-Bold.ttf", max(18, w // 50))
    except Exception:
        font_lg = ImageFont.load_default()
        font_md = ImageFont.load_default()
        font_sm = ImageFont.load_default()

    text_top = int(h * 0.68)
    draw.text((int(w * 0.05), text_top), winner.slogan[:60], fill=(20, 20, 30), font=font_lg)
    draw.text(
        (int(w * 0.05), text_top + int(h * 0.08)),
        winner.body[:120],
        fill=(50, 50, 60),
        font=font_md,
    )
    draw.text(
        (int(w * 0.05), h - int(h * 0.08)),
        winner.cta[:30].upper(),
        fill=(0, 80, 200),
        font=font_sm,
    )
    return canvas


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
