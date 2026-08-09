"""Shared prompt blocks — the same grounding text for every node that writes.

The free-form ``notes`` field and the ``ProductBrief`` are injected into the
persona, the generator, the critic and the image-prompt writer. Formatting them
in one place keeps the wording identical across prompts (a node that phrases
"нет данных" differently teaches the model that the section is optional) and
keeps the "no product card yet" fallback in a single spot.
"""

from __future__ import annotations

from typing import Literal

from graph import knowledge
from graph.state import AdBrief, GraphState, ProductBrief

NONE = "(не указано)"


def get_product(state: GraphState) -> ProductBrief | None:
    """The product card, if ``understand_product`` produced one."""
    raw = state.get("product")
    if raw is None:
        return None
    if isinstance(raw, ProductBrief):
        return raw
    return ProductBrief.model_validate(raw)


def product_block(product: ProductBrief | None) -> str:
    """Facts a copywriter may rely on — the boundary of what can be claimed."""
    if product is None:
        return "(карточка продукта не собрана — опирайся только на название)"
    parts = [f"{product.canonical_name} — {product.what_it_is}"]
    for title, items in (
        ("Что умеет", product.key_capabilities),
        ("Какие боли закрывает", product.problems_solved),
        ("Что можно утверждать (конкретика)", product.proof_points),
        ("Словарь терминов", product.vocabulary),
    ):
        if items:
            parts.append(title + ":\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(parts)


def must_honour_block(product: ProductBrief | None) -> str:
    """Hard requirements the marketer flagged in the free-form field."""
    if product is None or not product.must_honour:
        return "(жёстких требований нет)"
    return "\n".join(f"- {req}" for req in product.must_honour)


def notes_block(brief: AdBrief) -> str:
    """The free-form field verbatim — never summarized, that is the point."""
    return brief.notes.strip() or "(маркетолог ничего не добавил)"


# Сколько заметок просматриваем, чтобы набрать `limit` годных. Отбор по
# непустому полю идёт ПОСЛЕ выборки, и без запаса пять свежих пустышек
# похоронили бы весь опыт продукта: рецепт запуска собирается best-effort
# (_collect_recipe в app/services/creatives.py на любой ошибке чтения
# чекпоинта отдаёт {}), поэтому строки с пустым слоганом и метафорой в
# kb_runs — норма, а не патология. Потолок нужен, чтобы блок не начал
# перебирать весь снапшот, когда у продукта накопились сотни пустых отметок.
_SCAN_LIMIT = 50


def _one_line(s: str, cap: int = 200) -> str:
    """Схлопнуть в одну строку и обрезать по длине.

    Комментарий человека приезжает из <textarea> и сплошь и рядом написан
    буллетами («Взяли, но:», перевод строки, «- слишком длинно»). Вставленный
    дословно, он разваливает список блока: перенос читается моделью как ещё
    один отгруженный слоган, а секция велит от таких уходить — и модель начнёт
    обходить кусок чужого комментария. Обрезка держит объём: пять отметок по
    2000 символов (потолок OutcomeIn.comment) перевесили бы сам бриф.
    Слоган и якорь идут от LLM, но человек правит их в HITL — тот же вход."""
    return " ".join(s.split())[:cap]


def experience_block(
    state: GraphState,
    *,
    kind: Literal["text", "metaphor"] = "text",
    limit: int = 5,
) -> str:
    """Слой «опыт»: что по этому продукту уже ушло в работу.

    kind="text" — русские слоганы с якорями (копирайтер);
    kind="metaphor" — английские метафоры (промпт картинки — он на английском).
    Пусто, когда отметок нет: секция тогда не подмешивается вовсе и промпт
    остаётся ровно таким, каким был до слоя опыта (решение спеки). Именно
    поэтому здесь нет fallback-строки, в отличие от notes_block — там пустая
    секция ломала бы структуру user-сообщения, а тут секции просто не будет."""
    # Опечатка в kind тихо дала бы текстовую ветку — русские слоганы внутри
    # английского промпта картинки, и заметить это можно только глазами.
    if kind not in ("text", "metaphor"):
        raise ValueError(f"unknown experience block kind: {kind!r}")
    slug = (state.get("kb_match") or {}).get("slug")
    notes = knowledge.experience_for(slug, limit=_SCAN_LIMIT)
    if kind == "metaphor":
        picked = [n for n in notes if n.metaphor][:limit]
        return "\n".join(f"- {_one_line(n.metaphor)}" for n in picked)
    lines = []
    for n in [n for n in notes if n.slogan][:limit]:
        line = f"- «{_one_line(n.slogan)}» — {_one_line(n.anchor) or 'якорь не записан'}"
        comment = _one_line(n.comment)
        if comment:
            line += f"; комментарий: {comment}"
        lines.append(line)
    return "\n".join(lines)
