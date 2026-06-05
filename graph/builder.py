"""StateGraph assembly.

M3.3 — user-uploaded hero (no Phygital, no Figma MCP rendering):

    parse_brief
      -> derive_persona
      -> generate_message_candidates
      -> evaluate_as_persona_loop
        --(winner is None / revise)--> generate_message_candidates
        --(winner found)-------------> hitl_text_approve
                                       --(approve)-> route_image_style
                                       --(cancel)-> END
                                       --(regenerate / refine)-> generate_message_candidates

    route_image_style          (LLM classifier: photo | render | isometric)
      -> generate_image_prompt (LLM writes EN hero prompt for the user)
      -> hitl_image_upload     (interrupt; user pastes prompt into their
                                generator and uploads the result)
         --(upload)-> fill_templates_per_format -> render_all -> END
         --(cancel / timeout)-> END

M3.3 status:
- route_image_style: REAL LLM classifier (GLM-5.1).
- generate_image_prompt: REAL LLM writer (GLM-5.1, EN output).
- hitl_image_upload: REAL HITL (PTB photo/Document handler in bot/graph_runner).
- fill_templates_per_format: REAL PIL composer (no Figma at runtime).
- render_all: REAL (zipfile of per-format PNGs).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes.derive_persona import derive_persona
from graph.nodes.evaluate_as_persona_loop import evaluate_as_persona_loop
from graph.nodes.fill_templates_per_format import fill_templates_per_format
from graph.nodes.generate_image_prompt import generate_image_prompt
from graph.nodes.generate_message_candidates import generate_message_candidates
from graph.nodes.hitl_image_upload import hitl_image_upload
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


def _route_after_image_upload(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("image"):
        return "fill_templates_per_format"
    # Defensive fallback — should not happen because hitl_image_upload
    # always returns either an image, cancelled=True, or an error.
    return END


def build_text_graph() -> StateGraph:
    g: StateGraph = StateGraph(GraphState)

    g.add_node("parse_brief", parse_brief)
    g.add_node("derive_persona", derive_persona)
    g.add_node("generate_message_candidates", generate_message_candidates)
    g.add_node("evaluate_as_persona_loop", evaluate_as_persona_loop)
    g.add_node("hitl_text_approve", hitl_text_approve)
    g.add_node("route_image_style", route_image_style)
    g.add_node("generate_image_prompt", generate_image_prompt)
    g.add_node("hitl_image_upload", hitl_image_upload)
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
    g.add_edge("route_image_style", "generate_image_prompt")
    g.add_edge("generate_image_prompt", "hitl_image_upload")
    g.add_conditional_edges(
        "hitl_image_upload",
        _route_after_image_upload,
        {
            "fill_templates_per_format": "fill_templates_per_format",
            END: END,
        },
    )
    g.add_edge("fill_templates_per_format", "render_all")
    g.add_edge("render_all", END)
    return g
