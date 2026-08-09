"""Слой «опыт»: инжект в graph.knowledge и блок для промптов.

Оба слоя знаний графа сбрасывает autouse-фикстура tests/unit/conftest.py
::_restore_graph_state — своего try/finally здесь не нужно ни одному тесту.
"""

from __future__ import annotations

import pytest

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
    knowledge.set_experience((_note(), _note(slug="other", slogan="Чужое")))
    block = experience_block({"kb_match": {"slug": "managed-rag"}})
    assert "GPU без очереди" in block
    assert "Чужое" not in block


def test_rejected_is_stored_but_never_reaches_prompts():
    """rejected копится в kb_runs (человеку он виден на странице библиотеки),
    но в промпт идёт только принятое: забракованный текст как «пример» —
    это подсказка повторить неудачу."""
    knowledge.set_experience((
        _note(outcome="rejected", slogan="Слишком общо", comment="ни о чём"),
    ))
    assert experience_block({"kb_match": {"slug": "managed-rag"}}) == ""


def test_block_keeps_five_freshest():
    knowledge.set_experience(tuple(_note(slogan=f"Слоган {i}") for i in range(8)))
    block = experience_block({"kb_match": {"slug": "managed-rag"}})
    assert block.count("\n") == 4  # пять строк
    assert "Слоган 0" in block and "Слоган 5" not in block


def test_no_kb_match_means_no_experience():
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


def test_metaphor_kind_uses_metaphor_text():
    knowledge.set_experience((_note(),))
    block = experience_block({"kb_match": {"slug": "managed-rag"}}, kind="metaphor")
    assert "a bridge across a canyon" in block
    assert "GPU без очереди" not in block


def test_line_carries_anchor_and_comment():
    """Комментарий человека — самый ценный сигнал слоя: он объясняет, ПОЧЕМУ
    слоган зашёл. Без проверки полной строки его (и якорь) можно выбросить
    правкой формата, и сюита этого не заметит."""
    knowledge.set_experience((_note(comment="взяли без правок"),))
    assert experience_block({"kb_match": {"slug": "managed-rag"}}) == (
        "- «GPU без очереди» — боль: очередь на GPU; комментарий: взяли без правок"
    )


def test_missing_anchor_falls_back_to_explicit_marker():
    """Пустой якорь нельзя молча схлопнуть в тире: строка «слоган — » читается
    как «якорь пустой по смыслу», а не «его не записали»."""
    knowledge.set_experience((_note(anchor="", comment=""),))
    assert experience_block({"kb_match": {"slug": "managed-rag"}}) == (
        "- «GPU без очереди» — якорь не записан"
    )


def test_multiline_comment_stays_one_line():
    """Маркетолог пишет комментарий буллетами в <textarea>. Дословная вставка
    разрывает список: перенос строки модель читает как ещё один отгруженный
    слоган, от которого секция велит уходить."""
    knowledge.set_experience((
        _note(comment="Взяли, но:\n- слишком длинно\n- CTA заменили"),
        _note(slogan="Второй слоган", comment="ок"),
    ))
    block = experience_block({"kb_match": {"slug": "managed-rag"}})
    assert len(block.splitlines()) == 2, "строк в блоке должно быть ровно по заметкам"
    assert "\n- слишком длинно" not in block
    assert "Взяли, но: - слишком длинно - CTA заменили" in block


def test_long_comment_is_capped():
    """comment принимает до 2000 символов (OutcomeIn): пять таких отметок
    перевесили бы сам бриф, ради которого промпт и собирается."""
    knowledge.set_experience((_note(comment="я" * 2000),))
    block = experience_block({"kb_match": {"slug": "managed-rag"}})
    assert len(block) < 500


def test_multiline_metaphor_stays_one_line():
    knowledge.set_experience((_note(metaphor="a bridge\nacross a canyon"),))
    block = experience_block({"kb_match": {"slug": "managed-rag"}}, kind="metaphor")
    assert block == "- a bridge across a canyon"


def test_empty_recipe_notes_do_not_eat_the_five():
    """Рецепт запуска собирается best-effort: _collect_recipe отдаёт {} на любой
    ошибке чтения чекпоинта, и в kb_runs штатно ложатся строки без слогана.
    Если пятёрку резать ДО отбора, пять таких пустышек оставят копирайтера без
    опыта вовсе — при живой и непустой библиотеке, и молча."""
    knowledge.set_experience(
        tuple(_note(slogan="", anchor="", metaphor="") for _ in range(5))
        + (_note(slogan="Шестой, годный"),)
    )
    block = experience_block({"kb_match": {"slug": "managed-rag"}})
    assert "Шестой, годный" in block


def test_empty_metaphor_notes_do_not_eat_the_five():
    knowledge.set_experience(
        tuple(_note(metaphor="") for _ in range(5))
        + (_note(metaphor="a lighthouse in fog"),)
    )
    block = experience_block({"kb_match": {"slug": "managed-rag"}}, kind="metaphor")
    assert "a lighthouse in fog" in block


def test_unknown_kind_is_an_error():
    """Опечатка («metafor») тихо дала бы текстовую ветку — русские слоганы
    внутри английского промпта картинки."""
    knowledge.set_experience((_note(),))
    with pytest.raises(ValueError, match="metafor"):
        experience_block(
            {"kb_match": {"slug": "managed-rag"}},
            kind="metafor",  # type: ignore[arg-type]
        )
