"""app.services.webinar (M4-web, 2026-07-21): LangGraph-free webinar resizes.

The webinar flow has no LLM and no HITL graph: the user fills a form (variant
+ title/subtitle/date/time), uploads ONE hero (speaker photo or App1 metaphor
render) and optionally fits it manually in the web UI (infra.hero_fit
Transform). The service bakes the hero into the variant's reference frame and
runs a plain compose loop over the scenario formats, then zips the PNGs.

Contract:
  1. bake_hero(hero, variant, transform=None): speaker → SPEAKER_FRAME window,
     default cover/top (the window IS the visible-object rect of the Figma
     reference); visual → VISUAL_FRAME 1024 square, default contain into
     VISUAL_BOX. An explicit Transform is honored verbatim. Unknown variant
     raises ValueError.
  2. render_webinar(manifest, variant, hero, texts, ...) renders each scenario
     format via compose(hero_prefit=True), writes <slug>.png into out_dir and
     a webinar_<variant>.zip with all PNGs; `formats` narrows the list.
  3. WebinarService.create persists a Task (workflow="webinar"), renders in
     the manager lane and finishes with result_url → the ZIP under results/.
"""
from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from infra.hero_fit import SPEAKER_FRAME, VISUAL_BOX, VISUAL_FRAME, Transform
from infra.template_manifest import load_manifest

