"""FastAPI application factory + lifespan for App3 / creatives.

Phase 2: boots the auth skeleton + the task orchestrator. POST /api/tasks runs
the /new graph's first segment and parks at awaiting_text (Redis checkpoint).
SSE + HITL decision endpoints + ZIP delivery land in later phases.

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
        Session = make_sessionmaker(engine)
        app.state.engine = engine
        app.state.sessionmaker = Session
        app.state.settings = cfg

        from app.tasks.events import EventBus
        from app.tasks.manager import TaskManager

        manager = TaskManager(
            tmp_root=Path(cfg["tmp_root"]),
            max_concurrency=cfg["max_concurrency"],
            max_per_user_inflight=cfg["max_per_user_inflight"],
            user_queue_limit=cfg["user_queue_limit"],
        )
        bus = EventBus()
        app.state.manager = manager
        app.state.bus = bus

        # Compile the /new graph with the Redis checkpointer. If Redis is down,
        # the skeleton still boots (auth/me works); task creation returns 503.
        cm = None
        graph = None
        try:
            from app.services.creatives import CreativesService, init_graph

            graph, cm = await init_graph(cfg["redis_url"])
            app.state.creatives = CreativesService(
                manager=manager, bus=bus, sessionmaker=Session, graph=graph,
                max_open_per_user=cfg["user_queue_limit"],
            )
            log.info("app3 orchestrator ready")
        except Exception as exc:  # noqa: BLE001
            app.state.creatives = None
            log.error("graph init failed (tasks disabled): %s", exc)

        try:
            yield
        finally:
            await manager.shutdown()
            if cm is not None:
                await cm.__aexit__(None, None, None)
            await engine.dispose()

    app = FastAPI(title="App3 — creatives", lifespan=lifespan)
    app.state.settings = cfg

    from app.api.routes_auth import router as auth_router
    from app.api.routes_stream import router as stream_router
    from app.api.routes_tasks import router as tasks_router

    app.include_router(auth_router)
    app.include_router(tasks_router)
    app.include_router(stream_router)

    results_dir = Path(cfg["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/results", StaticFiles(directory=str(results_dir)), name="results")
    return app


def _resolve_settings(test_settings: dict | None) -> dict:
    base = {
        "db_url": settings.db_url,
        "results_dir": str(settings.results_dir),
        "tmp_root": str(settings.tmp_root),
        "redis_url": settings.redis_url,
        "max_concurrency": settings.max_concurrency,
        "max_per_user_inflight": settings.max_per_user_inflight,
        "user_queue_limit": settings.user_queue_limit,
    }
    if test_settings:
        base.update(test_settings)
    return base


app = create_app()
