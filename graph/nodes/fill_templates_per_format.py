"""fill_templates_per_format node — 12-banner PIL composer (2026-06-21).

For each ranked proposition i:
  - take its generated hero (state.generated_heroes[i]) and its scenario
    (render|photo, read from the hero entry's ``style``),
  - look up the matching 300x600 template (banner_300x600_<scenario>),
  - compose hero + slogan + subtitle + cta, где subtitle — расшифровка термина
    из карточки продукта (см. `_subtitle_for`), а НЕ черновик копирайтера,
  - save a PNG and append {format=<NN_scenario>, path} to rendered_files.

Each banner gets a UNIQUE ``format`` label (index + scenario) so render_all's
per-entry ZIP arcname never collides — the 12 banners would otherwise share
only two template slugs.

Manual fallback: if no heroes were generated but the user uploaded a single
hero (state.image), compose ONE banner with the top-ranked proposition on its
scenario template. Raises if there is neither a generated hero nor an upload.

Per-banner try/except: one bad compose never kills the rest.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
from datetime import datetime
from pathlib import Path

import structlog
from PIL import Image

from graph.nodes import ranked_candidates
from graph.nodes.context import get_product
from graph.state import GeneratedImage, GraphState, MessageCandidate, ProductBrief
from infra.composer import compose
from infra.template_manifest import load_manifest

log = structlog.get_logger(__name__)

# Термин отделён от расшифровки тире или двоеточием: промпт просит «—», модели
# пишут кто во что горазд. Разделитель ищем с пробелом вокруг, иначе дефис
# внутри самого термина («S3-совместимое») разрежет его пополам.
_TERM_SEP_RE = re.compile(r"\s+[—–:-]\s+")

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Default to the Docker bot's /data/renders; App3 overrides via RENDERS_DIR.
_RENDER_DIR = Path(os.environ.get("RENDERS_DIR", "/data/renders"))
_MANIFEST_PATH = _REPO_ROOT / "config" / "templates.json"
_VALID_SCENARIOS = {"render", "photo"}
_DEFAULT_SCENARIO = "photo"


def _scenario_for(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    return s if s in _VALID_SCENARIOS else _DEFAULT_SCENARIO


def _subtitle_for(product: ProductBrief | None, slogan: str) -> str:
    """Строка под заголовком — расшифровка термина из карточки продукта.

    В макете (Figma 3460:1404) это «GenAI — генеративный искусственный
    интеллект»: пояснение к слову из заголовка, а не вторая рекламная фраза.
    Ровно этот формат собирает `understand_product` в `vocabulary`.

    Берём тот термин, который человек уже прочитал в заголовке — иначе
    подзаголовок объясняет не то, обо что читатель споткнулся. Не нашли — первый
    термин словаря: он собран под этот же продукт и всё равно по делу. Нет
    словаря или карточки — пустая строка: выдумывать расшифровку нельзя, а
    пустой слот композер просто не рисует.
    """
    if product is None:
        return ""
    terms = [t.strip() for t in product.vocabulary if t and t.strip()]
    if not terms:
        return ""
    low = slogan.lower()
    for term in terms:
        head = _TERM_SEP_RE.split(term, maxsplit=1)[0].strip()
        if head and head.lower() in low:
            return term
    return terms[0]


def _hero_ext(data: bytes) -> str:
    """File extension for the native hero artifact, sniffed from the bytes so
    the on-disk name matches the actual format App1 sent (PNG in practice; a
    manually uploaded JPEG stays .jpg). Falls back to png on any decode error."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "PNG").lower()
    except Exception:  # noqa: BLE001
        return "png"
    return "jpg" if fmt == "jpeg" else fmt


def _slug(scenario: str) -> str:
    return f"banner_300x600_{scenario}"