from app.services.webinar import (
    bake_hero,
    prepare_hero,
    render_webinar,
    speaker_subtitle,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── speaker_subtitle: name + position → the 2-line subtitle box ────


def test_speaker_subtitle_joins_name_and_position_two_lines():
    # Figma renders name + position as two lines in one subtitle box
    # (e.g. "Михаил Безобразов,\nархитектор решений").
    assert (
        speaker_subtitle("Михаил Безобразов", "архитектор решений")
        == "Михаил Безобразов,\nархитектор решений"
    )


def test_speaker_subtitle_single_field_no_dangling_comma():
    assert speaker_subtitle("Михаил Безобразов", "") == "Михаил Безобразов"
    assert speaker_subtitle("", "архитектор решений") == "архитектор решений"
    assert speaker_subtitle("  ", "  ") == ""


def _opaque_hero(w: int = 100, h: int = 100) -> Image.Image:
    return Image.new("RGBA", (w, h), (200, 30, 30, 255))


# ── bake_hero ─────────────────────────────────────────────────────


def test_bake_hero_speaker_default_covers_frame_top():
    out = bake_hero(_opaque_hero(), "speaker")
    assert out.size == SPEAKER_FRAME
    # cover: a square object over the tall window fills it completely
    for x, y in ((5, 5), (1488, 5), (747, 1334), (5, 2663), (1488, 2663)):
        assert out.getpixel((x, y))[3] == 255


def test_bake_hero_speaker_manual_transform_verbatim():
    out = bake_hero(_opaque_hero(), "speaker", Transform(scale=1.0, x=0, y=0))
    assert out.size == SPEAKER_FRAME
    assert out.getpixel((50, 50))[3] == 255
    assert out.getpixel((500, 500))[3] == 0


def test_bake_hero_visual_default_contains_in_box():
    out = bake_hero(_opaque_hero(), "visual")
    assert out.size == VISUAL_FRAME
    bx0, by0, bx1, by1 = VISUAL_BOX
    cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2
    assert out.getpixel((cx, cy))[3] == 255
    # outside the placement box the frame stays transparent
    assert out.getpixel((bx0 - 60, cy))[3] == 0
    assert out.getpixel((cx, by0 - 60))[3] == 0


def test_prepare_hero_speaker_bakes_and_prefits():
    img, prefit = prepare_hero(_opaque_hero(400, 600), "speaker")
    assert prefit is True
    assert img.size == SPEAKER_FRAME  # baked into the reference frame


def test_prepare_hero_visual_passes_through_without_prefit():
    # Variant A: the generated full-bleed metaphor is NOT baked into a sub-box;
    # it flows to the per-format fit:crop/stretch verbatim (hero_prefit=False).
    src = _opaque_hero(400, 400)
    img, prefit = prepare_hero(src, "visual")
    assert prefit is False
    assert img.size == src.size  # untouched, not the 1024 VISUAL_FRAME square


def test_prepare_hero_unknown_variant_raises():
    with pytest.raises(ValueError):
        prepare_hero(_opaque_hero(), "banner")


def test_bake_hero_unknown_variant_raises():
    with pytest.raises(ValueError):
        bake_hero(_opaque_hero(), "banner")


# ── render_webinar ────────────────────────────────────────────────


def test_render_webinar_speaker_writes_pngs_and_zip(tmp_path):
    manifest = load_manifest(REPO_ROOT / "config" / "webinar_templates.json")
    texts = {"title": "Т", "subtitle": "С", "date": "08 сентября", "time": "11:00"}
    files, zip_path = render_webinar(
        manifest,
        "speaker",
        _opaque_hero(400, 600),
        texts,
        assets_root=REPO_ROOT,
        out_dir=tmp_path,
        formats=["webinar_600x600_speaker", "webinar_1080x1080_speaker"],
    )
    assert [f.name for f in files] == [
        "webinar_600x600_speaker.png",
        "webinar_1080x1080_speaker.png",
    ]
    assert Image.open(files[0]).size == (600, 600)
    assert Image.open(files[1]).size == (1080, 1080)
    assert zip_path.name == "webinar_speaker.zip"
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == [
            "webinar_1080x1080_speaker.png",
            "webinar_600x600_speaker.png",
        ]


def test_render_webinar_visual_uses_crop_math(tmp_path):
    manifest = load_manifest(REPO_ROOT / "config" / "webinar_templates.json")
    texts = {"title": "Т", "date": "08 сентября", "time": "11:00"}
    files, zip_path = render_webinar(
        manifest,
        "visual",
        _opaque_hero(400, 400),
        texts,
        assets_root=REPO_ROOT,
        out_dir=tmp_path,
        formats=["webinar_240x240_email_podborki_visual"],
    )
    assert Image.open(files[0]).size == (240, 240)
    assert zip_path.name == "webinar_visual.zip"


def test_render_webinar_defaults_to_scenario_formats(tmp_path):
    """formats=None → the manifest scenario drives the list (tiny manifest so
    the test doesn't render all 26 production formats)."""
    mini = {
        "version": "1",
        "templates": {
            "wtest_100x100_speaker": {
                "width": 100,
                "height": 100,
                "background_color": "#26D07C",
                "layers": [
                    {
                        "type": "hero_cutout",
                        "x": 0, "y": 0, "width": 100, "height": 100,
                        "fit": "cover", "anchor_h": "center", "anchor_v": "top",
                        "allow_upscale": True, "z": 0,
                    }
                ],
            }
        },
        "scenarios": {
            "webinar_speaker": {"title": "t", "formats": ["wtest_100x100_speaker"]}
        },
    }
    mpath = tmp_path / "mini.json"
    mpath.write_text(json.dumps(mini), encoding="utf-8")
    manifest = load_manifest(mpath)
    files, zip_path = render_webinar(
        manifest, "speaker", _opaque_hero(), {}, assets_root=REPO_ROOT,
        out_dir=tmp_path / "out",
    )
    assert [f.name for f in files] == ["wtest_100x100_speaker.png"]
    # prefit cover: the baked SPEAKER_FRAME window fills the 100x100 rect
    assert Image.open(files[0]).getpixel((50, 50))[:3] == (200, 30, 30)


# ── WebinarService end-to-end (hermetic: tiny manifest, sqlite) ──


@pytest.fixture()
def mini_manifest(tmp_path):
    mini = {
        "version": "1",
        "templates": {
            "wtest_100x100_speaker": {
                "width": 100,
                "height": 100,
                "background_color": "#26D07C",
                "layers": [
                    {
                        "type": "hero_cutout",
                        "x": 0, "y": 0, "width": 100, "height": 100,
                        "fit": "cover", "anchor_h": "center", "anchor_v": "top",
                        "allow_upscale": True, "z": 0,
                    }
                ],
            }
        },
        "scenarios": {
            "webinar_speaker": {"title": "t", "formats": ["wtest_100x100_speaker"]}
        },
    }
    mpath = tmp_path / "mini.json"
    mpath.write_text(json.dumps(mini), encoding="utf-8")
    return load_manifest(mpath)


def test_webinar_service_create_renders_to_done(tmp_path, mini_manifest):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import select

    from app.db import models
    from app.db.database import init_db, make_engine, make_sessionmaker
    from app.services.webinar import WebinarService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    async def _run():
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
        await init_db(engine)
        Session = make_sessionmaker(engine)
        async with Session() as s:
            s.add(models.User(gateway_user_id="7", yandex_id="y7", email="u@cloud.ru"))
            await s.commit()
            uid = (await s.execute(select(models.User))).scalar_one().id

        manager = TaskManager(tmp_root=tmp_path / "tmp")
        service = WebinarService(
            manager=manager,
            bus=EventBus(),
            sessionmaker=Session,
            manifest=mini_manifest,
            assets_root=REPO_ROOT,
            results_dir=tmp_path / "results",
        )
        import io

        buf = io.BytesIO()
        _opaque_hero().save(buf, format="PNG")
        task_uid = await service.create(
            str(uid),
            {"variant": "speaker", "title": "Т", "date": "08 сентября", "time": "11:00"},
            hero_bytes=buf.getvalue(),
        )

        for _ in range(100):
            async with Session() as s:
                res = await s.execute(
                    select(models.Task).where(models.Task.task_uid == task_uid)
                )
                task = res.scalar_one()
                if task.status in ("done", "failed"):
                    break
            await asyncio.sleep(0.05)
        assert task.status == "done", task.error
        assert task.workflow == "webinar"
        assert task.result_url == f"/results/{task_uid}/webinar_speaker.zip"
        assert (tmp_path / "results" / task_uid / "webinar_speaker.zip").exists()
        assert (tmp_path / "results" / task_uid / "wtest_100x100_speaker.png").exists()
        # one-shot: the uploaded hero tmp dir is cleared once the render finishes
        assert not (tmp_path / "tmp" / str(uid) / task_uid).exists()
        await manager.shutdown()
        await engine.dispose()

    asyncio.run(_run())


def test_webinar_service_create_joins_name_position_into_subtitle(
    tmp_path, mini_manifest
):
    """The form sends name + position separately; the service folds them into
    the single 2-line ``subtitle`` text the pixel-closed templates expect."""
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import select

    from app.db import models
    from app.db.database import init_db, make_engine, make_sessionmaker
    from app.services.webinar import WebinarService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    async def _run():
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
        await init_db(engine)
        Session = make_sessionmaker(engine)
        async with Session() as s:
            s.add(models.User(gateway_user_id="9", yandex_id="y9", email="u@cloud.ru"))
            await s.commit()
            uid = (await s.execute(select(models.User))).scalar_one().id

        manager = TaskManager(tmp_root=tmp_path / "tmp")
        service = WebinarService(
            manager=manager,
            bus=EventBus(),
            sessionmaker=Session,
            manifest=mini_manifest,
            assets_root=REPO_ROOT,
            results_dir=tmp_path / "results",
        )
        import io

        buf = io.BytesIO()
        _opaque_hero().save(buf, format="PNG")
        task_uid = await service.create(
            str(uid),
            {
                "variant": "speaker",
                "title": "Т",
                "name": "Михаил Безобразов",
                "position": "архитектор решений",
            },
            hero_bytes=buf.getvalue(),
        )

        async with Session() as s:
            res = await s.execute(
                select(models.Task).where(models.Task.task_uid == task_uid)
            )
            task = res.scalar_one()
        assert task.params["subtitle"] == "Михаил Безобразов,\nархитектор решений"
        # raw name/position are not leaked as separate text slots
        assert "name" not in task.params and "position" not in task.params
        await manager.shutdown()
        await engine.dispose()

    asyncio.run(_run())


class _FakeHeroGen:
    """Records the prompt and writes a valid opaque PNG to dest."""

    available = True

    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, *, prompt, style, dest, user_id=None, email=None):
        self.calls.append(
            {"prompt": prompt, "style": style, "user_id": user_id}
        )
        import io

        buf = io.BytesIO()
        _opaque_hero(64, 64).save(buf, format="PNG")
        Path(dest).write_bytes(buf.getvalue())
        return Path(dest)


