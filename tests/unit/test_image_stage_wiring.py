"""Image stage reconnection (2026-06-21).

Block 1 dropped the single ``winner`` for 12 ranked propositions; the image
stage now composes the TOP-ranked proposition onto one hero. These tests cover:
- the ``chosen_candidate`` helper that picks ranked[0],
- the builder wiring the 5 image nodes after text approval.
"""
from __future__ import annotations

import pytest

from graph.nodes import chosen_candidate
from graph.state import MessageCandidate


def test_chosen_candidate_picks_top_ranked():
    state = {
        "ranked": [
            {"id": "c2", "slogan": "Best", "body": "B2", "cta": "Go", "hook_angle": "rational", "score": 9.1, "reason": "r"},
            {"id": "c1", "slogan": "Second", "body": "B1", "cta": "Buy", "hook_angle": "curiosity", "score": 4.0, "reason": "r"},
        ]
    }
    chosen = chosen_candidate(state)  # type: ignore[arg-type]
    assert isinstance(chosen, MessageCandidate)
    assert chosen.slogan == "Best"
    assert chosen.cta == "Go"
    # extra ranker keys (score/reason) are ignored by the model
    assert not hasattr(chosen, "score")


def test_chosen_candidate_empty_ranked_raises():
    with pytest.raises(ValueError, match="ranked is empty"):
        chosen_candidate({"ranked": []})  # type: ignore[arg-type]


def test_chosen_candidate_accepts_model_instances():
    mc = MessageCandidate(slogan="S", body="B", cta="C", hook_angle="rational")
    assert chosen_candidate({"ranked": [mc]}).slogan == "S"  # type: ignore[arg-type]


def test_builder_wires_image_stage():
    from langgraph.graph import END

    from graph.builder import build_text_graph

    g = build_text_graph()
    nodes = set(g.nodes)
    for n in (
        "route_image_style",
        "generate_image_prompt",
        "hitl_image_upload",
        "fill_templates_per_format",
        "render_all",
    ):
        assert n in nodes, f"{n} not wired into the graph"
    # compiles without a checkpointer (structure check)
    compiled = g.compile()
    assert compiled is not None
