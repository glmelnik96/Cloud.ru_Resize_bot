"""App3 M4-web — webinar routes (hermetic: graph init stubbed, no LLM)."""
from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from PIL import Image  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "9", "X-User-Email": "u@cloud.ru"}


def _app(tmp_path, monkeypatch):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"})


def _hero_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (80, 80), (200, 30, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_webinar_meta_exposes_frame_geometry(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/api/webinar/meta", headers=_HDR)
        assert r.status_code == 200
        meta = r.json()
        assert meta["speaker"]["frame"] == [1494, 2669]
        assert meta["visual"]["frame"] == [1024, 1024]
        assert meta["visual"]["box"] == [211, 157, 813, 887]
        # fit flag drives the canvas: speaker hand-fits, visual auto-fits
        assert meta["speaker"]["fit"] is True
        assert meta["visual"]["fit"] is False


def test_webinar_meta_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/api/webinar/meta").status_code == 401


def test_create_webinar_bad_variant_422(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.post(
            "/api/webinar/tasks",
            data={"variant": "banner", "title": "T"},
            files={"file": ("h.png", _hero_png(), "image/png")},
            headers=_HDR,
        )
        assert r.status_code == 422


def test_create_webinar_partial_fit_422(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.post(
            "/api/webinar/tasks",
            data={"variant": "speaker", "title": "T", "fit_scale": "1.0"},
            files={"file": ("h.png", _hero_png(), "image/png")},
            headers=_HDR,
        )
        assert r.status_code == 422


def test_create_webinar_returns_uid_with_stub(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as c:
        seen = {}

        class _Stub:
            async def create(self, user_id, fields, *, hero_bytes, transform):
                seen["fields"] = fields
                seen["transform"] = transform
                seen["hero_len"] = len(hero_bytes)
                return "webinar000001"

        app.state.webinar = _Stub()
        r = c.post(
            "/api/webinar/tasks",
            data={
                "variant": "speaker", "title": "Т", "date": "08 сентября",
                "time": "11:00", "fit_scale": "1.5", "fit_x": "10", "fit_y": "-20",
            },
            files={"file": ("h.png", _hero_png(), "image/png")},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert r.json()["task_uid"] == "webinar000001"
        assert seen["fields"]["variant"] == "speaker"
        assert seen["transform"].scale == 1.5
        assert seen["transform"].x == 10 and seen["transform"].y == -20
        assert seen["hero_len"] > 0


def test_create_webinar_503_when_service_down(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as c:
        app.state.webinar = None
        r = c.post(
            "/api/webinar/tasks",
            data={"variant": "speaker", "title": "T"},
            files={"file": ("h.png", _hero_png(), "image/png")},
            headers=_HDR,
        )
        assert r.status_code == 503
