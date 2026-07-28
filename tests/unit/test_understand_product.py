"""Unit tests for the understand_product node.

The node's job is grounding: pull the KB card, read the marketer's link, and
hand the model all three sources. What is worth pinning down offline is that
(a) each source actually reaches the prompt, and (b) an unreadable link
degrades the run instead of failing it — the URL is optional enrichment and a
dead link must not cost the marketer the whole generation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from graph.nodes import understand_product as mod
from graph.state import ProductBrief
from infra.urlfetch import UrlFetchError


_PRODUCT = ProductBrief(
    canonical_name="Evolution Managed RAG",
    what_it_is="Управляемый сервис поиска ответов по корпоративным документам.",
    key_capabilities=["индексация документов", "поиск по смыслу"],
    problems_solved=["ответы теряются в чатах", "долгий онбординг"],
    must_honour=["обязательно упомянуть бесплатный период"],
)


def _state(**brief) -> dict:
    base = {
        "product": "Evolution Managed RAG",
        "audience_raw": "техлиды",
        "emotion": "спокойная уверенность",
        "notes": "",
        "source_url": "",
    }
    base.update(brief)
    return {"session_id": "s-test", "brief": base}


@pytest.fixture
def captured(monkeypatch) -> dict:
    """Stub the LLM + prompt loading; capture the rendered user message."""
    seen: dict = {}
    monkeypatch.setattr(mod, "load_skill", lambda name: SimpleNamespace(body=""))
    monkeypatch.setattr(mod, "extract_section", lambda body, header: "tpl")
    monkeypatch.setattr(mod, "render", lambda tpl, **kw: seen.update(kw) or "rendered")

    async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
        return _PRODUCT

    monkeypatch.setattr(mod, "run_agent", fake_run_agent)
    return seen


async def test_kb_card_and_glossary_reach_the_prompt(captured):
    out = await mod.understand_product(_state())

    assert "Блок 1." in captured["kb_block"]
    assert "Блок 3." in captured["kb_block"]  # audiences travel with the card
    # the current product is not repeated in the neighbours' glossary
    assert "Evolution Managed RAG:" not in captured["glossary_block"]
    assert "Evolution Foundation Models" in captured["glossary_block"]
    assert out["product"]["canonical_name"] == "Evolution Managed RAG"


async def test_unknown_product_degrades_to_marketer_text(captured):
    await mod.understand_product(_state(product="Наша пиццерия"))
    assert "нет карточки" in captured["kb_block"]


async def test_notes_reach_the_prompt(captured):
    await mod.understand_product(_state(notes="нельзя слово «дешёвый»"))
    assert captured["notes_block"] == "нельзя слово «дешёвый»"


async def test_empty_notes_are_labelled(captured):
    await mod.understand_product(_state())
    assert "ничего не добавил" in captured["notes_block"]


async def test_page_text_reaches_the_prompt_and_is_kept_on_the_brief(
    captured, monkeypatch
):
    async def fake_fetch(url, **kw):
        assert url == "https://cloud.ru/products/rag"
        return "Managed RAG — страница продукта"

    monkeypatch.setattr(mod, "fetch_page_text", fake_fetch)

    out = await mod.understand_product(
        _state(source_url="  https://cloud.ru/products/rag  ")
    )

    assert captured["source_block"] == "Managed RAG — страница продукта"
    assert out["brief"]["source_text"] == "Managed RAG — страница продукта"


async def test_unreadable_url_does_not_fail_the_run(captured, monkeypatch):
    async def boom(url, **kw):
        raise UrlFetchError("host resolves to non-public 127.0.0.1")

    monkeypatch.setattr(mod, "fetch_page_text", boom)

    out = await mod.understand_product(_state(source_url="http://localhost/admin"))

    assert "не прочитана" in captured["source_block"]
    assert out["brief"]["source_text"] == ""
    assert out["product"]["canonical_name"] == "Evolution Managed RAG"


async def test_missing_brief_is_an_error(captured):
    with pytest.raises(ValueError, match="state.brief is missing"):
        await mod.understand_product({"session_id": "s"})