@pytest.fixture()
def mini_visual_manifest(tmp_path):
    mini = {
        "version": "1",
        "templates": {
            "wtest_100x100_visual": {
                "width": 100,
                "height": 100,
                "background_color": "#26D07C",
                "layers": [
                    {
                        "type": "hero_cutout",
                        "x": 0, "y": 0, "width": 100, "height": 100,
                        "fit": "cover", "anchor_h": "center", "anchor_v": "top",
                        "allow_upscale": True, "z": 0,
                    }
                ],
            }
        },
        "scenarios": {
            "webinar_visual": {"title": "t", "formats": ["wtest_100x100_visual"]}
        },
    }
    mpath = tmp_path / "mini_visual.json"
    mpath.write_text(json.dumps(mini), encoding="utf-8")
    return load_manifest(mpath)


def test_webinar_service_visual_generates_metaphor_from_prompt(
    tmp_path, mini_visual_manifest
):
    """Visual variant with no upload generates the metaphor via the App1
    hero generator from a prompt, then renders + zips like any other run."""
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import select

    from app.db import models
    from app.db.database import init_db, make_engine, make_sessionmaker
    from app.services.webinar import WebinarService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    async def _run():
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
        await init_db(engine)
        Session = make_sessionmaker(engine)
        async with Session() as s:
            s.add(models.User(gateway_user_id="8", yandex_id="y8", email="u@cloud.ru"))
            await s.commit()
            uid = (await s.execute(select(models.User))).scalar_one().id

        manager = TaskManager(tmp_root=tmp_path / "tmp")
        gen = _FakeHeroGen()
        service = WebinarService(
            manager=manager,
            bus=EventBus(),
            sessionmaker=Session,
            manifest=mini_visual_manifest,
            assets_root=REPO_ROOT,
            results_dir=tmp_path / "results",
            hero_generator=gen,
        )
        task_uid = await service.create(
            str(uid),
            {"variant": "visual", "title": "Т"},
            hero_bytes=None,
            prompt="облачная метафора: парящий сервер над облаками",
        )

        for _ in range(100):
            async with Session() as s:
                res = await s.execute(
                    select(models.Task).where(models.Task.task_uid == task_uid)
                )
                task = res.scalar_one()
                if task.status in ("done", "failed"):
                    break
            await asyncio.sleep(0.05)
        assert task.status == "done", task.error
        assert (tmp_path / "results" / task_uid / "webinar_visual.zip").exists()
        assert gen.calls and gen.calls[0]["prompt"].startswith("облачная метафора")
        assert gen.calls[0]["user_id"] == str(uid)
        await manager.shutdown()
        await engine.dispose()

    asyncio.run(_run())


