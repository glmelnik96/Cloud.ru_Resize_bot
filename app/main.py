"""FastAPI application factory + lifespan for App3 / creatives.

Phase 1: boots, authenticates via the gateway header, owns its DB, and mounts
/results. The task orchestrator (graph driver), queue, SSE event bus and the
HITL endpoints are added in later phases.

Routes live at the root ("/api/...", "/results/...") because the gateway
strips the "/creatives" prefix when proxying.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db.database import init_db, make_engine, make_sessionmaker

log = logging.getLogger("app3")


def create_app(test_settings: dict | None = None) -> FastAPI:
    cfg = _resolve_settings(test_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(cfg["db_url"])
        await init_db(engine)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.settings = cfg
        log.info("app3 lifespan up (db=%s)", cfg["db_url"])
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="App3 — creatives", lifespan=lifespan)
    app.state.settings = cfg

    from app.api.routes_auth import router as auth_router

    app.include_router(auth_router)

    results_dir = Path(cfg["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/results", StaticFiles(directory=str(results_dir)), name="results")
    return app


def _resolve_settings(test_settings: dict | None) -> dict:
    base = {
        "db_url": settings.db_url,
        "results_dir": str(settings.results_dir),
        "redis_url": settings.redis_url,
        "max_concurrency": settings.max_concurrency,
        "max_per_user_inflight": settings.max_per_user_inflight,
        "user_queue_limit": settings.user_queue_limit,
    }
    if test_settings:
        base.update(test_settings)
    return base


app = create_app()
