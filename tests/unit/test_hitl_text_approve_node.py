"""Unit tests for graph.nodes.hitl_text_approve.

Same trick as the image-upload node tests: monkey-patch the ``interrupt``
symbol imported into the module so each resume-action can be driven without a
checkpointer.

The timeout branch is what makes an abandoned text session distinguishable from
a deliberate cancel — the service reads the error marker to pick meta.reason.
"""

from __future__ import annotations

import pytest

from graph.nodes import hitl_text_approve as mod


def _patch_interrupt(monkeypatch, decision):
    def _fake_interrupt(payload):
        _fake_interrupt.last_payload = payload
        return decision

    _fake_interrupt.last_payload = None
    monkeypatch.setattr(mod, "interrupt", _fake_interrupt)
    return _fake_interrupt


def _state():
    return {"session_id": "sT", "ranked": [{"slogan": "a"}, {"slogan": "b"}]}


@pytest.mark.asyncio
async def test_approve_marks_the_set_accepted(monkeypatch):
    _patch_interrupt(monkeypatch, {"action": "approve"})
    assert await mod.hitl_text_approve(_state()) == {"text_approved": True}


@pytest.mark.asyncio
async def test_regenerate_clears_the_set(monkeypatch):
    _patch_interrupt(monkeypatch, {"action": "regenerate"})
    assert await mod.hitl_text_approve(_state()) == {"candidates": [], "ranked": []}


@pytest.mark.asyncio
async def test_cancel_carries_no_error(monkeypatch):
    """A deliberate cancel must stay clean — an error marker here would make the
    terminal row read as an abandoned session."""
    _patch_interrupt(monkeypatch, {"action": "cancel"})
    out = await mod.hitl_text_approve(_state())
    assert out == {"text_approved": False, "cancelled": True}


@pytest.mark.asyncio
async def test_timeout_marks_the_abandoned_session(monkeypatch):
    _patch_interrupt(monkeypatch, {"action": "timeout"})
    out = await mod.hitl_text_approve(_state())
    assert out["cancelled"] is True
    assert out["error"] == "text_approve_timeout"