def test_webinar_service_visual_requires_hero_or_prompt(
    tmp_path, mini_visual_manifest
):
    pytest.importorskip("sqlalchemy")
    from app.db.database import init_db, make_engine, make_sessionmaker
    from app.services.webinar import WebinarService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    async def _run():
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
        await init_db(engine)
        Session = make_sessionmaker(engine)
        manager = TaskManager(tmp_root=tmp_path / "tmp")
        service = WebinarService(
            manager=manager,
            bus=EventBus(),
            sessionmaker=Session,
            manifest=mini_visual_manifest,
            assets_root=REPO_ROOT,
            results_dir=tmp_path / "results",
            hero_generator=_FakeHeroGen(),
        )
        # neither an upload nor a prompt → rejected before queueing
        with pytest.raises(ValueError):
            await service.create(
                "1", {"variant": "visual", "title": "t"}, hero_bytes=None, prompt=""
            )
        await manager.shutdown()
        await engine.dispose()

    asyncio.run(_run())


def test_webinar_service_bad_variant_fails_before_queue(tmp_path, mini_manifest):
    pytest.importorskip("sqlalchemy")
    from app.db.database import init_db, make_engine, make_sessionmaker
    from app.services.webinar import WebinarService
    from app.tasks.events import EventBus
    from app.tasks.manager import TaskManager

    async def _run():
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'w.db'}")
        await init_db(engine)
        Session = make_sessionmaker(engine)
        manager = TaskManager(tmp_root=tmp_path / "tmp")
        service = WebinarService(
            manager=manager,
            bus=EventBus(),
            sessionmaker=Session,
            manifest=mini_manifest,
            assets_root=REPO_ROOT,
            results_dir=tmp_path / "results",
        )
        with pytest.raises(ValueError):
            await service.create("1", {"variant": "nope", "title": "t"}, hero_bytes=b"x")
        await manager.shutdown()
        await engine.dispose()

    asyncio.run(_run())
