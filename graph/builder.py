"""StateGraph assembly.

The text stage is "маркетолог предлагает, ЦА выбирает" (2026-07-28): the
generator over-produces from two creative stances and the persona rejects half.
Everything is grounded first, by a node that actually studies the product.

    understand_product                 (KB card + page behind the marketer's
                                        link + free-form notes -> ProductBrief)
      -> derive_persona                (ONE persona from audience + emotion,
                                        grounded in the KB's real segments)
      -> generate_message_candidates   (2 parallel calls x 12 = 24 drafts)
      -> select_by_persona             (the persona, in first person, keeps 12)
      -> lint_candidates               (флажки честности: код-фильтры +
                                        инверсионный судья; fail-open)
      -> hitl_text_approve             (interrupt; user sees the chosen 12)
         --(regenerate)-> generate_message_candidates
         --(cancel)-----> END
         --(approve)----> route_image_style
                          -> generate_image_prompt
                          -> hitl_image_upload      (interrupt; upload/generate)
                             --(cancel/timeout)-> END
                             --(upload)---------> fill_templates_per_format
                                                  -> render_all -> END

The image stage runs per selected proposition: a scenario and a metaphor prompt
each, one hero each, one 300x600 banner each, then a ZIP.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.nodes.derive_persona import derive_persona
from graph.nodes.fill_templates_per_format import fill_templates_per_format
from graph.nodes.generate_image_prompt import generate_image_prompt
from graph.nodes.generate_message_candidates import generate_message_candidates
from graph.nodes.hitl_image_upload import hitl_image_upload
from graph.nodes.hitl_text_approve import hitl_text_approve
from graph.nodes.lint_candidates import lint_candidates
from graph.nodes.render_all import render_all
from graph.nodes.route_image_style import route_image_style
from graph.nodes.select_by_persona import select_by_persona
from graph.nodes.understand_product import understand_product
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

    g.add_node("understand_product", understand_product)
    g.add_node("derive_persona", derive_persona)
    g.add_node("generate_message_candidates", generate_message_candidates)
    g.add_node("select_by_persona", select_by_persona)
    g.add_node("lint_candidates", lint_candidates)
    g.add_node("hitl_text_approve", hitl_text_approve)
    g.add_node("route_image_style", route_image_style)
    g.add_node("generate_image_prompt", generate_image_prompt)
    g.add_node("hitl_image_upload", hitl_image_upload)
    g.add_node("fill_templates_per_format", fill_templates_per_format)
    g.add_node("render_all", render_all)

    g.add_edge(START, "understand_product")
    g.add_edge("understand_product", "derive_persona")
    g.add_edge("derive_persona", "generate_message_candidates")
    g.add_edge("generate_message_candidates", "select_by_persona")
    g.add_edge("select_by_persona", "lint_candidates")
    g.add_edge("lint_candidates", "hitl_text_approve")
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
