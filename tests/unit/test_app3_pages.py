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


def test_index_has_recent_tasks_panel(tmp_path, monkeypatch):
    """A finished run must stay reachable after a reload: the page carries a
    'Последние креативы' panel that the JS fills from GET /api/tasks."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert 'id="tasksPanel"' in html
        assert 'id="tasksList"' in html
        assert "Последние креативы" in html


def test_creatives_js_loads_recent_tasks(tmp_path, monkeypatch):
    """The served JS fetches the task list and renders the ZIP link so a
    completed creative is re-downloadable within the 24h retention window."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "loadRecentTasks" in js  # fetch + render recent tasks on load
        assert "tasksPanel" in js  # reveal the panel when there are tasks
        assert "result_url" in js  # done rows expose the ZIP download


def test_index_shows_retention_notice_from_config(tmp_path, monkeypatch):
    """The history panel tells the user how long runs are kept; the number is
    derived from RETENTION_TTL_SEC, not hard-coded."""
    with TestClient(_app(tmp_path, monkeypatch, retention_ttl_sec=7200)) as c:
        html = c.get("/", headers=_HDR).text
        assert "2 ч" in html  # 7200s // 3600 = 2 hours
        assert "удаляются" in html


def test_index_retention_notice_defaults_to_24h(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert "24 ч" in html


def test_creatives_js_renders_banner_grid(tmp_path, monkeypatch):
    """Finished task rows expand into a grid of their banner images with the
    original brief, per-thumb download, and a swipeable lightbox."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "images" in js  # consume the per-task image URLs
        assert "task-grid" in js  # the expandable grid container
        assert "brief" in js  # show the brief fields on expand
        assert "download" in js  # per-thumbnail download affordance
        assert "lightbox" in js  # gallery overlay to page through banners


def test_creatives_js_lightbox_navigation(tmp_path, monkeypatch):
    """The lightbox can page between banners (prev/next + keyboard)."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "ArrowRight" in js  # keyboard paging
        assert "ArrowLeft" in js


def test_creatives_js_rehydrates_active_task(tmp_path, monkeypatch):
    """Canon-header nav is a full reload; the JS must restore an in-flight run:
    persist the uid, look it up (localStorage / GET /api/tasks), snapshot via
    /pending, and reattach the EventSource (contract v3 §8)."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "localStorage" in js  # uid persisted across the reload
        assert "/api/tasks" in js  # discover the active task when storage is empty
        assert "/pending" in js  # snapshot the parked/running state before stream
        assert "rehydrate" in js  # run the restore on load


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
        assert 'href="/static/app.css?v=' in html
        assert 'src="/static/creatives.js?v=' in html
        assert 'window.APP_PREFIX = "";' in html


def test_webinar_page_renders(tmp_path, monkeypatch):
    """The webinar resizes page serves with canon nav (is-active on Вебинары)
    and loads its own fit-engine script."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/webinar", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        assert 'class="topnav__link is-active">Вебинары' in html
        assert "/creatives/static/webinar.js" in html
        assert 'id="fitCanvas"' in html


def test_webinar_page_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/webinar").status_code == 401
