"""Слой «опыт»: инжект в graph.knowledge и блок для промптов."""

from __future__ import annotations

from graph import knowledge
from graph.knowledge import ExperienceNote
from graph.nodes.context import experience_block


def _note(**kw) -> ExperienceNote:
    base = dict(
        slug="managed-rag", outcome="shipped", slogan="GPU без очереди",
        anchor="боль: очередь на GPU", desired_outcome="обучение стартует за минуты",
        metaphor="a bridge across a canyon", persona_segment="ML-инженеры",
        comment="",
    )
    base.update(kw)
    return ExperienceNote(**base)


def test_empty_layer_gives_empty_block():
    """Нет отметок — секции в промпте не будет вообще (решение спеки:
    промпт остаётся ровно таким, каким он был до слоя опыта)."""
    knowledge.set_experience(())
    assert experience_block({"kb_match": {"slug": "managed-rag"}}) == ""


def test_notes_are_filtered_by_slug():
    try:
        knowledge.set_experience((_note(), _note(slug="other", slogan="Чужое")))
        block = experience_block({"kb_match": {"slug": "managed-rag"}})
        assert "GPU без очереди" in block
        assert "Чужое" not in block
    finally:
        knowledge.set_experience(())


def test_rejected_is_stored_but_never_reaches_prompts():
    """rejected копится в kb_runs (человеку он виден на странице библиотеки),
    но в промпт идёт только принятое: забракованный текст как «пример» —
    это подсказка повторить неудачу."""
    try:
        knowledge.set_experience((
            _note(outcome="rejected", slogan="Слишком общо", comment="ни о чём"),
        ))
        assert experience_block({"kb_match": {"slug": "managed-rag"}}) == ""
    finally:
        knowledge.set_experience(())


def test_block_keeps_five_freshest():
    try:
        knowledge.set_experience(
            tuple(_note(slogan=f"Слоган {i}") for i in range(8))
        )
        block = experience_block({"kb_match": {"slug": "managed-rag"}})
        assert block.count("\n") == 4  # пять строк
        assert "Слоган 0" in block and "Слоган 5" not in block
    finally:
        knowledge.set_experience(())


def test_no_kb_match_means_no_experience():
    try:
        knowledge.set_experience((_note(),))
        # Опыт по другому продукту хуже, чем никакого: он уводит копирайтера
        # в чужие формулировки. Нет привязки к карточке — нет и блока.
        assert experience_block({}) == ""

        # Запуск без совпадения по каталогу пишется в kb_runs с пустым slug
        # (record_outcome так и делает). Без раннего выхода такие заметки
        # склеились бы в общий котёл «безымянных» продуктов и потекли бы в
        # любой другой безымянный запуск — чужой опыт под видом своего.
        knowledge.set_experience((_note(slug="", slogan="Из безымянного запуска"),))
        assert experience_block({"kb_match": {"slug": ""}}) == ""
        assert experience_block({}) == ""
    finally:
        knowledge.set_experience(())


def test_metaphor_kind_uses_metaphor_text():
    try:
        knowledge.set_experience((_note(),))
        block = experience_block(
            {"kb_match": {"slug": "managed-rag"}}, kind="metaphor"
        )
        assert "a bridge across a canyon" in block
        assert "GPU без очереди" not in block
    finally:
        knowledge.set_experience(())
