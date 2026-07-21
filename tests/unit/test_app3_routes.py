"""App3 Phase 2 — task routes (hermetic: graph init stubbed, no Redis/LLM)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "5", "X-User-Email": "u@cloud.ru"}


def _png_bytes() -> bytes:
    """A minimal but genuinely-decodable PNG (upload routes now validate images)."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (2, 2), (0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _app(tmp_path, monkeypatch, *, graph_ok: bool):
    async def fake_init_graph(checkpoint_db):
        if not graph_ok:
            raise RuntimeError("checkpointer init failed")
        return object(), None  # (graph, cm); cm=None so shutdown skips

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"})


def test_create_returns_503_when_graph_down(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, graph_ok=False)) as c:
        r = c.post("/api/tasks", json={"product": "p", "audience": "a", "emotion": "e"}, headers=_HDR)
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
        r = c.post("/api/tasks", json={"product": "p", "audience": "a", "emotion": "e"}, headers=_HDR)
        assert r.status_code == 200
        assert r.json()["task_uid"] == "deadbeef0001"


def test_create_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, graph_ok=True)) as c:
        r = c.post("/api/tasks", json={"product": "p", "audience": "a", "emotion": "e"})
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


def _seed_task(db_path, uid, status, user_id, **extra):
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
                        task_uid=uid, user_id=user_id, workflow="creatives",
                        status=status, **extra,
                    )
                )
                await s.commit()
        finally:
            await eng.dispose()

    asyncio.run(_ins())


def test_list_tasks_includes_prompt_and_result(tmp_path, monkeypatch):
    """The recent-creatives list needs a human label (prompt) and the ZIP link
    so a finished run is re-downloadable after a reload."""
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(
            db, "done1", "done", me["id"],
            prompt="Cloud.ru Evolution",
            result_url="/results/done1/done1.zip",
        )
        rows = c.get("/api/tasks", headers=_HDR).json()
        assert len(rows) == 1
        assert rows[0]["prompt"] == "Cloud.ru Evolution"
        assert rows[0]["result_url"] == "/results/done1/done1.zip"


def _app_with_results(tmp_path, monkeypatch, results_dir):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app(
        {
            "db_url": f"sqlite+aiosqlite:///{tmp_path / 'r.db'}",
            "results_dir": str(results_dir),
        }
    )


def test_list_tasks_includes_banner_images(tmp_path, monkeypatch):
    """A finished run exposes its individual banner PNGs (sorted, ZIP excluded)
    so the history grid can render them without a second request."""
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        d = results / "done1"
        d.mkdir(parents=True)
        # intentionally out of order on disk; zero-padded names sort correctly
        for name in ["02_render.png", "01_photo.png", "03_photo.png"]:
            (d / name).write_bytes(b"\x89PNG")
        (d / "done1.zip").write_bytes(b"PK\x03\x04")  # ZIP must NOT appear
        _seed_task(db, "done1", "done", me["id"], result_url="/results/done1/done1.zip")
        rows = c.get("/api/tasks", headers=_HDR).json()
        assert rows[0]["images"] == [
            "/results/done1/01_photo.png",
            "/results/done1/02_render.png",
            "/results/done1/03_photo.png",
        ]


def test_results_file_gated_by_ownership(tmp_path, monkeypatch):
    """Result artifacts are served only to the owning user (was an open
    StaticFiles mount → any user could read another's ZIP by uid). Non-owners
    get 404; a traversal filename cannot escape the task's results dir."""
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    other = {"X-User-Id": "9", "X-User-Email": "b@cloud.ru"}
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        c.get("/api/me", headers=other)  # materialize the second user
        d = results / "own1"
        d.mkdir(parents=True)
        (d / "own1.zip").write_bytes(b"PK\x03\x04")
        _seed_task(db, "own1", "done", me["id"], result_url="/results/own1/own1.zip")
        # owner gets the bytes
        r = c.get("/results/own1/own1.zip", headers=_HDR)
        assert r.status_code == 200 and r.content == b"PK\x03\x04"
        # a different authenticated user cannot read it
        assert c.get("/results/own1/own1.zip", headers=other).status_code == 404
        # traversal is refused (stays within the task's results dir)
        assert c.get("/results/own1/..%2f..%2fr.db", headers=_HDR).status_code == 404


