"""lint_candidates node — флажки честности на карточках перед HITL (блок 3).

Этап 1 «Язык» синтеза с Bannerzila: их линт-стадия в нашем формате. Узел
стоит между select_by_persona и hitl_text_approve, НИЧЕГО не отбраковывает
и не переписывает — только вешает lint_flags на карточки, которые HITL
показывает человеку. Решение остаётся за человеком (наш принцип «каждый
результат уникален» — линт информирует, не гейтит).

Два контура:

1. Код-фильтры (детерминированные, без LLM):
   - «штамп» — стоп-лист выхолощенных формулировок (референс — brandkit
     Bannerzila, tone_of_voice.stopwords);
   - «цифра без источника» — число в slogan/desired_outcome, которого нет
     ни в фактах о продукте (ProductBrief), ни в брифе маркетолога. Это
     lite-форма их правила «числа без Approved Claim блокируются» — у нас
     не блок, а флажок.

2. Инверсионный судья (LLM ДРУГОГО семейства — DeepSeek при генераторе
   GLM): читает ТОЛЬКО поверхность баннера (slogan + cta), от первого лица
   говорит, что понял, и сверяется с обещанным результатом. Fail-open:
   любая ошибка судьи логируется, флажков от судьи нет, конвейер живёт.

Input:
  - GraphState.ranked (12 отобранных карточек)
  - GraphState.product / brief (источники разрешённых чисел)
  - GraphState.personas (роль судьи)
Output:
  - GraphState.ranked — те же карточки в том же порядке + lint_flags.
"""

from __future__ import annotations

import re

import structlog

from graph.agent_runner import run_agent
from graph.prompts import extract_section, load_skill, render
from graph.state import GraphState, JudgeReadingSet, Persona

log = structlog.get_logger(__name__)

_AGENT_ID = "lint_inversion_judge"
_SKILL_NAME = "lint_inversion_judge"

# Стоп-лист штампов. Референс — Bannerzila brand/brandkit.json
# (tone_of_voice.stopwords); держим копией у себя: их репо — карьер,
# не runtime-зависимость.
_STOPWORDS = (
    "надёжный партнёр",
    "надежный партнер",
    "инновационный",
    "лучший в мире",
    "революционный",
    "уникальный",
    "лидер рынка",
)

# Числа: «10», «99.9», «99,9». Проценты ловятся тем же токеном.
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


async def lint_candidates(state: GraphState) -> dict:
    ranked = state.get("ranked") or []
    if not ranked:
        return {}
    session_id = state.get("session_id")

    allowed_numbers = _allowed_numbers(state)
    flags_by_id: dict[str, list[str]] = {
        c["id"]: _code_flags(c, allowed_numbers) for c in ranked
    }

    # --- инверсионный судья: советник, fail-open -----------------------------
    try:
        readings = await _run_judge(state, ranked, session_id=session_id)
        for r in readings.readings:
            if r.candidate_id in flags_by_id and not r.matches_offer:
                flags_by_id[r.candidate_id].append(
                    f"ЦА-судья прочитал иначе: {r.understood}"
                )
    except Exception as exc:  # fail-open: судья не должен убивать конвейер
        log.warning(
            "lint_judge_failed",
            session_id=session_id,
            error=str(exc),
        )

    linted = [{**c, "lint_flags": flags_by_id[c["id"]]} for c in ranked]
    n_flagged = sum(1 for c in linted if c["lint_flags"])
    log.info(
        "lint_candidates_ok",
        session_id=session_id,
        n=len(linted),
        n_flagged=n_flagged,
        flags={c["id"]: c["lint_flags"] for c in linted if c["lint_flags"]},
    )
    return {"ranked": linted}


def _code_flags(card: dict, allowed_numbers: set[str]) -> list[str]:
    flags: list[str] = []
    surface = " ".join(
        str(card.get(k) or "") for k in ("slogan", "body", "desired_outcome")
    )
    lowered = surface.lower()
    for stop in _STOPWORDS:
        if stop in lowered:
            flags.append(f"штамп: «{stop}»")

    for field in ("slogan", "desired_outcome"):
        for num in _NUM_RE.findall(str(card.get(field) or "")):
            if _norm_num(num) not in allowed_numbers:
                flags.append(f"цифра без источника: {num} (в {field})")
    return flags


def _allowed_numbers(state: GraphState) -> set[str]:
    """Числа, которые МОЖНО утверждать: всё, что встречается в фактах о
    продукте или в собственных словах маркетолога (бриф)."""
    product = state.get("product") or {}
    brief = state.get("brief") or {}
    if hasattr(brief, "model_dump"):
        brief = brief.model_dump()
    chunks: list[str] = []
    for value in list(product.values()) + list(brief.values()):
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, list):
            chunks.extend(str(v) for v in value)
    return {_norm_num(n) for text in chunks for n in _NUM_RE.findall(text)}


def _norm_num(token: str) -> str:
    return token.replace(",", ".")


async def _run_judge(
    state: GraphState, ranked: list[dict], *, session_id: str | None
) -> JudgeReadingSet:
    personas_raw = state.get("personas") or []
    if not personas_raw:
        raise ValueError("lint_candidates: state.personas is empty")
    persona = (
        personas_raw[0]
        if isinstance(personas_raw[0], Persona)
        else Persona.model_validate(personas_raw[0])
    )

    skill = load_skill(_SKILL_NAME)
    system_msg = extract_section(skill.body, "## System message")
    user_tpl = extract_section(skill.body, "## User message template")

    # Судья видит ТОЛЬКО то, что видит зритель: slogan + cta. body — внутренняя
    # заметка маркетолога и в ввод не попадает. Обещанный результат передаётся
    # отдельной строкой для сверки ПОСЛЕ прочтения.
    cards_block = "\n".join(
        f"- id={c['id']} | на баннере: «{c['slogan']}» / кнопка «{c['cta']}»"
        f" | маркетолог обещал: {c.get('desired_outcome') or '(не заявлено)'}"
        for c in ranked
    )

    user_msg = render(
        user_tpl,
        **{
            "persona.segment": persona.segment,
            "persona.age_range": persona.age_range,
            "persona.pain_points": ", ".join(persona.pain_points),
            "persona.motivations": ", ".join(persona.motivations),
            "persona.objections": ", ".join(persona.objections),
            "persona.communication_style": persona.communication_style,
            "cards_block": cards_block,
        },
    )
    return await run_agent(
        _AGENT_ID,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        schema=JudgeReadingSet,
        session_id=session_id,
    )
