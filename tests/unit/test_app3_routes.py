"""App3 Phase 2 — task routes (hermetic: graph init stubbed, no Redis/LLM)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "5", "X-User-Email": "u@cloud.ru"}


def _app(tmp_path, monkeypatch, *, graph_ok: bool):
    async def fake_init_graph(checkpoint_db):
        if not graph_ok:
            raise RuntimeError("checkpointer init failed")
        return object(), None  # (graph, cm); cm=None so shutdown skips

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"})


def test_create_returns_503_when_graph_down(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, graph_ok=False)) as c:
        r = c.post("/api/tasks", json={"product": "p", "goal": "g", "audience": "a"}, headers=_HDR)
        assert r.status_code == 503


def test_create_returns_uid_with_stub_service(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        # Replace the real orchestrator with a stub (avoids running the graph).
        class _Stub:
            async def create(self, user_id, fields):
                self.seen = (user_id, fields)
                return "deadbeef0001"

        app.state.creatives = _Stub()
        r = c.post("/api/tasks", json={"product": "p", "goal": "g", "audience": "a"}, headers=_HDR)
        assert r.status_code == 200
        assert r.json()["task_uid"] == "deadbeef0001"


def test_create_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, graph_ok=True)) as c:
        r = c.post("/api/tasks", json={"product": "p", "goal": "g", "audience": "a"})
        assert r.status_code == 401


def test_list_and_get_task_isolation(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        # empty list for a fresh user
        r = c.get("/api/tasks", headers=_HDR)
        assert r.status_code == 200 and r.json() == []
        # unknown uid → 404
        r2 = c.get("/api/tasks/nope", headers=_HDR)
        assert r2.status_code == 404


def _seed_task(db_path, uid, status, user_id):
    """Insert a task row via a separate async engine to the same sqlite file
    (the TestClient runs the app loop in a worker thread, so we can't reuse
    its sessionmaker from the test thread)."""
    import asyncio

    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import models

    async def _ins():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            async with async_sessionmaker(eng)() as s:
                await s.execute(
                    insert(models.Task).values(
                        task_uid=uid, user_id=user_id, workflow="creatives", status=status
                    )
                )
                await s.commit()
        finally:
            await eng.dispose()

    asyncio.run(_ins())


def test_decision_text_409_when_not_awaiting(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()  # ensures user row exists

        class _Stub:
            async def submit_decision(self, *a, **k):
                self.called = True

        app.state.creatives = _Stub()
        _seed_task(db, "rt", "running", me["id"])
        r = c.post("/api/tasks/rt/decision/text", json={"action": "approve"}, headers=_HDR)
        assert r.status_code == 409


def test_decision_text_accepts_when_awaiting(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        seen = {}

        class _Stub:
            async def submit_decision(self, uid, user_id, decision):
                seen["uid"] = uid
                seen["decision"] = decision

        app.state.creatives = _Stub()
        _seed_task(db, "at", "awaiting_text", me["id"])
        r = c.post(
            "/api/tasks/at/decision/text",
            json={"action": "refine", "comment": "короче"},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert seen["uid"] == "at"
        assert seen["decision"] == {"action": "refine", "comment": "короче"}


def test_decision_text_rejects_bad_action(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        c.get("/api/me", headers=_HDR)
        r = c.post("/api/tasks/x/decision/text", json={"action": "nope"}, headers=_HDR)
        assert r.status_code == 422  # pydantic Literal rejects unknown action


def test_decision_image_upload_saves_and_resumes(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        seen = {}

        class _Stub:
            async def submit_decision(self, uid, user_id, decision):
                seen["decision"] = decision

        app.state.creatives = _Stub()
        _seed_task(db, "im", "awaiting_image", me["id"])
        r = c.post(
            "/api/tasks/im/decision/image",
            data={"action": "upload"},
            files={"file": ("hero.png", b"\x89PNG", "image/png")},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert seen["decision"]["action"] == "upload"
        assert seen["decision"]["local_path"].endswith("hero.png")


def test_decision_image_cancel(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        seen = {}

        class _Stub:
            async def submit_decision(self, uid, user_id, decision):
                seen["decision"] = decision

        app.state.creatives = _Stub()
        _seed_task(db, "ic", "awaiting_image", me["id"])
        r = c.post("/api/tasks/ic/decision/image", data={"action": "cancel"}, headers=_HDR)
        assert r.status_code == 200
        assert seen["decision"] == {"action": "cancel"}


def test_decision_image_409_when_not_awaiting_image(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        app.state.creatives = type("S", (), {"submit_decision": None})()
        _seed_task(db, "iw", "awaiting_text", me["id"])
        r = c.post("/api/tasks/iw/decision/image", data={"action": "cancel"}, headers=_HDR)
        assert r.status_code == 409


def test_decision_image_generate_501_when_no_backend(tmp_path, monkeypatch):
    """With no Phygital session file, the real service has a Null generator →
    generate returns 501 and the UI falls back to manual upload."""
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        # use the REAL service built in lifespan (Null hero generator)
        _seed_task(db, "ig", "awaiting_image", me["id"])
        r = c.post("/api/tasks/ig/decision/image", data={"action": "generate"}, headers=_HDR)
        assert r.status_code == 501
