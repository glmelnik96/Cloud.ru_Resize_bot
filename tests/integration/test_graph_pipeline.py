"""End-to-end LangGraph text pipeline test.

Feeds a raw brief through parse_brief → derive_persona → generate → evaluate_loop.
Verifies that:
- AdBrief is parsed with controlled-vocab goal/channel
- At least 1 persona derived
- 3-5 candidates with distinct hook_angles
- Verdicts collected for each (candidate × persona)
- Either a winner is returned or revise_round advanced

Skipped if CLOUDRU_API_KEY not set (real network calls — costs tokens).
"""

from __future__ import annotations

import os
import sys

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from graph.builder import build_text_graph
from graph.state import AdBrief, MessageCandidate, Persona, Verdict


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("CLOUDRU_API_KEY"),
        reason="CLOUDRU_API_KEY not set; graph pipeline test skipped",
    ),
    pytest.mark.skipif(
        sys.version_info < (3, 11),
        reason=(
            "langgraph interrupt() in async nodes needs asyncio.Task(context=...) "
            "(Python 3.11+); runs in Docker (3.11), not on local 3.10"
        ),
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
        "revise_round": 0,
    }
    final = await graph.ainvoke(initial, config=cfg)
    # graph should pause at hitl_text_approve when a winner is picked
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

    # --- personas
    personas = [
        p if isinstance(p, Persona) else Persona.model_validate(p)
        for p in final["personas"]
    ]
    assert 1 <= len(personas) <= 3
    for p in personas:
        assert len(p.pain_points) >= 2

    # --- candidates
    candidates = [
        c if isinstance(c, MessageCandidate) else MessageCandidate.model_validate(c)
        for c in final["candidates"]
    ]
    assert 3 <= len(candidates) <= 5
    hooks = {c.hook_angle for c in candidates}
    assert len(hooks) >= min(len(candidates), 3), f"hook_angle diversity too low: {hooks}"
    for c in candidates:
        assert len(c.slogan) <= 100, f"slogan overflow: {c.slogan!r}"
        assert len(c.body) <= 250, f"body overflow: {c.body!r}"
        assert len(c.cta) <= 50, f"cta overflow: {c.cta!r}"

    # --- verdicts
    verdicts = [
        v if isinstance(v, Verdict) else Verdict.model_validate(v)
        for v in final["verdicts"]
    ]
    assert len(verdicts) == len(candidates) * len(personas)
    for v in verdicts:
        assert 0 <= v.resonance <= 10
        assert 0 <= v.clarity <= 10
        assert 0 <= v.action_intent <= 10

    # --- winner or revise advanced
    winner = final.get("winner")
    revise_round = final.get("revise_round", 0)
    assert winner is not None or revise_round >= 1, (
        "neither winner picked nor revise advanced — terminal state inconsistent"
    )
    if winner is not None:
        w = winner if isinstance(winner, MessageCandidate) else MessageCandidate.model_validate(winner)
        assert w.id in {c.id for c in candidates}
