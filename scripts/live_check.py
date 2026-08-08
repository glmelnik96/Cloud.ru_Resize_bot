"""Live smoke of the text pipeline against the real Cloud.ru models.

Runs the REAL compiled graph (understand_product -> derive_persona ->
generate_message_candidates -> select_by_persona -> hitl_text_approve, resumed
through `approve`) on briefs that differ in how much grounding they carry, and
asserts the invariants the design promises.

It exists because the prod venv has no pytest and installing dev deps into it
to run one e2e would change the deployed environment. Run from the repo root:

    .venv/bin/python scripts/live_check.py

Costs tokens — every case is a full set of real LLM calls.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from graph.builder import build_text_graph
from graph.state import MessageCandidate, Persona, ProductBrief

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# The case that killed prod on 2026-07-28 comes first: a product with no KB
# card, no link and no notes leaves the model nothing to ground on.
CASES: list[dict] = [
    {
        "name": "ungrounded (prod crash repro)",
        "expect_empty_proof": True,
        "brief": {
            "product": "Виртуальные машины",
            "audience_raw": "системные администраторы небольших компаний",
            "emotion": "уверенность, что сервер не упадёт",
            "notes": "",
            "source_url": "",
        },
    },
    {
        "name": "knowledge-base card only",
        "expect_empty_proof": False,
        "brief": {
            "product": "Evolution Managed RAG",
            "audience_raw": (
                "технические лиды в средних российских tech-компаниях, 30–45 лет, "
                "внутренняя документация разрослась и никто в ней ничего не находит"
            ),
            "emotion": "спокойная уверенность — будто вся база знаний под рукой",
            "notes": "обязательно упомянуть, что это управляемый сервис; без пафоса",
            "source_url": "",
        },
    },
    {
        "name": "link + free-form field",
        "expect_empty_proof": False,
        "brief": {
            "product": "ИТ-инфраструктура в облаке",
            "audience_raw": "ИТ-директора среднего бизнеса, переносят инфраструктуру из своего ЦОД",
            "emotion": "спокойствие: инфраструктура под контролем",
            "notes": "обязательно упомянуть, что ЦОД в России",
            "source_url": "https://cloud.ru/solutions/infrastruktura-v-oblake",
        },
    },
]


def _check(cond: bool, msg: str, failures: list[str]) -> None:
    if not cond:
        failures.append(msg)


async def run_case(case: dict, idx: int) -> list[str]:
    failures: list[str] = []
    graph = build_text_graph().compile(checkpointer=MemorySaver())
    thread = f"live-check-{idx}-{int(time.time())}"
    cfg = {"configurable": {"thread_id": thread}, "recursion_limit": 30}

    started = time.monotonic()
    final = await graph.ainvoke(
        {"session_id": thread, "user_id": 0, "brief": case["brief"]}, config=cfg
    )

    # Топология v2: первая остановка — персона, вторая — текст.
    _check(bool(final.get("__interrupt__")), "graph did not park at hitl_persona_approve", failures)
    if final.get("__interrupt__"):
        kind = (final["__interrupt__"][0].value or {}).get("kind")
        _check(kind == "persona_approve", f"first stop is {kind!r}, not persona_approve", failures)
        final = await graph.ainvoke(Command(resume={"action": "approve"}), config=cfg)

    _check(bool(final.get("__interrupt__")), "graph did not park at hitl_text_approve", failures)
    if final.get("__interrupt__"):
        kind = (final["__interrupt__"][0].value or {}).get("kind")
        _check(kind == "text_approve", f"second stop is {kind!r}, not text_approve", failures)
        final = await graph.ainvoke(Command(resume={"action": "approve"}), config=cfg)
        _check(final.get("text_approved") is True, "approve did not set text_approved", failures)

    elapsed = time.monotonic() - started

    # --- product card
    product = ProductBrief.model_validate(final["product"])
    _check(len(product.what_it_is) >= 20, "what_it_is too short", failures)
    if case["expect_empty_proof"]:
        _check(
            product.proof_points == [],
            f"invented proof points with no sources: {product.proof_points}",
            failures,
        )
    if case["brief"]["notes"]:
        _check(bool(product.must_honour), "free-form field produced no must_honour", failures)

    # --- persona (audience is single)
    personas = [Persona.model_validate(p) for p in final["personas"]]
    _check(len(personas) == 1, f"expected 1 persona, got {len(personas)}", failures)

    # --- 24 drafts = two batches of 12, ids unique across batches
    cands = [MessageCandidate.model_validate(c) for c in final["candidates"]]
    _check(len(cands) == 24, f"expected 24 drafts, got {len(cands)}", failures)
    _check(len({c.id for c in cands}) == len(cands), "candidate ids collided across batches", failures)
    for c in cands:
        _check(len(c.slogan) <= 42, f"slogan over hard cap ({len(c.slogan)}): {c.slogan!r}", failures)
        _check(not _CJK.search(c.slogan), f"CJK in slogan: {c.slogan!r}", failures)
        _check(not _CJK.search(c.cta), f"CJK in cta: {c.cta!r}", failures)

    # --- the persona keeps exactly 12, best-first, each with score + reason
    ranked = final["ranked"]
    pool = {c.id for c in cands}
    _check(len(ranked) == 12, f"expected 12 selected, got {len(ranked)}", failures)
    _check(len({r["id"] for r in ranked}) == len(ranked), "duplicate picks", failures)
    _check({r["id"] for r in ranked} <= pool, "picked an id outside the pool", failures)
    scores = [r["score"] for r in ranked]
    _check(scores == sorted(scores, reverse=True), "ranked not ordered best-first", failures)
    for r in ranked:
        _check(0 <= r["score"] <= 10, f"score out of band: {r['score']}", failures)
        _check(bool(str(r.get("reason", "")).strip()), "pick has no reason", failures)

    by_id = {c.id: c for c in cands}
    top = by_id[ranked[0]["id"]]
    print(f"    card:   {product.canonical_name} | caps={len(product.key_capabilities)} "
          f"problems={len(product.problems_solved)} proof={len(product.proof_points)} "
          f"must_honour={len(product.must_honour)}")
    print(f"    persona: {personas[0].segment} ({personas[0].age_range})")
    print(f"    top pick: {top.slogan!r} / {top.cta!r}  score={ranked[0]['score']}")
    print(f"    elapsed: {elapsed:.0f}s")
    return failures


async def main() -> int:
    if not os.environ.get("CLOUDRU_API_KEY"):
        print("CLOUDRU_API_KEY not set", file=sys.stderr)
        return 2

    total: list[str] = []
    for idx, case in enumerate(CASES, 1):
        print(f"\n[{idx}/{len(CASES)}] {case['name']}")
        try:
            failures = await run_case(case, idx)
        except Exception as exc:  # noqa: BLE001 — the point is to report, not raise
            failures = [f"{type(exc).__name__}: {exc}"]
        if failures:
            for f in failures:
                print(f"    FAIL  {f}")
        else:
            print("    OK")
        total.extend(f"[{case['name']}] {f}" for f in failures)

    print("\n" + "=" * 60)
    if total:
        print(f"{len(total)} FAILURES")
        for f in total:
            print(f"  - {f}")
        return 1
    print(f"all {len(CASES)} live cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
