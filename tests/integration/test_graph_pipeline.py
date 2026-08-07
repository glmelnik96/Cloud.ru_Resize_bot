"""End-to-end LangGraph text pipeline test.

Feeds a structured brief through understand_product → derive_persona →
hitl_persona_approve → generate_message_candidates → select_by_persona.
Verifies that:
- the product card is assembled from the knowledge base (no LLM round trip of
  the marketer's own wording, which is what parse_brief used to do)
- exactly ONE persona derived (audience is single)
- the graph parks first at hitl_persona_approve (persona pause, stop 1)
- then at hitl_text_approve (text approval, stop 2)
- 24 candidates generated (two batches of 12)
- the persona selects exactly 12, each carrying score + reason, best-first

Skipped if CLOUDRU_API_KEY not set (real network calls — costs tokens).
"""

from __future__ import annotations

import os
import sys

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from graph.builder import build_text_graph
from graph.state import AdBrief, MessageCandidate, Persona, ProductBrief

# interrupt() in async nodes needs Python 3.11+. On 3.10 langgraph's get_config()
# hard-branches and the runnable-config contextvar isn't propagated into async
# child nodes → "Called get_config outside of a runnable context" (Bug 1, prod).
# The langgraph version does NOT change this — it's the Python floor. This e2e
# resumes through the text-approve interrupt, so it guards that fix on 3.11+.
pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("CLOUDRU_API_KEY"),
        reason="CLOUDRU_API_KEY not set; graph pipeline test skipped",
    ),
    pytest.mark.skipif(
        sys.version_info < (3, 11),
        reason="interrupt() in async nodes requires Python 3.11+ (prod runs 3.11)",
    ),
]


# A product that HAS a knowledge-base card, so the run exercises the KB lookup
# rather than only the marketer's free text.
_BRIEF = {
    "product": "Evolution Managed RAG",
    "audience_raw": (
        "технические лиды и архитекторы в средних российских tech-компаниях, "
        "30–45 лет, у них разрослась внутренняя документация и никто в ней "
        "ничего не находит"
    ),
    "emotion": "спокойная уверенность — будто вся база знаний под рукой",
    "notes": "обязательно упомянуть, что это управляемый сервис; без пафоса, без эмодзи",
    "source_url": "",
}


@pytest.mark.asyncio
async def test_text_pipeline_e2e() -> None:
    graph = build_text_graph().compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "test-e2e-001"}, "recursion_limit": 30}

    initial = {
        "session_id": "test-e2e-001",
        "user_id": 0,
        "brief": _BRIEF,
    }
    final = await graph.ainvoke(initial, config=cfg)

    # --- stop 1: graph must pause at hitl_persona_approve
    interrupts = final.get("__interrupt__")
    assert interrupts, "graph did not pause at persona stop"
    assert interrupts[0].value.get("kind") == "persona_approve", (
        f"Expected first interrupt kind='persona_approve', got: {interrupts[0].value}"
    )
    # resume with approve so persona is accepted and pipeline continues
    final = await graph.ainvoke(Command(resume={"action": "approve"}), config=cfg)

    # --- stop 2: graph must pause at hitl_text_approve once the 12 are selected
    interrupts = final.get("__interrupt__")
    assert interrupts, "graph did not pause at text approve stop"
    assert interrupts[0].value.get("kind") == "text_approve", (
        f"Expected second interrupt kind='text_approve', got: {interrupts[0].value}"
    )
    # resume with approve so the pipeline reaches terminal state
    final = await graph.ainvoke(Command(resume={"action": "approve"}), config=cfg)
    assert final.get("text_approved") is True

    # --- brief survives verbatim (no LLM re-parse)
    brief = final["brief"]
    if not isinstance(brief, AdBrief):
        brief = AdBrief.model_validate(brief)
    assert brief.product == _BRIEF["product"]
    assert brief.audience_raw == _BRIEF["audience_raw"]
    assert brief.notes == _BRIEF["notes"]

    # --- product card (facts, not slogans)
    product = final["product"]
    if not isinstance(product, ProductBrief):
        product = ProductBrief.model_validate(product)
    assert "RAG" in product.canonical_name
    assert len(product.key_capabilities) >= 2
    assert len(product.problems_solved) >= 2
    assert product.must_honour, "the marketer's free field carried a hard requirement"

    # --- persona (exactly one)
    personas = [
        p if isinstance(p, Persona) else Persona.model_validate(p)
        for p in final["personas"]
    ]
    assert len(personas) == 1
    assert len(personas[0].pain_points) >= 2

    # --- candidates (24 = two batches of 12)
    candidates = [
        c if isinstance(c, MessageCandidate) else MessageCandidate.model_validate(c)
        for c in final["candidates"]
    ]
    assert len(candidates) == 24
    assert len({c.id for c in candidates}) == 24, "candidate ids collided across batches"
    for c in candidates:
        assert len(c.slogan) <= 100, f"slogan overflow: {c.slogan!r}"
        assert len(c.body) <= 250, f"body overflow: {c.body!r}"
        assert len(c.cta) <= 50, f"cta overflow: {c.cta!r}"

    # --- selection (exactly 12 of the 24, best-first, each with score + reason)
    ranked = final["ranked"]
    assert len(ranked) == 12
    ids = {r["id"] for r in ranked}
    assert len(ids) == 12, "duplicate picks"
    assert ids <= {c.id for c in candidates}, "picked an id outside the pool"
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True), "ranked not ordered best-first"
    for r in ranked:
        assert 0 <= r["score"] <= 10
        assert isinstance(r["reason"], str)
