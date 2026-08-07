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
    # approve теперь возвращает ranked и winner_id=None вдобавок к text_approved.
    _patch_interrupt(monkeypatch, {"action": "approve"})
    out = await mod.hitl_text_approve(_state())
    assert out["text_approved"] is True
    assert out["ranked"] == [{"slogan": "a"}, {"slogan": "b"}]
    assert out.get("winner_id") is None


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


# ----- Task 7: winner_id — победитель выбирается человеком ----------------


@pytest.mark.asyncio
async def test_approve_with_winner_reorders_ranked(monkeypatch):
    ranked = [{"id": "a", "slogan": "A"}, {"id": "b", "slogan": "B"}, {"id": "c", "slogan": "C"}]
    _patch_interrupt(monkeypatch, {"action": "approve", "winner_id": "b"})
    out = await mod.hitl_text_approve({"session_id": "s", "ranked": ranked})
    assert [c["id"] for c in out["ranked"]] == ["b", "a", "c"]
    assert out["winner_id"] == "b"
    assert out["text_approved"] is True


@pytest.mark.asyncio
async def test_approve_with_unknown_winner_is_fail_open(monkeypatch):
    """API валидирует раньше; узел не роняет граф, порядок остаётся скоринговым."""
    ranked = [{"id": "a"}, {"id": "b"}]
    _patch_interrupt(monkeypatch, {"action": "approve", "winner_id": "zzz"})
    out = await mod.hitl_text_approve({"session_id": "s", "ranked": ranked})
    assert [c["id"] for c in out["ranked"]] == ["a", "b"]
    assert out["text_approved"] is True


@pytest.mark.asyncio
async def test_approve_without_winner_keeps_order(monkeypatch):
    ranked = [{"id": "a"}, {"id": "b"}]
    _patch_interrupt(monkeypatch, {"action": "approve"})
    out = await mod.hitl_text_approve({"session_id": "s", "ranked": ranked})
    assert out.get("winner_id") is None
    assert [c["id"] for c in out["ranked"]] == ["a", "b"]
