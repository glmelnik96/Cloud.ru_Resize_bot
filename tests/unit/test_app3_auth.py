"""App3 web skeleton — gateway-header auth contract.

The sub-app trusts identity ONLY from the gateway-injected X-User-Id /
X-User-Email headers. No header → 401. Same gateway_user_id reuses the row.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from starlette.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


def _client(tmp_path):
    app = create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'app3.db'}"})
    return TestClient(app)


def test_me_requires_gateway_header(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/me")
        assert r.status_code == 401


def test_me_upserts_user_from_header(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/me", headers={"X-User-Id": "7", "X-User-Email": "a@cloud.ru"})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == "a@cloud.ru"
        assert isinstance(body["id"], int)


def test_same_gateway_id_reuses_row_and_updates_email(tmp_path):
    with _client(tmp_path) as c:
        r1 = c.get("/api/me", headers={"X-User-Id": "9", "X-User-Email": "x@cloud.ru"})
        r2 = c.get("/api/me", headers={"X-User-Id": "9", "X-User-Email": "y@cloud.ru"})
        assert r1.json()["id"] == r2.json()["id"]  # same DB row
        assert r2.json()["email"] == "y@cloud.ru"  # email refreshed on revisit


def test_results_mount_exists(tmp_path):
    app = create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'app3.db'}"})
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/api/me" in paths
    assert "/results" in paths
