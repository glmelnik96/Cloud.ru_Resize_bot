"""Маршрутизация после persona-паузы + топология с тремя остановками.

Проверяет:
- _route_after_persona_hitl: cancel → END, approve → generate_message_candidates,
  regenerate (personas сброшены в узле) → derive_persona
- build_text_graph включает узел hitl_persona_approve
- ребро derive_persona → hitl_persona_approve (НЕ напрямую к generate_message_candidates)
"""

from __future__ import annotations

from langgraph.graph import END

from graph import builder


def test_route_after_persona_cancel():
    assert builder._route_after_persona_hitl({"cancelled": True}) == END


def test_route_after_persona_approve():
    assert (
        builder._route_after_persona_hitl({"persona_approved": True})
        == "generate_message_candidates"
    )


def test_route_after_persona_regenerate():
    # personas очищены в hitl_persona_approve; state без cancelled/persona_approved
    assert builder._route_after_persona_hitl({}) == "derive_persona"


def test_graph_contains_persona_node():
    g = builder.build_text_graph()
    assert "hitl_persona_approve" in g.nodes


def test_graph_edge_derive_to_persona_hitl():
    """derive_persona должна вести в hitl_persona_approve, а не напрямую в generate."""
    g = builder.build_text_graph()
    edges = set(g.edges)
    assert ("derive_persona", "hitl_persona_approve") in edges
    # прямого ребра derive_persona → generate_message_candidates быть не должно
    assert ("derive_persona", "generate_message_candidates") not in edges
