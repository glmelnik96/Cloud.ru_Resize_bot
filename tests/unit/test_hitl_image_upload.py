"""Unit tests — metaphor feedback loop in hitl_image_upload (Task 8).

Проверяет:
- action "metaphor" сохраняет comment в metaphor_comment и metaphor_comments.
- Пустой comment не перегенерирует и не отменяет.
- История comments накапливается (append, не перезапись).
"""

from __future__ import annotations

import pytest

from graph.nodes import hitl_image_upload as mod


def _state(**overrides):
    base = {
        "session_id": "sX",
        "image_prompt": "A serene render of a server. No text, no letters, no logos.",
        "image_style": "render",
    }
    base.update(overrides)
    return base


@pytest.fixture
def decide(monkeypatch):
    """Fixture: подменяет interrupt, возвращает заданное decision."""

    def _setup(decision: dict):
        def _fake_interrupt(payload):
            return decision

        monkeypatch.setattr(mod, "interrupt", _fake_interrupt)

    return _setup


@pytest.mark.asyncio
async def test_metaphor_action_stores_comment(decide):
    decide({"action": "metaphor", "comment": "не часы, покажи исчезающую очередь"})
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert out["metaphor_comment"] == "не часы, покажи исчезающую очередь"
    assert out["metaphor_comments"] == ["не часы, покажи исчезающую очередь"]
    assert "cancelled" not in out


@pytest.mark.asyncio
async def test_metaphor_action_empty_comment_reinterrupts(decide):
    """Пустой комментарий — не перегенерация и не отмена; граф должен встать
    заново в hitl_image_upload (сигнал image_action_pending=True)."""
    decide({"action": "metaphor", "comment": "  "})
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert out.get("metaphor_comment") is None
    assert out.get("cancelled") is None or out.get("cancelled") is False
    # явный сигнал для роутера: нет артефакта → пауза снова
    assert out.get("image_action_pending") is True


@pytest.mark.asyncio
async def test_metaphor_comment_history_accumulates(decide):
    """История comments накапливается — новый append, не перезапись."""
    decide({"action": "metaphor", "comment": "второй"})
    out = await mod.hitl_image_upload(
        _state(metaphor_comments=["первый"])
    )  # type: ignore[arg-type]
    assert out["metaphor_comments"] == ["первый", "второй"]
    assert out["metaphor_comment"] == "второй"
