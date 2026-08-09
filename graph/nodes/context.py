"""Shared prompt blocks — the same grounding text for every node that writes.

The free-form ``notes`` field and the ``ProductBrief`` are injected into the
persona, the generator, the critic and the image-prompt writer. Formatting them
in one place keeps the wording identical across prompts (a node that phrases
"нет данных" differently teaches the model that the section is optional) and
keeps the "no product card yet" fallback in a single spot.
"""

from __future__ import annotations

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


def experience_block(state: GraphState, *, kind: str = "text", limit: int = 5) -> str:
    """Слой «опыт»: что по этому продукту уже ушло в работу.

    kind="text" — русские слоганы с якорями (копирайтер);
    kind="metaphor" — английские метафоры (промпт картинки — он на английском).
    Пусто, когда отметок нет: секция тогда не подмешивается вовсе и промпт
    остаётся ровно таким, каким был до слоя опыта (решение спеки). Именно
    поэтому здесь нет fallback-строки, в отличие от notes_block — там пустая
    секция ломала бы структуру user-сообщения, а тут секции просто не будет."""
    slug = (state.get("kb_match") or {}).get("slug")
    notes = knowledge.experience_for(slug, limit=limit)
    if kind == "metaphor":
        return "\n".join(f"- {n.metaphor}" for n in notes if n.metaphor)
    return "\n".join(
        f"- «{n.slogan}» — {n.anchor or 'якорь не записан'}"
        + (f"; комментарий: {n.comment}" if n.comment else "")
        for n in notes
        if n.slogan
    )
