"""Unit tests for graph.nodes.hitl_image_upload (M3.3).

We bypass the LangGraph interrupt machinery by monkey-patching the
``interrupt`` symbol imported into the module under test. That lets us
drive the node by simulating each resume-action without spinning up a
checkpointer.
"""

from __future__ import annotations

import pytest

from graph.nodes import hitl_image_upload as mod


def _state(**overrides):
    base = {
        "session_id": "sX",
        "image_prompt": "A serene render of a server with negative space on the left. No text, no letters, no logos.",
        "image_style": "render",
    }
    base.update(overrides)
    return base


def _patch_interrupt(monkeypatch, decision):
    def _fake_interrupt(payload):
        _fake_interrupt.last_payload = payload
        return decision

    _fake_interrupt.last_payload = None
    monkeypatch.setattr(mod, "interrupt", _fake_interrupt)
    return _fake_interrupt


@pytest.mark.asyncio
async def test_upload_action_populates_image(monkeypatch):
    fake = _patch_interrupt(
        monkeypatch,
        {
            "action": "upload",
            "local_path": "/data/heroes/abc.png",
            "style": "photo",
            "prompt": "P",
        },
    )
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert "image" in out
    img = out["image"]
    assert img["local_path"] == "/data/heroes/abc.png"
    assert img["style"] == "photo"  # decision style wins over state
    assert img["prompt"] == "P"
    assert img["variant"] == "default"
    # interrupt was called with the right kind/payload
    assert fake.last_payload["kind"] == "image_upload"
    assert fake.last_payload["image_style"] == "render"


@pytest.mark.asyncio
async def test_upload_action_falls_back_to_state_style_and_prompt(monkeypatch):
    _patch_interrupt(
        monkeypatch,
        {"action": "upload", "local_path": "/data/heroes/q.png"},
    )
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert out["image"]["style"] == "render"
    assert out["image"]["prompt"].startswith("A serene render")


@pytest.mark.asyncio
async def test_upload_action_without_local_path_cancels(monkeypatch):
    _patch_interrupt(monkeypatch, {"action": "upload"})
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert out["cancelled"] is True
    assert "error" in out


@pytest.mark.asyncio
async def test_upload_action_with_heroes_populates_generated_heroes(monkeypatch):
    """The generation path resumes with a list of 12 heroes (one per
    proposition) -> state.generated_heroes; no single image is set."""
    heroes = [
        {"local_path": f"/data/h{i}.png", "style": "render", "prompt": "p"}
        for i in range(12)
    ]
    _patch_interrupt(monkeypatch, {"action": "upload", "heroes": heroes})
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert "generated_heroes" in out
    assert len(out["generated_heroes"]) == 12
    assert "image" not in out


@pytest.mark.asyncio
async def test_cancel_action(monkeypatch):
    _patch_interrupt(monkeypatch, {"action": "cancel"})
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert out["cancelled"] is True
    assert out.get("image_action_pending") is False


@pytest.mark.asyncio
async def test_timeout_action(monkeypatch):
    _patch_interrupt(monkeypatch, {"action": "timeout"})
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert out["cancelled"] is True
    assert out["error"] == "image_upload_timeout"


@pytest.mark.asyncio
async def test_unknown_action_cancels(monkeypatch):
    _patch_interrupt(monkeypatch, {"action": "garbage"})
    out = await mod.hitl_image_upload(_state())  # type: ignore[arg-type]
    assert out["cancelled"] is True
    assert out.get("image_action_pending") is False


@pytest.mark.asyncio
async def test_missing_image_prompt_errors_without_interrupt(monkeypatch):
    """If state.image_prompt is missing, the node should NOT call interrupt;
    builder is supposed to keep generate_image_prompt immediately upstream."""
    called = {"flag": False}

    def _should_not_be_called(payload):
        called["flag"] = True
        return {"action": "upload"}

    monkeypatch.setattr(mod, "interrupt", _should_not_be_called)
    state = _state()
    state["image_prompt"] = ""
    out = await mod.hitl_image_upload(state)  # type: ignore[arg-type]
    assert "error" in out
    assert called["flag"] is False


@pytest.mark.asyncio
async def test_interrupt_payload_carries_metaphor_meta(monkeypatch):
    """Человек должен видеть, ЧТО за образ ему предлагают, иначе комментировать
    метафору нечем."""
    fake = _patch_interrupt(monkeypatch, {"action": "cancel"})
    await mod.hitl_image_upload(
        _state(  # type: ignore[arg-type]
            metaphor_meta=[
                {
                    "candidate_id": "c1",
                    "metaphor": "a bridge across a canyon",
                    "rationale": "переход от хаоса к порядку",
                    "intended_inference": "с продуктом путь становится коротким",
                    "anti_reading": "не должно читаться как стройка",
                }
            ]
        )
    )
    p = fake.last_payload
    assert p["metaphor"] == "a bridge across a canyon"
    assert p["intended_inference"] == "с продуктом путь становится коротким"
    assert p["anti_reading"] == "не должно читаться как стройка"


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [None, [], [None]])
async def test_interrupt_payload_without_metaphor_meta_is_empty_strings(monkeypatch, meta):
    """Ни одна форма «метафоры нет» не должна ронять остановку: экран просто
    теряет блок задумки и остаётся прежним."""
    fake = _patch_interrupt(monkeypatch, {"action": "cancel"})
    state = _state() if meta is None else _state(metaphor_meta=meta)
    await mod.hitl_image_upload(state)  # type: ignore[arg-type]
    p = fake.last_payload
    assert p["metaphor"] == "" and p["anti_reading"] == ""


@pytest.mark.asyncio
async def test_interrupt_payload_takes_the_first_metaphor(monkeypatch):
    """metaphor_meta[0] — метафора победителя; остальные к этой остановке
    отношения не имеют."""
    fake = _patch_interrupt(monkeypatch, {"action": "cancel"})
    await mod.hitl_image_upload(
        _state(  # type: ignore[arg-type]
            metaphor_meta=[{"metaphor": "первая"}, {"metaphor": "вторая"}]
        )
    )
    assert fake.last_payload["metaphor"] == "первая"
