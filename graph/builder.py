"""StateGraph assembly.

M1 + M2 + HITL text pipeline:

    parse_brief
      -> derive_persona
      -> generate_message_candidates
      -> evaluate_as_persona_loop
        --(winner is None / revise)--> generate_message_candidates
        --(winner found)-------------> hitl_text_approve
                                       --(approve / cancel)-> END
                                       --(regenerate / refine)-> generate_message_candidates

Image gen, Figma fill, render — added in M3 as additional nodes after the
``text_approved`` branch.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes.derive_persona import derive_persona
from graph.nodes.evaluate_as_persona_loop import evaluate_as_persona_loop
from graph.nodes.generate_message_candidates import generate_message_candidates
from graph.nodes.hitl_text_approve import hitl_text_approve
from graph.nodes.parse_brief import parse_brief
from graph.state import GraphState


def _route_after_eval(state: GraphState) -> str:
    if state.get("winner") is None:
        return "generate_message_candidates"
    return "hitl_text_approve"


def _route_after_hitl(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("text_approved"):
        # M3 will replace this with generate_image. For M2 we stop here.
        return END
    # regenerate or refine — winner has been cleared in hitl_text_approve
    return "generate_message_candidates"


def build_text_graph() -> StateGraph:
    g: StateGraph = StateGraph(GraphState)

    g.add_node("parse_brief", parse_brief)
    g.add_node("derive_persona", derive_persona)
    g.add_node("generate_message_candidates", generate_message_candidates)
    g.add_node("evaluate_as_persona_loop", evaluate_as_persona_loop)
    g.add_node("hitl_text_approve", hitl_text_approve)

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
        _route_after_hitl,
        {
            "generate_message_candidates": "generate_message_candidates",
            END: END,
        },
    )
    return g
