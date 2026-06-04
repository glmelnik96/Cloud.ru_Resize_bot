"""generate_image node — M3.1: Phygital+ brand text→image adapter.

Input:
  - GraphState.session_id
  - GraphState.image_style ∈ {photo, render, isometric}
  - GraphState.brief (AdBrief) — product + audience define the visual theme
  - GraphState.image_revise_round (int)
  - GraphState.image_refine_comment (str | None) — owner-typed refine note,
    appended to the prompt on regenerate

Output:
  - GraphState.image (GeneratedImage.model_dump()) — local_path filled with the
    downloaded PNG under /data/images/, url stays None for M3.1 (the layout
    stage in M3.2 — Figma MCP — will resolve the public URL itself).

Behavior:
  - If PhygitalClient is initialized (PHYGITAL_ENABLED=true + session.json
    bootstrapped + login still good), call run_brand_text2img with variant =
    image_style. Result is a download URL from Phygital S3 → stream to disk.
  - If anything in the Phygital path is unavailable (disabled, no session,
    transient failure, safety scrubber gave up), fall back to the M3.0 PIL
    placeholder so the graph keeps progressing — the owner sees a stub-marked
    image and can re-run with a tightened brief.

Prompt contract (see memory/feedback_hero_image_no_text.md):
  Picture is a self-contained hero visual; text overlay (slogan/body/CTA) is
  applied later by the layout stage (fill_templates_per_format / Figma MCP).
  So the prompt we feed to the Gemini brand-enhancer carries ONLY the theme
  (product + audience + tone hints + optional refine comment) plus an explicit
  "no text, no logos" guard. winner.slogan/body/cta are NOT included.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path

import httpx
import structlog
from PIL import Image, ImageDraw, ImageFont

from graph.state import AdBrief, GeneratedImage, GraphState
from infra.phygital_client import get_client, is_enabled

log = structlog.get_logger(__name__)

_IMAGES_DIR = Path("/data/images")

# Allowed variants accepted by Phygital brand-enhancer (mirrors VARIANT_DOCS).
_VALID_VARIANTS = {"photo", "render", "isometric"}

# Stub palette — kept as fallback when Phygital is off / errors.
_STYLE_PALETTE = {
    "photo": ((40, 60, 90), (220, 200, 170), "PHOTO"),
    "render": ((20, 30, 50), (90, 200, 230), "RENDER"),
    "isometric": ((250, 245, 235), (40, 80, 200), "ISOMETRIC"),
}
_ROUND_TINT = [(0, 0, 0), (30, 0, 0), (0, 30, 0), (0, 0, 30)]


async def generate_image(state: GraphState) -> dict:
    session_id = state.get("session_id") or "nosession"
    style = state.get("image_style") or "photo"
    if style not in _VALID_VARIANTS:
        log.warning("generate_image_unknown_style_fallback", got=style)
        style = "photo"
    revise_round = state.get("image_revise_round", 0)
    refine_comment = state.get("image_refine_comment")

    brief_raw = state.get("brief")
    brief = _coerce(brief_raw, AdBrief, "brief") if brief_raw else None

    _IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    prompt = _build_brand_prompt(
        brief=brief, refine_comment=refine_comment, revise_round=revise_round
    )

    client = get_client()
    if client is None:
        reason = "PHYGITAL_ENABLED=false" if not is_enabled() else "client_not_initialized"
        log.warning("generate_image_falling_back_to_stub", reason=reason)
        return _stub_fallback(
            session_id=session_id,
            style=style,
            revise_round=revise_round,
            brief=brief,
            prompt=prompt,
            note=f"stub:{reason}",
        )

    try:
        gen = await _run_phygital(
            client=client,
            session_id=session_id,
            style=style,
            revise_round=revise_round,
            prompt=prompt,
        )
    except Exception as exc:
        err_type = type(exc).__name__
        log.warning(
            "generate_image_phygital_failed_falling_back",
            error=str(exc),
            error_type=err_type,
        )
        # PhygitalAuthError == both tokens мёртвые → нужен re-recon на хосте.
        # Без алерта владелец узнаёт только по stub-картинке в TG. Дедуп — час
        # на ключ, чтобы клики «перегенерить» не спамили admin'а. См.
        # memory/reference_phygital_session_refresh.md
        if err_type == "PhygitalAuthError":
            from infra.admin_alert import notify_admin

            await notify_admin(
                "Phygital session мёртвая — генерация скатилась на stub-картинку.\n"
                "Нужен re-recon: открыть Phygital-bot, перелогиниться, затем "
                "docker cp storage/session.json -> resize-bot:/data/phygital_storage/, "
                "потом docker restart resize-bot.\n"
                f"(session={session_id}, err={str(exc)[:160]})",
                dedupe_key="phygital_auth_dead",
                cooldown_s=3600,
            )
        return _stub_fallback(
            session_id=session_id,
            style=style,
            revise_round=revise_round,
            brief=brief,
            prompt=prompt,
            note=f"stub:phygital_error:{err_type}",
        )

    log.info(
        "generate_image_phygital_ok",
        session_id=session_id,
        style=style,
        revise_round=revise_round,
        local_path=gen.local_path,
    )
    return {"image": gen.model_dump()}


# ── prompt builder ──────────────────────────────────────────────────────────


def _build_brand_prompt(
    *,
    brief: AdBrief | None,
    refine_comment: str | None,
    revise_round: int,
) -> str:
    """Build the user_text for Gemini brand-enhancer.

    NO slogan/CTA/body here — those belong to the layout overlay, not to the
    hero visual. See memory/feedback_hero_image_no_text.md.

    Revise semantics (P0.1, 2026-06-04):
      Phygital вызовы изолированы — Gemini Text не помнит, что было раньше
      ни между сессиями, ни между «перегенерить»-кликами. Чтобы клик «перегенерить»
      реально менял результат, мы сами должны кодировать в prompt сигнал
      «дай другую композицию» — иначе платим за второй идентичный input и
      полагаемся только на стохастику Gemini.

      - refine_comment (owner набрал текст «сделай темнее / другой ракурс») —
        это самый сильный сигнал; ставим вверху отдельным блоком, чтобы Gemini
        точно учёл, а не размазал по остальным строкам.
      - revise_round > 0 без refine_comment («просто перегенерить») — добавляем
        инструкцию «дай альтернативную композицию, отличную по углу/палитре/
        фокусу от предыдущей попытки». Номер раунда тоже шлём — это лёгкий seed
        для разнообразия (Gemini видит «попытка №2» и сдвигает выбор).
    """
    lines: list[str] = []

    # Сильный сигнал сверху — иначе Gemini может его размыть.
    if refine_comment:
        lines.append(
            "ПРИОРИТЕТНЫЕ ПРАВКИ от владельца (применить в первую очередь): "
            f"{refine_comment}"
        )

    if brief is not None:
        lines.append(f"Тема рекламы: {brief.product}")
        lines.append(f"Целевая аудитория: {brief.audience_raw}")
        if brief.tone_hints:
            lines.append(f"Tone of voice: {brief.tone_hints}")
        if brief.constraints:
            lines.append("Ограничения: " + "; ".join(brief.constraints))
    else:
        lines.append("Тема рекламы: продукт Cloud.ru")
        lines.append("Целевая аудитория: B2B-маркетологи")

    # «Просто перегенерить» (revise_round > 0, refine_comment пуст) — Phygital
    # call stateless, поэтому без подсказки получим почти то же самое.
    if revise_round > 0 and not refine_comment:
        lines.append(
            f"Это попытка №{revise_round + 1} перегенерации. Предложи альтернативную "
            "композицию, отличную от предыдущих по как минимум двум осям: ракурс/угол "
            "камеры, центральный объект или композиционный фокус, цветовая палитра в "
            "пределах брендовой системы. Избегай очевидно похожих сцен."
        )

    lines.append(
        "Требования к визуалу: самодостаточный hero-визуал под тему, без какого-либо "
        "текста, надписей, типографики, слоганов, CTA и логотипов. Текстовая часть "
        "макета накладывается на следующем этапе и не должна присутствовать на самой "
        "картинке."
    )
    return "\n".join(lines)


# ── Phygital call + download ────────────────────────────────────────────────


async def _run_phygital(
    *,
    client,
    session_id: str,
    style: str,
    revise_round: int,
    prompt: str,
) -> GeneratedImage:
    # Late import — vendor sys.path is patched only after phygital_client.start.
    from workflows.brand_text2img import run_brand_text2img  # type: ignore[import-not-found]

    job = await run_brand_text2img(
        client,
        prompt=prompt,
        variant=style,
        # Defaults below match Phygital-bot brand_text2img:
        #   model_name="v3_1" (Nano Banana), ratio/resolution = Phygital default.
    )
    if job.status != "completed" or not job.result_urls:
        raise RuntimeError(f"brand_text2img failed: status={job.status} err={job.error!r}")

    src_url = job.result_urls[0]

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    hash_tail = hashlib.sha1(
        f"{session_id}|{style}|{revise_round}|{src_url}".encode()
    ).hexdigest()[:8]
    out_path = _IMAGES_DIR / f"{session_id}_{style}_r{revise_round}_{hash_tail}_{ts}.png"

    await _download(src_url, out_path)

    return GeneratedImage(
        url=None,  # M3.2 (Figma MCP) fills the public URL itself
        local_path=str(out_path),
        style=style,
        variant=style,
        prompt=prompt,
    )


async def _download(url: str, dest: Path) -> None:
    """Stream-download the S3 URL to disk. Phygital download-links are pre-signed
    so no auth headers needed."""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as c:
        async with c.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)


# ── Stub fallback (preserved from M3.0) ─────────────────────────────────────


def _stub_fallback(
    *,
    session_id: str,
    style: str,
    revise_round: int,
    brief: AdBrief | None,
    prompt: str,
    note: str,
) -> dict:
    bg, accent, label = _STYLE_PALETTE.get(style, _STYLE_PALETTE["photo"])
    tint = _ROUND_TINT[revise_round % len(_ROUND_TINT)]
    bg = tuple(min(255, max(0, c + t)) for c, t in zip(bg, tint))

    width, height = 1080, 1350
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(60, height - 360), (width - 60, height - 60)], fill=accent)

    product_text = brief.product if brief else "PRODUCT"
    hash_tail = hashlib.sha1(
        f"{session_id}|{style}|{revise_round}|{product_text}".encode()
    ).hexdigest()[:8]

    try:
        font_lg = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
        font_md = ImageFont.truetype("DejaVuSans.ttf", 40)
        font_sm = ImageFont.truetype("DejaVuSans.ttf", 28)
    except Exception:
        font_lg = ImageFont.load_default()
        font_md = ImageFont.load_default()
        font_sm = ImageFont.load_default()

    draw.text((80, 100), f"[STUB: {label}]", fill=accent, font=font_md)
    draw.text((80, 160), f"round={revise_round}  hash={hash_tail}", fill=accent, font=font_sm)
    draw.text((80, 210), note[:60], fill=accent, font=font_sm)
    draw.text((80, height - 320), product_text[:40], fill=(20, 20, 30), font=font_lg)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    filename = f"{session_id}_{style}_r{revise_round}_{hash_tail}_{ts}.png"
    out_path = _IMAGES_DIR / filename
    img.save(out_path, format="PNG", optimize=True)

    gen = GeneratedImage(
        url=None,
        local_path=str(out_path),
        style=style,
        variant="stub",
        prompt=prompt,
    )

    log.info(
        "generate_image_stub_ok",
        session_id=session_id,
        style=style,
        revise_round=revise_round,
        local_path=str(out_path),
        note=note,
    )
    return {"image": gen.model_dump()}


def _coerce(obj: object, model: type, label: str):
    if obj is None:
        raise ValueError(f"{label} is None")
    if isinstance(obj, model):
        return obj
    return model.model_validate(obj)