def test_list_tasks_exposes_brief_fields(tmp_path, monkeypatch):
    """Expanding a history row shows what the user originally briefed, so the
    brief (product/audience/emotion) travels with the task list."""
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(
            db, "brief1", "done", me["id"],
            params={"product": "Cloud.ru Evolution", "audience": "DevOps", "emotion": "контроль"},
        )
        rows = c.get("/api/tasks", headers=_HDR).json()
        assert rows[0]["brief"] == {
            "product": "Cloud.ru Evolution",
            "audience": "DevOps",
            "emotion": "контроль",
        }


def test_list_tasks_brief_whitelists_known_keys(tmp_path, monkeypatch):
    """Only the three brief fields are surfaced — any other internal params
    must not leak into the response."""
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(
            db, "brief2", "done", me["id"],
            params={"product": "p", "audience": "a", "emotion": "e", "secret": "x"},
        )
        rows = c.get("/api/tasks", headers=_HDR).json()
        assert set(rows[0]["brief"].keys()) == {"product", "audience", "emotion"}


def test_list_tasks_exposes_banner_cards(tmp_path, monkeypatch):
    """Block 3 (2026-07-10): the extended per-banner text (slogan/cta/hook/
    reason/body + scenario) must survive to the final result — the UI draws a
    caption under each banner. Cards travel in params['cards'], whitelisted."""
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(
            db, "cards1", "done", me["id"],
            params={
                "product": "p",
                "cards": [
                    {
                        "slogan": "Инфра без сюрпризов", "body": "идея",
                        "cta": "Начать", "hook_angle": "rational",
                        "score": 9.5, "reason": "почему зайдёт",
                        "scenario": "render",
                        "internal_leak": "x",  # must be dropped
                    }
                ],
            },
        )
        rows = c.get("/api/tasks", headers=_HDR).json()
        cards = rows[0]["cards"]
        assert len(cards) == 1
        assert cards[0]["slogan"] == "Инфра без сюрпризов"
        assert cards[0]["scenario"] == "render"
        assert cards[0]["reason"] == "почему зайдёт"
        assert "internal_leak" not in cards[0]


def test_list_tasks_cards_empty_when_absent(tmp_path, monkeypatch):
    """Old tasks (pre-Block-3) have no cards in params → empty list, no crash."""
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "old1", "done", me["id"], params={"product": "p"})
        rows = c.get("/api/tasks", headers=_HDR).json()
        assert rows[0]["cards"] == []


def test_list_tasks_images_empty_for_non_done(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "run1", "running", me["id"])
        rows = c.get("/api/tasks", headers=_HDR).json()
        assert rows[0]["images"] == []


def test_list_tasks_images_empty_after_files_purged(tmp_path, monkeypatch):
    """Files are derived from disk, so after 24h retention purge the grid is
    empty rather than showing broken links."""
    db = tmp_path / "r.db"
    results = tmp_path / "results"
    app = _app_with_results(tmp_path, monkeypatch, results)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "gone1", "done", me["id"], result_url="/results/gone1/gone1.zip")
        rows = c.get("/api/tasks", headers=_HDR).json()
        assert rows[0]["images"] == []


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
            json={"action": "regenerate"},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert seen["uid"] == "at"
        assert seen["decision"] == {"action": "regenerate"}


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
            files={"file": ("hero.png", _png_bytes(), "image/png")},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert seen["decision"]["action"] == "upload"
        assert seen["decision"]["local_path"].endswith("hero.png")


def test_decision_image_upload_sanitizes_hostile_filename(tmp_path, monkeypatch):
    """Client filename must never influence the stored path: fixed stem 'hero',
    extension whitelisted (non-image ext falls back to .png), no traversal."""
    from pathlib import Path as _P

    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        seen = {}

        class _Stub:
            async def submit_decision(self, uid, user_id, decision):
                seen["decision"] = decision

        app.state.creatives = _Stub()
        _seed_task(db, "ih", "awaiting_image", me["id"])
        r = c.post(
            "/api/tasks/ih/decision/image",
            data={"action": "upload"},
            files={"file": ("..\\..\\evil.exe", _png_bytes(), "image/png")},
            headers=_HDR,
        )
        assert r.status_code == 200
        saved = _P(seen["decision"]["local_path"])
        assert saved.name == "hero.png"  # stem fixed, .exe rejected -> .png
        # stays inside the manager's tmp root for this task
        assert str(saved.resolve()).startswith(str(_P(app.state.manager.tmp_root).resolve()))


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
