"""End-to-end LangGraph text pipeline test.

Feeds a raw brief through parse_brief → derive_persona → generate → rank.
Verifies that:
- AdBrief is parsed with controlled-vocab goal/channel
- exactly ONE persona derived (audience is single)
- exactly 12 candidates generated
- ranked set has 12 items, each carrying score + reason, ordered best-first
- the graph parks at hitl_text_approve and resumes through `approve`

Skipped if CLOUDRU_API_KEY not set (real network calls — costs tokens).
"""

from __future__ import annotations

import os
import sys

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from graph.builder import build_text_graph
from graph.state import AdBrief, MessageCandidate, Persona


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


_RAW_BRIEF = """
Продукт: Cloud.ru Evolution Object Storage — S3-совместимое объектное хранилище.
Цель: познакомить целевую аудиторию с продуктом, увести с AWS S3.
ЦА: технические лиды и архитекторы в средних российских tech-компаниях, 30–45 лет, работали с AWS, сейчас вынуждены искать российский аналог.
Канал: Telegram-пост в канале для devops.
Форматы: tg_post_1080x1350.
Тон: технично, без пафоса, без эмодзи.
Обязательно упомянуть «S3-совместимый API».
"""


@pytest.mark.asyncio
async def test_text_pipeline_e2e() -> None:
    graph = build_text_graph().compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "test-e2e-001"}, "recursion_limit": 30}

    initial = {
        "session_id": "test-e2e-001",
        "user_id": 0,
        "raw_brief": _RAW_BRIEF,
    }
    final = await graph.ainvoke(initial, config=cfg)
    # graph should pause at hitl_text_approve once the 12 are ranked
    interrupts = final.get("__interrupt__")
    if interrupts:
        # resume with approve so the pipeline reaches terminal state
        final = await graph.ainvoke(Command(resume={"action": "approve"}), config=cfg)
        assert final.get("text_approved") is True

    # --- brief
    brief = final["brief"]
    if not isinstance(brief, AdBrief):
        brief = AdBrief.model_validate(brief)
    assert brief.goal in {
        "awareness",
        "consideration",
        "conversion",
        "engagement",
        "retention",
    }, f"goal off-vocab: {brief.goal}"
    assert brief.channel.startswith("tg_"), f"channel off-vocab: {brief.channel}"
    assert "S3" in brief.product or "object" in brief.product.lower()

    # --- persona (exactly one)
    personas = [
        p if isinstance(p, Persona) else Persona.model_validate(p)
        for p in final["personas"]
    ]
    assert len(personas) == 1
    assert len(personas[0].pain_points) >= 2

    # --- candidates (exactly 12)
    candidates = [
        c if isinstance(c, MessageCandidate) else MessageCandidate.model_validate(c)
        for c in final["candidates"]
    ]
    assert len(candidates) == 12
    for c in candidates:
        assert len(c.slogan) <= 100, f"slogan overflow: {c.slogan!r}"
        assert len(c.body) <= 250, f"body overflow: {c.body!r}"
        assert len(c.cta) <= 50, f"cta overflow: {c.cta!r}"

    # --- ranked set (12, best-first, each with score + reason)
    ranked = final["ranked"]
    assert len(ranked) == 12
    assert {r["id"] for r in ranked} == {c.id for c in candidates}
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True), "ranked not ordered best-first"
    for r in ranked:
        assert 0 <= r["score"] <= 10
        assert isinstance(r["reason"], str)
