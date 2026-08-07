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


def test_route_after_image_hitl_metaphor_loop():
    """metaphor_comment → generate_image_prompt; без него → fill_templates_per_format."""
    from langgraph.graph import END

    assert (
        builder._route_after_image_hitl({"metaphor_comment": "другой образ"})
        == "generate_image_prompt"
    )
    assert builder._route_after_image_hitl({}) == "fill_templates_per_format"
    # cancel по-прежнему ведёт в END
    assert builder._route_after_image_hitl({"cancelled": True}) == END


def test_persona_hitl_conditional_branches():
    """hitl_persona_approve должна иметь conditional-рёбра в три узла:
    generate_message_candidates, derive_persona и END (__end__)."""
    compiled = builder.build_text_graph().compile()
    cg = compiled.get_graph()
    persona_targets = {
        e.target
        for e in cg.edges
        if e.source == "hitl_persona_approve" and e.conditional
    }
    assert "generate_message_candidates" in persona_targets, (
        f"missing branch → generate_message_candidates; got {persona_targets}"
    )
    assert "derive_persona" in persona_targets, (
        f"missing branch → derive_persona; got {persona_targets}"
    )
    assert "__end__" in persona_targets, (
        f"missing branch → END; got {persona_targets}"
    )
