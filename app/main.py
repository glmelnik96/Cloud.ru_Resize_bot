"""FastAPI application factory + lifespan for App3 / creatives.

Phase 2: boots the auth skeleton + the task orchestrator. POST /api/tasks runs
the /new graph's first segment and parks at the first HITL stop (awaiting_persona).
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

        # In-memory queue is lost on restart → fail orphaned queued/running rows.
        # Parked (awaiting_*) tasks survive: their state is in the Redis checkpoint.
        from app.tasks.reconcile import reconcile_interrupted_tasks

        await reconcile_interrupted_tasks(Session)

        # Библиотека знаний: сид из vendored-файлов при первом старте + инжект
        # БД-каталога в граф (граф не импортирует app — снапшот проталкиваем сюда).
        try:
            from app.kb.store import refresh_catalog, seed_from_files

            await seed_from_files(Session)
            await refresh_catalog(Session)
        except Exception as exc:  # noqa: BLE001
            log.error("kb_catalog_init_failed: %s", exc)

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

        # Point the graph nodes' scratch dirs under our WorkingDirectory (no
        # /data on the VM). Must be set BEFORE init_graph imports the nodes,
        # which read these env vars at module load.
        import os

        os.environ.setdefault("RENDERS_DIR", str(cfg["renders_dir"]))
        os.environ.setdefault("ZIPS_DIR", str(cfg["zips_dir"]))
        os.environ.setdefault("HEROES_DIR", str(cfg["heroes_dir"]))

        # Compile the /new graph with the SQLite checkpointer (no Redis). If it
        # fails, the skeleton still boots (auth/me works); tasks return 503.
        cm = None
        graph = None
        try:
            from app.services.creatives import CreativesService, init_graph
            from app.services.hero_gen import make_hero_generator

            graph, cm = await init_graph(cfg["checkpoint_db"])

            # Hero generation backend, chosen by env (HERO_GEN_BACKEND):
            #   "app1" → delegate to App1's /internal/hero (loopback);
            #   "none" (default) → manual upload only, until App1's endpoint is live.
            hero_gen = make_hero_generator(
                backend=cfg["hero_gen_backend"], app1_hero_url=cfg["app1_hero_url"]
            )
            log.info("hero generation backend: %s", cfg["hero_gen_backend"])

            app.state.creatives = CreativesService(
                manager=manager, bus=bus, sessionmaker=Session, graph=graph,
                results_dir=cfg["results_dir"], hero_generator=hero_gen,
                max_open_per_user=cfg["user_queue_limit"],
                park_timeout_sec=cfg["park_timeout_sec"],
            )
            # The park timeout is an in-memory timer — restore it for tasks
            # this restart left parked, or they never close (see
            # CreativesService.rearm_parked_timeouts).
            expired = await app.state.creatives.rearm_parked_timeouts()
            log.info("app3 orchestrator ready (expired parked tasks: %d)", expired)
        except Exception as exc:  # noqa: BLE001
            app.state.creatives = None
            log.error("graph init failed (tasks disabled): %s", exc)

        # Webinar resizes (M4-web): LangGraph-free compose loop. Independent of
        # the graph stack on purpose — a checkpointer failure must not take the
        # webinar flow down with it.
        try:
            from infra.template_manifest import load_manifest

            from app.services.webinar import WebinarService

            repo_root = Path(__file__).resolve().parents[1]
            manifest_path = Path(
                cfg.get("webinar_manifest")
                or repo_root / "config" / "webinar_templates.json"
            )
            app.state.webinar = WebinarService(
                manager=manager, bus=bus, sessionmaker=Session,
                manifest=load_manifest(manifest_path), assets_root=repo_root,
                results_dir=cfg["results_dir"],
                max_open_per_user=cfg["user_queue_limit"],
                # Share the App1 hero generator: the visual (metaphor) variant
                # renders its hero from a prompt when no image is uploaded.
                hero_generator=locals().get("hero_gen"),
            )
            log.info("webinar service ready (%s)", manifest_path.name)
        except Exception as exc:  # noqa: BLE001
            app.state.webinar = None
            log.error("webinar init failed (webinar tasks disabled): %s", exc)

        # Background results retention (TTL cleanup of results/<uid>/ + rows).
        import asyncio

        from app.tasks.retention import retention_loop

        retention_task = asyncio.create_task(
            retention_loop(
                results_dir=Path(cfg["results_dir"]), sessionmaker=Session,
                ttl_sec=cfg["retention_ttl_sec"],
            ),
            name="retention-loop",
        )

        try:
            yield
        finally:
            retention_task.cancel()
            await manager.shutdown()
            if cm is not None:
                await cm.__aexit__(None, None, None)
            await engine.dispose()

    app = FastAPI(title="App3 — creatives", lifespan=lifespan)
    app.state.settings = cfg

    from app.api.routes_auth import router as auth_router
    from app.api.routes_pages import router as pages_router
    from app.api.routes_stream import router as stream_router
    from app.api.routes_tasks import results_router
    from app.api.routes_tasks import router as tasks_router
    from app.api.routes_webinar import router as webinar_router

    app.include_router(auth_router)
    app.include_router(tasks_router)
    app.include_router(webinar_router)
    app.include_router(stream_router)
    app.include_router(pages_router)
    app.include_router(results_router)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Result artifacts are served by the ownership-gated results_router (see
    # routes_tasks), NOT a StaticFiles mount — a bare mount is an IDOR leak.
    results_dir = Path(cfg["results_dir"])
    results_dir.mkdir(parents=True, exist_ok=True)
    app.state.results_dir = results_dir
    return app


def _resolve_settings(test_settings: dict | None) -> dict:
    base = {
        "prefix": settings.prefix,
        "dev_user": settings.dev_user,
        "db_url": settings.db_url,
        "results_dir": str(settings.results_dir),
        "tmp_root": str(settings.tmp_root),
        "renders_dir": str(settings.renders_dir),
        "zips_dir": str(settings.zips_dir),
        "heroes_dir": str(settings.heroes_dir),
        "checkpoint_db": settings.checkpoint_db,
        "hero_gen_backend": settings.hero_gen_backend,
        "app1_hero_url": settings.app1_hero_url,
        "max_concurrency": settings.max_concurrency,
        "max_per_user_inflight": settings.max_per_user_inflight,
        "user_queue_limit": settings.user_queue_limit,
        "park_timeout_sec": settings.park_timeout_sec,
        "retention_ttl_sec": settings.retention_ttl_sec,
    }
    if test_settings:
        base.update(test_settings)
    return base


app = create_app()
