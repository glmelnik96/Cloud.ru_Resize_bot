"""StateGraph assembly.

M1 + M2 + M3.0 image pipeline (stubs for image gen & Figma rendering):

    parse_brief
      -> derive_persona
      -> generate_message_candidates
      -> evaluate_as_persona_loop
        --(winner is None / revise)--> generate_message_candidates
        --(winner found)-------------> hitl_text_approve
                                       --(approve)-> route_image_style
                                       --(cancel)-> END
                                       --(regenerate / refine)-> generate_message_candidates

    route_image_style
      -> generate_image
      -> hitl_image_approve
         --(approve)-> fill_templates_per_format -> render_all -> END
         --(cancel)-> END
         --(regenerate / refine)-> generate_image

M3.0 status:
- route_image_style: REAL LLM classifier (GLM-5.1).
- generate_image: STUB (PIL placeholder PNG) — Phygital adapter is M3.1.
- fill_templates_per_format: STUB (local compositing) — Figma MCP adapter is M3.2.
- render_all: REAL (zipfile of per-format PNGs).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes.derive_persona import derive_persona
from graph.nodes.evaluate_as_persona_loop import evaluate_as_persona_loop
from graph.nodes.fill_templates_per_format import fill_templates_per_format
from graph.nodes.generate_image import generate_image
from graph.nodes.generate_message_candidates import generate_message_candidates
from graph.nodes.hitl_image_approve import hitl_image_approve
from graph.nodes.hitl_text_approve import hitl_text_approve
from graph.nodes.parse_brief import parse_brief
from graph.nodes.render_all import render_all
from graph.nodes.route_image_style import route_image_style
from graph.state import GraphState


def _route_after_eval(state: GraphState) -> str:
    if state.get("winner") is None:
        return "generate_message_candidates"
    return "hitl_text_approve"


def _route_after_text_hitl(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("text_approved"):
        return "route_image_style"
    # regenerate or refine — winner has been cleared in hitl_text_approve
    return "generate_message_candidates"


def _route_after_image_hitl(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("image_approved"):
        return "fill_templates_per_format"
    # regenerate or refine — image has been cleared in hitl_image_approve
    return "generate_image"


def build_text_graph() -> StateGraph:
    g: StateGraph = StateGraph(GraphState)

    g.add_node("parse_brief", parse_brief)
    g.add_node("derive_persona", derive_persona)
    g.add_node("generate_message_candidates", generate_message_candidates)
    g.add_node("evaluate_as_persona_loop", evaluate_as_persona_loop)
    g.add_node("hitl_text_approve", hitl_text_approve)
    g.add_node("route_image_style", route_image_style)
    g.add_node("generate_image", generate_image)
    g.add_node("hitl_image_approve", hitl_image_approve)
    g.add_node("fill_templates_per_format", fill_templates_per_format)
    g.add_node("render_all", render_all)

    g.add_edge(START, "parse_brief")
    g.add_edge("parse_brief", "derive_persona")
    g.add_edge("derive_persona", "generate_message_candidates")
    g.add_edge("generate_message_candidates", "evaluate_as_persona_loop")
    g.add_conditional_edges(
        "evaluate_as_persona_loop",
        _route_after_eval,
        {
            "generate_message_candidates": "generate_message_candidates",
            "hitl_text_approve": "hitl_text_approve",
        },
    )
    g.add_conditional_edges(
        "hitl_text_approve",
        _route_after_text_hitl,
        {
            "generate_message_candidates": "generate_message_candidates",
            "route_image_style": "route_image_style",
            END: END,
        },
    )
    g.add_edge("route_image_style", "generate_image")
    g.add_edge("generate_image", "hitl_image_approve")
    g.add_conditional_edges(
        "hitl_image_approve",
        _route_after_image_hitl,
        {
            "generate_image": "generate_image",
            "fill_templates_per_format": "fill_templates_per_format",
            END: END,
        },
    )
    g.add_edge("fill_templates_per_format", "render_all")
    g.add_edge("render_all", END)
    return g
