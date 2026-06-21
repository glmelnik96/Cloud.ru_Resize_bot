"""StateGraph assembly.

App3 redesign (2026-06-21) — 12 propositions, light ranker, set approval,
then the image/render stage on the TOP-ranked proposition:

    parse_brief
      -> derive_persona                  (ONE persona from audience + emotion)
      -> generate_message_candidates     (12 angles into that persona)
      -> rank_candidates                 (ONE light LLM call: orders the 12 by
                                          predicted resonance + one-line reason)
      -> hitl_text_approve               (interrupt; user sees all 12 ranked)
         --(regenerate)-> generate_message_candidates
         --(cancel)-----> END
         --(approve)----> route_image_style
                          -> generate_image_prompt
                          -> hitl_image_upload      (interrupt; upload/generate)
                             --(cancel/timeout)-> END
                             --(upload)---------> fill_templates_per_format
                                                  -> render_all -> END

The image stage composes ONE proposition's text (the top-ranked, via
``chosen_candidate``) onto the user-provided hero across brief.formats, then
packs a ZIP. Per-candidate heroes (one hero per proposition) remain a future
extension.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes.derive_persona import derive_persona
from graph.nodes.fill_templates_per_format import fill_templates_per_format
from graph.nodes.generate_image_prompt import generate_image_prompt
from graph.nodes.generate_message_candidates import generate_message_candidates
from graph.nodes.hitl_image_upload import hitl_image_upload
from graph.nodes.hitl_text_approve import hitl_text_approve
from graph.nodes.parse_brief import parse_brief
from graph.nodes.rank_candidates import rank_candidates
from graph.nodes.render_all import render_all
from graph.nodes.route_image_style import route_image_style
from graph.state import GraphState


def _route_after_text_hitl(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("text_approved"):
        return "route_image_style"
    # regenerate — the set has been cleared in hitl_text_approve
    return "generate_message_candidates"


def _route_after_image_hitl(state: GraphState) -> str:
    # cancel / timeout park the run as cancelled; only an upload proceeds.
    if state.get("cancelled"):
        return END
    return "fill_templates_per_format"


def build_text_graph() -> StateGraph:
    g: StateGraph = StateGraph(GraphState)

    g.add_node("parse_brief", parse_brief)
    g.add_node("derive_persona", derive_persona)
    g.add_node("generate_message_candidates", generate_message_candidates)
    g.add_node("rank_candidates", rank_candidates)
    g.add_node("hitl_text_approve", hitl_text_approve)
    g.add_node("route_image_style", route_image_style)
    g.add_node("generate_image_prompt", generate_image_prompt)
    g.add_node("hitl_image_upload", hitl_image_upload)
    g.add_node("fill_templates_per_format", fill_templates_per_format)
    g.add_node("render_all", render_all)

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
            "route_image_style": "route_image_style",
            END: END,
        },
    )
    g.add_edge("route_image_style", "generate_image_prompt")
    g.add_edge("generate_image_prompt", "hitl_image_upload")
    g.add_conditional_edges(
        "hitl_image_upload",
        _route_after_image_hitl,
        {
            "fill_templates_per_format": "fill_templates_per_format",
            END: END,
        },
    )
    g.add_edge("fill_templates_per_format", "render_all")
    g.add_edge("render_all", END)
    return g
