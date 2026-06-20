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
    async def fake_init_graph(redis_url):
        if not graph_ok:
            raise RuntimeError("redis down")
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
