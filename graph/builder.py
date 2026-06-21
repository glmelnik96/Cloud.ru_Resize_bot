"""StateGraph assembly.

App3 redesign (2026-06-21) — 12 propositions, light ranker, set approval:

    parse_brief
      -> derive_persona                  (ONE persona from audience + emotion)
      -> generate_message_candidates     (12 angles into that persona)
      -> rank_candidates                 (ONE light LLM call: orders the 12 by
                                          predicted resonance + one-line reason)
      -> hitl_text_approve               (interrupt; user sees all 12 ranked)
         --(approve)----> END            (the 12 propositions ARE the deliverable)
         --(regenerate)-> generate_message_candidates
         --(cancel)-----> END

The image/hero stage (route_image_style -> generate_image_prompt ->
hitl_image_upload -> fill_templates_per_format -> render_all) is FROZEN until
App1's /internal/hero is ready and the stage is reworked to be per-candidate
(decision 2026-06-21). Those node modules still exist but are NOT wired into the
compiled graph yet — the buildable pipeline ends at text approval.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes.derive_persona import derive_persona
from graph.nodes.generate_message_candidates import generate_message_candidates
from graph.nodes.hitl_text_approve import hitl_text_approve
from graph.nodes.parse_brief import parse_brief
from graph.nodes.rank_candidates import rank_candidates
from graph.state import GraphState


def _route_after_text_hitl(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("text_approved"):
        return END
    # regenerate — the set has been cleared in hitl_text_approve
    return "generate_message_candidates"


def build_text_graph() -> StateGraph:
    g: StateGraph = StateGraph(GraphState)

    g.add_node("parse_brief", parse_brief)
    g.add_node("derive_persona", derive_persona)
    g.add_node("generate_message_candidates", generate_message_candidates)
    g.add_node("rank_candidates", rank_candidates)
    g.add_node("hitl_text_approve", hitl_text_approve)

    g.add_edge(START, "parse_brief")
    g.add_edge("parse_brief", "derive_persona")
    g.add_edge("derive_persona", "generate_message_candidates")
    g.add_edge("generate_message_candidates", "rank_candidates")
    g.add_edge("rank_candidates", "hitl_text_approve")
    g.add_conditional_edges(
        "hitl_text_approve",
        _route_after_text_hitl,
        {
            "generate_message_candidates": "generate_message_candidates",
            END: END,
        },
    )
    return g
