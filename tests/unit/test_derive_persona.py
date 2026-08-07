"""Unit tests for the derive_persona node.

Covers:
- Task 4: state.kb_match используется вместо повторного knowledge.find_product;
  при kb_match=None — блок аудиторий = fallback-строка «нет карточки».
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph import knowledge
from graph.knowledge import ProductDoc
from graph.nodes import derive_persona as mod
from graph.state import Persona, PersonaSet

_PERSONA = Persona(
    segment="DevOps",
    age_range="28-40",
    pain_points=["downtime"],
    motivations=["надёжность"],
    objections=["дорого"],
    communication_style="рациональный",
)

_PERSONA_SET = PersonaSet(personas=[_PERSONA])


def _state(**overrides) -> dict:
    base: dict = {
        "session_id": "s-dp-test",
        "brief": {
            "product": "Evolution ML Inference",
            "audience_raw": "ML-инженеры",
            "emotion": "доверие",
            "notes": "",
            "source_url": "",
        },
        "product": {
            "canonical_name": "Evolution ML Inference",
            "what_it_is": "Сервис инференса ML-моделей.",
            "key_capabilities": [],
            "problems_solved": [],
            "proof_points": [],
            "vocabulary": [],
            "must_honour": [],
        },
        # kb_match по умолчанию отсутствует — узел должен работать без него
    }
    base.update(overrides)
    return base


@pytest.fixture
def captured(monkeypatch) -> dict:
    """Stub LLM + prompt loading; захватывает kwargs _render."""
    seen: dict = {}
    monkeypatch.setattr(mod, "load_skill", lambda name: SimpleNamespace(body=""))
    monkeypatch.setattr(mod, "_extract_section", lambda body, header: "tpl")
    monkeypatch.setattr(
        mod, "_render", lambda tpl, **kw: seen.update(kw) or "rendered"
    )

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return _PERSONA_SET

    monkeypatch.setattr(mod, "run_agent", fake_run_agent)
    return seen


# ── Task 4 tests ───────────────────────────────────────────────────────


def _make_doc(slug: str, name: str) -> ProductDoc:
    return ProductDoc(
        slug=slug,
        name=name,
        aliases=(name,),
        tagline=f"{name} tagline",
        body=(
            "## Блок 1. Что это\nОписание продукта.\n\n"
            "## Блок 3. Аудитории\nАудитория-специфично для " + name + ".\n"
        ),
    )


async def test_derive_persona_uses_state_kb_match(captured):
    """Если state.kb_match задан, kb_audiences_block берётся из карточки."""
    doc = _make_doc("evolution-ml-inference", "Evolution ML Inference")
    prev = knowledge._catalog_override
    try:
        knowledge.set_catalog((doc,))
        state = _state(
            kb_match={
                "slug": "evolution-ml-inference",
                "name": "Evolution ML Inference",
                "version": 1,
            }
        )
        await mod.derive_persona(state)
    finally:
        knowledge.set_catalog(prev)

    kb_block = captured["kb_audiences_block"]
    # doc.audiences — это Блок 3; проверяем, что он попал в промпт
    assert "Аудитория-специфично для Evolution ML Inference" in kb_block


async def test_derive_persona_no_kb_match_means_no_kb_block(captured):
    """kb_match=None → fallback-строка «нет карточки аудиторий»."""
    state = _state(kb_match=None)
    await mod.derive_persona(state)

    kb_block = captured["kb_audiences_block"]
    assert "нет карточки" in kb_block
