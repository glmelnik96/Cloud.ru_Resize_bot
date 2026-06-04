"""fill_templates_per_format node — M3.2 Figma MCP integration.

For each slug in brief.formats:
  1. If MCP client available + manifest entry exists → upload hero, set texts,
     export PNG via Figma MCP.
  2. Otherwise (no client / no manifest entry / MCP error) → PIL composite
     fallback. Per-format try/except so one bad format doesn't kill the rest.

The PIL path is the same composite that shipped in M3.0 — kept intentionally
so the bot never returns zero renders, even if Figma is fully down.

Contract unchanged from M3.0:
  state.brief.formats × state.image (hero) × state.winner (text) →
  state.rendered_files = [{format, path}, ...]
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import structlog
from PIL import Image, ImageDraw, ImageFont

from graph.state import AdBrief, GeneratedImage, GraphState, MessageCandidate
from infra import figma_mcp
from infra.figma_manifest import FigmaManifest, load_manifest

log = structlog.get_logger(__name__)

_RENDER_DIR = Path("/data/renders")
_MANIFEST_PATH = Path(os.environ.get("FIGMA_MANIFEST_PATH", "config/figma_templates.json"))
_DEFAULT_FORMAT = "vk_post_1080x1080"
_NODE_TIMEOUT_S = 180.0


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

    manifest = _load_manifest_safe()
    client = figma_mcp.get_client()
    hero_bytes = Path(image.local_path).read_bytes()
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")

    out: list[dict] = []
    n_real = 0
    n_fallback = 0
    start = datetime.utcnow()

    # asyncio.wait_for (not asyncio.timeout) so this works on Python 3.10 dev
    # envs as well as the 3.11 Docker runtime. Inner coroutine + nonlocal so
    # the counters and `out` list survive a cancellation.
    async def _render_all() -> None:
        nonlocal n_real, n_fallback
        for fmt in formats:
            path, used_real = await _render_one(
                fmt=fmt,
                session_id=session_id,
                ts=ts,
                hero_bytes=hero_bytes,
                hero_local_path=image.local_path,
                winner=winner,
                manifest=manifest,
                client=client,
            )
            out.append({"format": fmt, "path": str(path)})
            if used_real:
                n_real += 1
            else:
                n_fallback += 1

    try:
        await asyncio.wait_for(_render_all(), timeout=_NODE_TIMEOUT_S)
    except asyncio.TimeoutError:
        # Hard timeout — fill in whatever wasn't rendered with PIL stubs.
        rendered_fmts = {r["format"] for r in out}
        for fmt in formats:
            if fmt in rendered_fmts:
                continue
            path = _pil_fallback(
                fmt=fmt,
                session_id=session_id,
                ts=ts,
                hero_local_path=image.local_path,
                winner=winner,
                manifest=manifest,
                reason="node_timeout",
            )
            out.append({"format": fmt, "path": str(path)})
            n_fallback += 1
        log.warning("figma_node_timeout", session_id=session_id, n_real=n_real, n_fallback=n_fallback)

    elapsed_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
    log.info(
        "figma_node_done",
        session_id=session_id,
        n_total=len(out),
        n_real=n_real,
        n_fallback=n_fallback,
        node_latency_ms=elapsed_ms,
    )
    return {"rendered_files": out}


def _load_manifest_safe() -> FigmaManifest | None:
    """Read the manifest; on any failure return None and log loudly.
    Caller then full-fallbacks every format to PIL."""
    try:
        return load_manifest(_MANIFEST_PATH)
    except FileNotFoundError:
        log.warning("figma_manifest_missing", path=str(_MANIFEST_PATH))
        return None
    except Exception as exc:  # noqa: BLE001 — broken JSON, bad schema, etc.
        log.warning(
            "figma_manifest_broken",
            path=str(_MANIFEST_PATH),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


async def _render_one(
    *,
    fmt: str,
    session_id: str,
    ts: str,
    hero_bytes: bytes,
    hero_local_path: str,
    winner: MessageCandidate,
    manifest: FigmaManifest | None,
    client,
) -> tuple[Path, bool]:
    """Render one format; return (path, used_real_figma)."""
    # PIL fallback path: no client, no manifest, or no template entry.
    if client is None or manifest is None:
        reason = "no_client" if client is None else "no_manifest"
        return (
            _pil_fallback(
                fmt=fmt, session_id=session_id, ts=ts,
                hero_local_path=hero_local_path, winner=winner,
                manifest=manifest, reason=reason,
            ),
            False,
        )
    tmpl = manifest.templates.get(fmt)
    if tmpl is None:
        log.info("figma_format_fallback", slug=fmt, reason="manifest_miss")
        return (
            _pil_fallback(
                fmt=fmt, session_id=session_id, ts=ts,
                hero_local_path=hero_local_path, winner=winner,
                manifest=manifest, reason="manifest_miss",
            ),
            False,
        )

    # Real Figma path with per-format try/except.
    fmt_start = datetime.utcnow()
    try:
        await client.upload_hero(
            file_key=manifest.file_key,
            node_id=tmpl.slots.hero_image_id,
            png_bytes=hero_bytes,
        )
        replacements: list[tuple[str, str]] = [
            (tmpl.slots.slogan_text_id, winner.slogan),
        ]
        if tmpl.slots.cta_text_id and winner.cta:
            replacements.append((tmpl.slots.cta_text_id, winner.cta))
        await client.set_texts(file_key=manifest.file_key, replacements=replacements)
        png_bytes = await client.export_frame(
            file_key=manifest.file_key,
            node_id=tmpl.frame_id,
            max_dim=max(tmpl.width, tmpl.height),
        )
        path = _RENDER_DIR / f"{session_id}_{fmt}_{ts}.png"
        path.write_bytes(png_bytes)
        latency_ms = int((datetime.utcnow() - fmt_start).total_seconds() * 1000)
        log.info("figma_format_ok", slug=fmt, latency_ms=latency_ms)
        return path, True
    except Exception as exc:  # noqa: BLE001 — fall back per format
        err_type = type(exc).__name__
        log.warning(
            "figma_format_fallback",
            slug=fmt,
            reason="mcp_error",
            error=str(exc),
            error_type=err_type,
        )
        return (
            _pil_fallback(
                fmt=fmt, session_id=session_id, ts=ts,
                hero_local_path=hero_local_path, winner=winner,
                manifest=manifest, reason=f"mcp_error:{err_type}",
            ),
            False,
        )


def _pil_fallback(
    *,
    fmt: str,
    session_id: str,
    ts: str,
    hero_local_path: str,
    winner: MessageCandidate,
    manifest: FigmaManifest | None,
    reason: str,
) -> Path:
    """Reproduce the M3.0 PIL composite for one slug. Size taken from manifest
    if present, otherwise from a default."""
    w, h = _size_for(fmt, manifest)
    hero = Image.open(hero_local_path).convert("RGB")
    canvas = _pil_compose(hero, w, h, winner)
    path = _RENDER_DIR / f"{session_id}_{fmt}_{ts}.png"
    canvas.save(path, format="PNG", optimize=True)
    log.info("figma_format_fallback_saved", slug=fmt, reason=reason, path=str(path))
    return path


def _size_for(fmt: str, manifest: FigmaManifest | None) -> tuple[int, int]:
    if manifest is not None:
        t = manifest.templates.get(fmt)
        if t is not None:
            return t.width, t.height
    return 1080, 1350


def _pil_compose(hero: Image.Image, w: int, h: int, winner: MessageCandidate) -> Image.Image:
    canvas = Image.new("RGB", (w, h), (245, 245, 245))
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