async def fill_templates_per_format(state: GraphState) -> dict:
    session_id = state.get("session_id") or "nosession"

    candidates = ranked_candidates(state)
    heroes = state.get("generated_heroes") or []
    image_raw = state.get("image")
    product = get_product(state)

    _RENDER_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(_MANIFEST_PATH)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    start = datetime.utcnow()

    # Build the (index, candidate, hero_bytes, scenario) jobs.
    jobs: list[tuple[int, MessageCandidate, bytes, str]] = []
    if heroes:
        for pos, hero in enumerate(heroes):
            # Each hero may carry the ranked index it was generated for, so a
            # failed/skipped hero never shifts the candidate alignment.
            idx = hero.get("index", pos)
            if idx >= len(candidates):
                continue
            scenario = _scenario_for(hero.get("style"))
            hero_bytes = Path(hero["local_path"]).read_bytes()
            jobs.append((idx, candidates[idx], hero_bytes, scenario))
    elif image_raw is not None:
        # Manual single-upload fallback: one banner, top proposition.
        image = _coerce(image_raw, GeneratedImage, "image")
        scenario = _scenario_for(image.style)
        hero_bytes = Path(image.local_path).read_bytes()
        jobs.append((0, candidates[0], hero_bytes, scenario))
    else:
        raise ValueError(
            "fill_templates_per_format: no heroes and no uploaded image"
        )

    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                _render_one_sync,
                manifest.templates[_slug(scenario)],
                _slug(scenario),
                f"{idx + 1:02d}_{scenario}",
                hero_bytes,
                {
                    "slogan": cand.slogan,
                    "subtitle": _subtitle_for(product, cand.slogan),
                    "cta": cand.cta,
                },
                session_id,
                ts,
            )
            for (idx, cand, hero_bytes, scenario) in jobs
        )
    )
    out: list[dict] = [r for r in results if r is not None]

    log.info(
        "compose_node_done",
        session_id=session_id,
        n_total=len(out),
        n_requested=len(jobs),
        node_latency_ms=int((datetime.utcnow() - start).total_seconds() * 1000),
    )
    return {"rendered_files": out}


def _render_one_sync(
    spec,
    slug: str,
    label: str,
    hero_bytes: bytes,
    texts: dict[str, str],
    session_id: str,
    ts: str,
) -> dict | None:
    """Compose + save one banner. Runs in a worker thread.

    Returns {format, path, layers} on success, None on failure (per-banner
    isolation: one bad compose never kills the rest). ``label`` is the
    unique per-banner id used for the filename and the ZIP arcname.

    ``layers`` (Block 1, 2026-07-10) maps message/hero/brand to un-composited
    artifacts of the same banner:
      - message / brand — transparent-PNG alpha layers at TEMPLATE scale (the
        text plates alone; the brand strips as a third layer),
      - hero — the NATIVE hero exactly as App1 delivered it (full resolution
        and original format), written verbatim, NOT the downscaled composited
        frame. Designers recomposite from the source asset, so a 300px slot
        thumbnail is useless (2026-07-15, Глеб).
    Best-effort: a failed artifact is skipped, the composite still ships.
    """
    fmt_start = datetime.utcnow()
    try:
        canvas = compose(
            spec,
            hero=hero_bytes,
            texts=texts,
            assets_root=_REPO_ROOT,
            slug=slug,
        )
        path = _RENDER_DIR / f"{session_id}_{label}_{ts}.png"
        canvas.save(path, format="PNG", optimize=True)

        layers: dict[str, str] = {}
        # Template-scale alpha artifacts, drawn on a transparent canvas.
        for group in ("message", "brand"):
            try:
                art = compose(
                    spec,
                    hero=None,
                    texts=texts,
                    assets_root=_REPO_ROOT,
                    slug=slug,
                    only={group},
                )
                art_path = _RENDER_DIR / f"{session_id}_{label}_{ts}_{group}.png"
                art.save(art_path, format="PNG", optimize=True)
                layers[group] = str(art_path)
            except Exception as exc:  # noqa: BLE001 — artifact is optional
                log.warning(
                    "compose_layer_artifact_error",
                    session_id=session_id,
                    label=label,
                    group=group,
                    error=str(exc),
                )
        # Native hero asset, byte-for-byte as received — full resolution kept.
        try:
            ext = _hero_ext(hero_bytes)
            hero_art_path = _RENDER_DIR / f"{session_id}_{label}_{ts}_hero.{ext}"
            hero_art_path.write_bytes(hero_bytes)
            layers["hero"] = str(hero_art_path)
        except Exception as exc:  # noqa: BLE001 — artifact is optional
            log.warning(
                "hero_layer_artifact_error",
                session_id=session_id,
                label=label,
                error=str(exc),
            )

        log.info(
            "compose_format_ok",
            session_id=session_id,
            label=label,
            slug=slug,
            latency_ms=int(
                (datetime.utcnow() - fmt_start).total_seconds() * 1000
            ),
        )
        return {"format": label, "path": str(path), "layers": layers}
    except Exception as exc:  # noqa: BLE001 — single-banner isolation
        log.error(
            "compose_format_error",
            session_id=session_id,
            label=label,
            slug=slug,
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
