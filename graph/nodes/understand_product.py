"""understand_product node — the pipeline learns WHAT is being sold.

Replaces ``parse_brief`` (2026-07-28). That node was a no-op round trip: the
web layer glued the wizard fields into one string, an LLM split the string back
into the same fields — copying audience and emotion out verbatim by explicit
instruction — and along the way compressed ``product`` to "1–6 слов" and
guessed a ``goal``/``channel`` nobody downstream acted on. Nothing was
*studied*.

This node actually studies the product. It gathers three grounding sources
before the first word of copy is written:

  - the vendored knowledge base (``config/knowledge/``) — matched by product
    name, plus a one-line glossary of the neighbouring Evolution products the
    documents cross-reference;
  - the page behind ``brief.source_url``, fetched through the SSRF guard, so a
    marketer can point at a landing page or article instead of retyping it;
  - ``brief.notes`` — the free-form field, declared the highest-priority source
    in the prompt, since it is where the marketer puts the thing that must not
    be missed.

Every source is optional. No URL, no KB match and no notes degrades to "what
the marketer typed", which is exactly what the pipeline saw before.

Input:  GraphState.brief (AdBrief — product/audience_raw/emotion/notes/source_url)
Output: GraphState.brief (with source_text filled in) + GraphState.product
        (ProductBrief) — the factual card every downstream text node reads.
"""

from __future__ import annotations

import structlog

from graph import knowledge
from graph.agent_runner import run_agent
from graph.prompts import extract_section, load_skill, render
from graph.state import AdBrief, GraphState, ProductBrief
from infra.urlfetch import UrlFetchError, fetch_page_text

log = structlog.get_logger(__name__)

_AGENT_ID = "understand_product"
_SKILL_NAME = "understand_product"

_NONE = "(не указано)"


def _resolve_kb(brief: AdBrief, *, session_id: str | None) -> object:
    """Выбор продукта: явный slug из брифа > alias-автодетект; 'none' — без KB."""
    if brief.product_slug == "none":
        return None
    if brief.product_slug and brief.product_slug != "auto":
        doc = knowledge.get_by_slug(brief.product_slug)
        if doc is not None:
            return doc
        log.warning(
            "kb_slug_unknown", session_id=session_id, slug=brief.product_slug
        )
    return knowledge.find_product(brief.product, brief.notes)


async def understand_product(state: GraphState) -> dict:
    brief = _coerce_brief(state.get("brief"))
    session_id = state.get("session_id")

    source_text = await _read_source(brief.source_url, session_id=session_id)
    doc = _resolve_kb(brief, session_id=session_id)

    skill = load_skill(_SKILL_NAME)
    system_msg = extract_section(skill.body, "## System message")
    user_tpl = extract_section(skill.body, "## User message template")

    user_msg = render(
        user_tpl,
        product=brief.product,
        audience_raw=brief.audience_raw,
        emotion=brief.emotion or _NONE,
        notes_block=brief.notes.strip() or "(маркетолог ничего не добавил)",
        kb_block=(
            f"{doc.product_facts}\n\n{doc.audiences}"
            if doc
            else "(в базе знаний нет карточки по этому продукту)"
        ),
        glossary_block=knowledge.glossary(exclude=doc.slug if doc else None) or _NONE,
        source_block=source_text or "(ссылка не указана или страница не прочитана)",
    )

    product = await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=ProductBrief,
        session_id=session_id,
    )

    log.info(
        "understand_product_ok",
        session_id=session_id,
        canonical_name=product.canonical_name,
        kb_match=doc.slug if doc else None,
        source_chars=len(source_text),
        n_capabilities=len(product.key_capabilities),
        n_must_honour=len(product.must_honour),
    )
    return {
        "brief": brief.model_copy(update={"source_text": source_text}).model_dump(),
        "product": product.model_dump(),
        "kb_match": (
            {"slug": doc.slug, "name": doc.name, "version": doc.version}
            if doc
            else None
        ),
    }


async def _read_source(url: str, *, session_id: str | None) -> str:
    """Fetch the marketer's link. A bad link degrades the run, never fails it."""
    if not url.strip():
        return ""
    try:
        return await fetch_page_text(url.strip())
    except UrlFetchError as exc:
        log.warning("source_url_unreadable", session_id=session_id, url=url, error=str(exc))
        return ""


def _coerce_brief(obj: object) -> AdBrief:
    if obj is None:
        raise ValueError("understand_product: state.brief is missing")
    if isinstance(obj, AdBrief):
        return obj
    return AdBrief.model_validate(obj)
