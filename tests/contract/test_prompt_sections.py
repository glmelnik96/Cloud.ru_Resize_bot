"""Контракт «промпт ↔ узел»: секции, которые код выдирает из SKILL.md, есть.

`graph.prompts.extract_section` кидает ValueError, когда заголовка нет или под
ним нет ```-блока. Для секций, которые узел читает всегда (System message, User
message template), это ловится любым тестом узла. А «## Experience addendum»
читается ТОЛЬКО когда у продукта есть отмеченный опыт — на пустой библиотеке
кода этой ветки не видно, и переименование заголовка при правке промпта прошло
бы всю сюиту, чтобы упасть в проде у первого же продукта с опытом.

Отдельный файл, а не test_import_arch: тот кодифицирует границы импортов и
читает исходники через AST — здесь же контракт между md-файлом и узлом.
"""

from __future__ import annotations

import pytest

from graph.prompts import extract_section, load_skill

# Промпт → секции, которые из него извлекает код узла. Список ведётся руками:
# добавил extract_section в узле — допиши сюда, иначе секция снова окажется
# покрыта только тем сценарием, при котором узел до неё дошёл.
REQUIRED_SECTIONS = {
    # graph/nodes/generate_message_candidates.py
    "creative_ads_explorer": (
        "## System message",
        "## User message template",
        "## Experience addendum",
    ),
    # graph/nodes/generate_image_prompt.py
    "generate_image_prompt": (
        "## System message",
        "## User message template",
        "## Experience addendum",
    ),
}


@pytest.mark.parametrize(
    ("skill_name", "heading"),
    [(name, h) for name, headings in REQUIRED_SECTIONS.items() for h in headings],
)
def test_prompt_section_is_extractable(skill_name: str, heading: str):
    body = load_skill(skill_name).body
    assert extract_section(body, heading).strip(), (
        f"{skill_name}.md: секция {heading!r} пуста или её нет — "
        "узел упадёт ValueError на первом же запуске, который до неё дойдёт"
    )


def test_experience_addendum_carries_the_placeholder():
    """Без {{experience_block}} секция допишется к промпту пустой болванкой:
    модель получит заголовок «что уже отгружено» без единой строки под ним."""
    for skill_name in REQUIRED_SECTIONS:
        section = extract_section(load_skill(skill_name).body, "## Experience addendum")
        assert "{{experience_block}}" in section, skill_name
