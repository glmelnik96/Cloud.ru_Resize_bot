"""App3 — HTML page + static mount (canon v2 header)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "3", "X-User-Email": "gleb@cloud.ru"}


def _app(tmp_path, monkeypatch, **extra):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'p.db'}", **extra})


def test_index_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/").status_code == 401


def test_index_renders_canon_header(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        # canon nav: 3 sections, is-active on Креативы, brand split span
        assert 'href="/images"' in html
        assert 'href="/slides"' in html
        assert 'href="/creatives" class="topnav__link is-active"' in html
        assert "Cloud.ru <span>Design</span>" in html
        # user email + logout to gateway
        assert "gleb@cloud.ru" in html
        assert 'action="/logout"' in html
        # assets go through the gateway prefix
        assert "/creatives/static/app.css" in html
        assert "/creatives/static/creatives.js" in html


def test_static_css_served(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/static/app.css")
        assert r.status_code == 200
        assert "--accent:#26D07C" in r.text  # canon palette present


def test_static_font_served(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/static/fonts/sb-sans-text-400.woff2")
        assert r.status_code == 200
        assert r.content[:4] == b"wOF2"  # woff2 magic


# ── local-smoke dev affordances (no gateway) ────────────────────────


def test_dev_user_allows_access_without_gateway_header(tmp_path, monkeypatch):
    """APP3_DEV_USER set → the page renders without a gateway X-User-Id header."""
    with TestClient(_app(tmp_path, monkeypatch, dev_user="gleb@cloud.ru")) as c:
        r = c.get("/")  # no _HDR
        assert r.status_code == 200
        assert "gleb@cloud.ru" in r.text


def test_no_dev_user_still_401_without_header(tmp_path, monkeypatch):
    """Default (no dev user) → unauthenticated request is rejected as in prod."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/").status_code == 401


def test_empty_prefix_serves_assets_and_api_at_root(tmp_path, monkeypatch):
    """APP3_PREFIX='' → assets/API resolve at root (local run without gateway)."""
    with TestClient(_app(tmp_path, monkeypatch, prefix="")) as c:
        r = c.get("/", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        assert 'href="/static/app.css"' in html
        assert 'src="/static/creatives.js"' in html
        assert 'window.APP_PREFIX = "";' in html
