"""App3 Phase 2 — orchestrator segment driver + create() gate.

Uses a fake compiled graph (no Redis, no LLM) to validate that the first
segment parks at awaiting_text and publishes the decision payload, and that
create() persists a queued task + enforces the per-user open-session gate.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import func, select  # noqa: E402

from app.db import models  # noqa: E402
from app.db.database import init_db, make_engine, make_sessionmaker  # noqa: E402
from app.services.creatives import (  # noqa: E402
    CapacityError,
    CreativesService,
    DecisionConflict,
    build_brief,
)
from app.tasks.events import EventBus  # noqa: E402
from app.tasks.manager import TaskManager  # noqa: E402
from graph.builder import GRAPH_VERSION  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────
class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


class _FakeState:
    def __init__(self, values):
        self.values = values


class _FakeGraph:
    """astream: emit a couple node updates, then park at a text interrupt."""

    def __init__(self, interrupt_value):
        self._iv = interrupt_value

    async def astream(self, payload, config=None, stream_mode=None):
        yield {"understand_product": {"product": {"canonical_name": "x"}}}
        yield {"generate_message_candidates": {"candidates": []}}
        yield {"__interrupt__": [_FakeInterrupt(self._iv)]}


class _ResumableGraph:
    """Branches on the input: initial → text interrupt; Command(resume=approve)
    → image interrupt; Command(resume=cancel) → terminal cancelled."""

    def __init__(self):
        # graph_version — свежий прогон на текущей топологии (гард резюме проходит)
        self.values = {
            "ranked": [{"id": "c1", "slogan": "S"}],
            "image_prompt": "P",
            "image_style": "render",
            "metaphor_meta": [{"metaphor": "мост через каньон",
                               "intended_inference": "путь становится коротким",
                               "anti_reading": "не стройка"}],
            "graph_version": GRAPH_VERSION,
        }

    async def astream(self, payload, config=None, stream_mode=None):
        resume = getattr(payload, "resume", None)
        if resume is None:
            yield {"__interrupt__": [_FakeInterrupt({"kind": "text_approve", "candidates": [{"id": "c1", "slogan": "S"}]})]}
            return
        action = resume.get("action")
        if action == "approve":
            yield {"route_image_style": {"image_style": "render"}}
            yield {"__interrupt__": [_FakeInterrupt(
                {"kind": "image_upload", "image_prompt": "P", "image_style": "render",
                 "metaphor": "мост через каньон",
                 "intended_inference": "путь становится коротким",
                 "anti_reading": "не стройка"}
            )]}
        elif action == "cancel":
            yield {"hitl_text_approve": {"cancelled": True}}
        else:  # regenerate → back to a fresh text interrupt
            yield {"__interrupt__": [_FakeInterrupt({"kind": "text_approve", "candidates": [{"id": "c2", "slogan": "S2"}]})]}

    async def aget_state(self, config):
        return _FakeState(self.values)


class _TerminalGraph:
    """Resume(upload) → terminal with rendered files + zip."""

    def __init__(self, files, zip_path):
        self._files = files
        self._zip = zip_path

    async def astream(self, payload, config=None, stream_mode=None):
        yield {"fill_templates_per_format": {"rendered_files": self._files}}
        yield {"render_all": {"rendered_zip_path": self._zip}}


async def _sessionmaker(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'orch.db'}")
    await init_db(engine)
    return make_sessionmaker(engine)


class _FakeHeroGen:
    """Available generator that writes a stub PNG to dest."""

    available = True

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.seen = None
        self.seen_identity = None

    async def generate(self, *, prompt, style, dest, user_id=None, email=None):
        if self.fail:
            raise RuntimeError("boom")
        self.seen = (prompt, style)
        self.seen_identity = (user_id, email)
        from pathlib import Path as _P

        _P(dest).write_bytes(b"\x89PNG-gen")
        return dest


def _service(Session, graph, **kw):
    return CreativesService(
        manager=TaskManager(tmp_root=kw.get("tmp", "./data/tmp_test")),
        bus=kw.get("bus", EventBus()),
        sessionmaker=Session,
        graph=graph,
        results_dir=kw.get("results_dir", "./data/results_test"),
        hero_generator=kw.get("hero_generator"),
        max_open_per_user=kw.get("max_open", 5),
    )


# ── tests ──────────────────────────────────────────────────────
def test_build_brief_keeps_wording_verbatim():
    brief = build_brief(
        {
            "product": "Облако",
            "audience": "DevOps",
            "emotion": "уверенность и контроль — будто всё управление под рукой",
            "notes": "обязательно упомянуть бесплатный период",
            "source_url": "https://cloud.ru/products/rag",
        }
    )
    assert brief["product"] == "Облако"
    assert brief["audience_raw"] == "DevOps"
    assert brief["emotion"].startswith("уверенность и контроль")
    assert brief["notes"] == "обязательно упомянуть бесплатный период"
    assert brief["source_url"] == "https://cloud.ru/products/rag"
    # the page is read by understand_product, not by the web layer
    assert brief["source_text"] == ""


def test_build_brief_optional_fields_default_empty():
    brief = build_brief({"product": "Облако", "audience": "DevOps", "emotion": "драйв"})
    assert brief["notes"] == ""
    assert brief["source_url"] == ""


@pytest.mark.asyncio
async def test_segment_parks_at_awaiting_text(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    iv = {"kind": "text_approve", "candidates": [{"id": "c1", "slogan": "Быстро в облако"}]}
    svc = _service(Session, _FakeGraph(iv), bus=bus, tmp=tmp_path / "tmp")

    # seed a queued task row
    async with Session() as s:
        s.add(models.Task(task_uid="t1", user_id=1, workflow="creatives", status="queued"))
        await s.commit()

    q = bus.subscribe("t1")
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t1", label="creatives", eta_sec=None)
    await svc._run_segment("t1", "1", {"brief": {"product": "x"}}, reporter)

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t1"))
        task = res.scalar_one()
    assert task.status == "awaiting_text"
    assert task.started_at is not None

    # the awaiting_input event carries the candidate for the UI
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    awaiting = [e for e in events if e["kind"] == "awaiting_input"]
    assert awaiting and awaiting[0]["phase"] == "text_approve"
    assert awaiting[0]["candidates"][0]["slogan"] == "Быстро в облако"


@pytest.mark.asyncio
async def test_park_persona_kind_sets_awaiting_persona(tmp_path):
    """interrupt kind=persona_approve паркует в awaiting_persona с payload
    персоны + kb_match для экрана «Кому пишем»."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    iv = {
        "kind": "persona_approve",
        "persona": {"segment": "ML-инженер"},
        "kb_match": {"slug": "evolution-ml-inference", "name": "X", "version": 1},
    }
    svc = _service(Session, _FakeGraph(iv), bus=bus, tmp=tmp_path / "tmp")

    async with Session() as s:
        s.add(models.Task(task_uid="p1", user_id=1, workflow="creatives", status="queued"))
        await s.commit()

    q = bus.subscribe("p1")
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="p1", label="creatives", eta_sec=None)
    await svc._run_segment("p1", "1", {"brief": {"product": "x"}}, reporter)

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "p1"))
        task = res.scalar_one()
    assert task.status == "awaiting_persona"

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    awaiting = [e for e in events if e["kind"] == "awaiting_input"]
    assert awaiting and awaiting[0]["phase"] == "persona_approve"
    assert awaiting[0]["persona"] == {"segment": "ML-инженер"}
    assert awaiting[0]["kb_match"] == {
        "slug": "evolution-ml-inference", "name": "X", "version": 1,
    }


@pytest.mark.asyncio
async def test_pending_rehydrates_awaiting_persona(tmp_path):
    """Reconnect на awaiting_persona: pending() отдаёт персону + kb_match из
    чекпоинта, чтобы браузер перерисовал экран решения."""
    graph = _ResumableGraph()
    graph.values = {
        "personas": [{"segment": "ML-инженер"}],
        "kb_match": {"slug": "evolution-ml-inference", "name": "X", "version": 1},
    }
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, graph, tmp=tmp_path / "tmp")
    payload = await svc.pending("tP", "awaiting_persona")
    assert payload["phase"] == "persona_approve"
    assert payload["persona"] == {"segment": "ML-инженер"}
    assert payload["kb_match"]["slug"] == "evolution-ml-inference"


@pytest.mark.asyncio
async def test_create_persists_queued_and_enqueues(tmp_path, monkeypatch):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _FakeGraph({"kind": "text_approve"}), tmp=tmp_path / "tmp")

    captured = {}

    async def fake_submit(user_id, runner):
        captured["user_id"] = user_id
        captured["runner"] = runner
        return True

    monkeypatch.setattr(svc.manager, "submit", fake_submit)
    uid = await svc.create("1", {"product": "p", "goal": "g", "audience": "a"})

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == uid))
        task = res.scalar_one()
    assert task.status == "queued"
    assert task.workflow == "creatives"
    assert task.params["product"] == "p"
    assert captured["user_id"] == "1" and callable(captured["runner"])


@pytest.mark.asyncio
async def test_create_rejects_when_too_many_open(tmp_path):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _FakeGraph({"kind": "text_approve"}), tmp=tmp_path / "tmp", max_open=2)
    async with Session() as s:
        for i in range(2):
            s.add(models.Task(task_uid=f"o{i}", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()
    with pytest.raises(CapacityError):
        await svc.create("1", {"product": "p", "goal": "g", "audience": "a"})


@pytest.mark.asyncio
async def test_approve_advances_to_awaiting_image(tmp_path):
    """Resuming a parked text task with approve runs the next segment and parks
    at awaiting_image, publishing the image_prompt for the UI."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(
        Session, _ResumableGraph(), bus=bus, tmp=tmp_path / "tmp",
        hero_generator=_FakeHeroGen(),
    )
    async with Session() as s:
        s.add(models.Task(task_uid="t2", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    q = bus.subscribe("t2")
    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t2", label="creatives", eta_sec=None)
    await svc._run_segment(
        "t2", "1", Command(resume={"action": "approve", "comment": None}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t2"))
        task = res.scalar_one()
    assert task.status == "awaiting_image"
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    awaiting = [e for e in events if e["kind"] == "awaiting_input"]
    assert awaiting and awaiting[0]["phase"] == "image_upload"
    assert awaiting[0]["image_prompt"] == "P"
    assert awaiting[0]["can_generate"] is True
    # Задумка образа обязана доехать до экрана — иначе комментировать нечего.
    assert awaiting[0]["metaphor"] == "мост через каньон"
    assert awaiting[0]["anti_reading"] == "не стройка"


@pytest.mark.asyncio
async def test_cancel_reaches_terminal_cancelled(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(Session, _ResumableGraph(), bus=bus, tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="t3", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    q = bus.subscribe("t3")
    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t3", label="creatives", eta_sec=None)
    await svc._run_segment(
        "t3", "1", Command(resume={"action": "cancel", "comment": None}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t3"))
        task = res.scalar_one()
    assert task.status == "cancelled"
    assert task.finished_at is not None
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e["kind"] == "cancelled" for e in events)


@pytest.mark.asyncio
async def test_pending_rehydrates_from_checkpoint(tmp_path):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")
    text_payload = await svc.pending("tX", "awaiting_text")
    assert text_payload["phase"] == "text_approve"
    assert text_payload["candidates"] == [{"id": "c1", "slogan": "S"}]
    img_payload = await svc.pending("tX", "awaiting_image")
    assert img_payload["phase"] == "image_upload"
    assert img_payload["image_prompt"] == "P"
    # /pending собирает задумку из состояния, а SSE — из payload interrupt:
    # два разных источника, которые обязаны сойтись, иначе экран после F5
    # отличается от экрана по стриму.
    assert img_payload["metaphor"] == "мост через каньон"
    assert img_payload["intended_inference"] == "путь становится коротким"
    assert img_payload["anti_reading"] == "не стройка"
    # Генератора у этого сервиса нет (Null) — значит и кнопки быть не должно.
    assert img_payload["can_generate"] is False
    assert await svc.pending("tX", "done") is None


@pytest.mark.asyncio
async def test_pending_can_generate_reflects_backend(tmp_path):
    """/pending и SSE обязаны сойтись по can_generate: экран после F5 — тот же."""
    Session = await _sessionmaker(tmp_path)
    svc = _service(
        Session, _ResumableGraph(), tmp=tmp_path / "tmp", hero_generator=_FakeHeroGen()
    )
    payload = await svc.pending("tX", "awaiting_image")
    assert payload["can_generate"] is True


@pytest.mark.asyncio
async def test_terminal_collects_results_to_results_dir(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    # fake rendered outputs on disk
    src = tmp_path / "renders"
    src.mkdir()
    png = src / "banner_300x250.png"
    png.write_bytes(b"\x89PNG-fake")
    zp = tmp_path / "out.zip"
    zp.write_bytes(b"PK-fake-zip")

    results = tmp_path / "results"
    svc = _service(
        Session,
        _TerminalGraph([{"format": "banner_300x250", "path": str(png)}], str(zp)),
        bus=bus, tmp=tmp_path / "tmp", results_dir=results,
    )
    async with Session() as s:
        s.add(models.Task(task_uid="t5", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="t5", label="creatives", eta_sec=None)
    await svc._run_segment(
        "t5", "1", Command(resume={"action": "upload", "local_path": "x"}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t5"))
        task = res.scalar_one()
    assert task.status == "done"
    assert task.result_url == "/results/t5/t5.zip"
    assert (results / "t5" / "t5.zip").exists()
    assert (results / "t5" / "banner_300x250.png").exists()


@pytest.mark.asyncio
async def test_terminal_persists_banner_cards(tmp_path):
    """Block 3 (2026-07-10): at 'done' the orchestrator snapshots the ranked
    propositions (+ per-banner scenario) from the checkpoint into
    task.params['cards'], so the final grid can caption each banner even
    after the graph checkpoint is gone."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    png = tmp_path / "b.png"
    png.write_bytes(b"x")
    graph = _TerminalGraph([{"format": "01_render", "path": str(png)}], None)
    graph.values = {
        "ranked": [
            {"id": "c1", "slogan": "S1", "body": "B1", "cta": "CTA1",
             "hook_angle": "rational", "score": 9.0, "reason": "R1",
             "internal": "leak"},
            {"id": "c2", "slogan": "S2", "body": "B2", "cta": "CTA2",
             "hook_angle": "curiosity", "score": 8.0, "reason": "R2"},
        ],
        "scenarios": ["render", "photo"],
    }

    async def _aget_state(config):
        return _FakeState(graph.values)

    graph.aget_state = _aget_state
    svc = _service(Session, graph, bus=bus, tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    async with Session() as s:
        s.add(models.Task(task_uid="cards1", user_id=1, workflow="creatives",
                          status="awaiting_image", params={"product": "p"}))
        await s.commit()

    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="cards1", label="creatives", eta_sec=None)
    await svc._run_segment(
        "cards1", "1", Command(resume={"action": "upload", "local_path": "x"}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "cards1"))
        task = res.scalar_one()
    assert task.status == "done"
    cards = task.params["cards"]
    assert [c["slogan"] for c in cards] == ["S1", "S2"]
    assert [c["scenario"] for c in cards] == ["render", "photo"]
    assert cards[0]["reason"] == "R1" and cards[0]["cta"] == "CTA1"
    assert "internal" not in cards[0] and "id" not in cards[0]
    # the original brief params survive the merge
    assert task.params["product"] == "p"


@pytest.mark.asyncio
async def test_terminal_persists_recipe_alongside_cards(tmp_path):
    """Рецепт запуска должен доехать до БД тем же финишем, что и карточки:
    ZIP с provenance.json ретеншен удалит через сутки, а объяснение результата
    остаётся в params. Два снимка пишутся подряд через переприсваивание
    params — проверяем, что второй не затирает ни первый, ни бриф."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    png = tmp_path / "b.png"
    png.write_bytes(b"x")
    graph = _TerminalGraph([{"format": "01_render", "path": str(png)}], None)
    graph.values = {
        "ranked": [{"id": "c1", "slogan": "S1", "anchor": "A1",
                    "desired_outcome": "быстрее", "cta": "CTA1"}],
        "scenarios": ["render"],
        "kb_match": {"slug": "kb-7", "name": "KB", "version": 1},
        "winner_id": "c1",
        "personas": [{"segment": "архитекторы"}],
        "metaphor_meta": [{"metaphor": "мост", "intended_inference": "короткий путь",
                           "anti_reading": "не стройка"}],
        "metaphor_comments": ["слишком буквально"],
        "image": {"local_path": "x"},
    }

    async def _aget_state(config):
        return _FakeState(graph.values)

    graph.aget_state = _aget_state
    svc = _service(Session, graph, bus=bus, tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    async with Session() as s:
        s.add(models.Task(task_uid="rec1", user_id=1, workflow="creatives",
                          status="awaiting_image", params={"product": "p"}))
        await s.commit()

    from langgraph.types import Command

    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="rec1", label="creatives", eta_sec=None)
    await svc._run_segment(
        "rec1", "1", Command(resume={"action": "upload", "local_path": "x"}), reporter
    )

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "rec1"))
        task = res.scalar_one()
    assert task.status == "done"
    recipe = task.params["recipe"]
    assert recipe["kb_source"]["slug"] == "kb-7"
    assert recipe["winner_id"] == "c1"
    assert recipe["persona_segment"] == "архитекторы"
    assert recipe["slogan"] == "S1" and recipe["desired_outcome"] == "быстрее"
    assert recipe["metaphor"] == "мост" and recipe["anti_reading"] == "не стройка"
    assert recipe["metaphor_comments"] == ["слишком буквально"]
    assert recipe["hero_source"] == "uploaded"
    # соседние снимки в params не пострадали от второго переприсваивания
    assert [c["slogan"] for c in task.params["cards"]] == ["S1"]
    assert task.params["product"] == "p"


@pytest.mark.asyncio
async def test_terminal_without_aget_state_still_finishes(tmp_path):
    """Cards are best-effort: a graph without aget_state (or a snapshot
    failure) must not break task completion."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    png = tmp_path / "b.png"
    png.write_bytes(b"x")
    svc = _service(
        Session, _TerminalGraph([{"format": "f1", "path": str(png)}], None),
        bus=bus, tmp=tmp_path / "tmp", results_dir=tmp_path / "res",
    )
    async with Session() as s:
        s.add(models.Task(task_uid="nog1", user_id=1, workflow="creatives",
                          status="awaiting_image"))
        await s.commit()

    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="nog1", label="creatives", eta_sec=None)
    await svc._run_segment(
        "nog1", "1", Command(resume={"action": "upload", "local_path": "x"}), reporter
    )
    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "nog1"))
        task = res.scalar_one()
    assert task.status == "done"
    assert "cards" not in (task.params or {})


@pytest.mark.asyncio
async def test_collect_results_png_fallback_when_no_zip(tmp_path):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    url = svc._collect_results("u1", {"rendered_files": [{"format": "f1", "path": str(png)}]})
    assert url == "/results/u1/f1.png"
    assert (tmp_path / "res" / "u1" / "f1.png").exists()


@pytest.mark.asyncio
async def test_null_hero_generator_unavailable():
    from app.services.hero_gen import HeroGenUnavailable, NullHeroGenerator

    g = NullHeroGenerator()
    assert g.available is False
    with pytest.raises(HeroGenUnavailable):
        await g.generate(prompt="x", style="render", dest="y")


@pytest.mark.asyncio
async def test_app1_hero_generator_sends_scenario(tmp_path, monkeypatch):
    """App1's /internal/hero needs the brand scenario (render|photo). The
    generator must POST it alongside prompt+style (2026-06-21 Block E)."""
    from app.services.hero_gen import App1HeroGenerator

    captured = {}

    class _FakeResp:
        headers = {"content-type": "image/png"}
        content = b"\x89PNG-bytes"

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    gen = App1HeroGenerator("http://127.0.0.1:8011/internal/hero")
    dest = tmp_path / "h.png"
    await gen.generate(prompt="a server, no text", style="render", dest=dest)

    assert captured["json"]["prompt"] == "a server, no text"
    assert captured["json"]["scenario"] == "render"
    assert dest.read_bytes() == b"\x89PNG-bytes"


@pytest.mark.asyncio
async def test_app1_hero_generator_sends_ratio_for_photo_only(tmp_path, monkeypatch):
    """App1 contract 195b892 (2026-07-10): /internal/hero accepts optional
    `ratio` forwarded into brand_t2i. Photo heroes land in a 300x550 cover
    slot, so we request r_9_16 (the r_1_1 default center-crops the scene).
    Render stays square (cutout contain) — the field is OMITTED, not sent
    empty, to keep the legacy path byte-identical."""
    from app.services.hero_gen import App1HeroGenerator

    captured = {}

    class _FakeResp:
        headers = {"content-type": "image/png"}
        content = b"\x89PNG-bytes"

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers=None):
            captured["json"] = json
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    gen = App1HeroGenerator("http://127.0.0.1:8011/internal/hero")

    await gen.generate(prompt="p", style="photo", dest=tmp_path / "a.png")
    assert captured["json"]["scenario"] == "photo"
    assert captured["json"]["ratio"] == "r_9_16"

    await gen.generate(prompt="p", style="render", dest=tmp_path / "b.png")
    assert captured["json"]["scenario"] == "render"
    assert "ratio" not in captured["json"]


@pytest.mark.asyncio
async def test_app1_hero_generator_forwards_user_headers(tmp_path, monkeypatch):
    """App1 lanes hero gen per end-user via X-User-Id (App1 commit 2010463).
    The generator must forward the gateway user id (+ email) as headers so a
    burst from many users is parallelised instead of serialised into one lane.
    """
    from app.services.hero_gen import App1HeroGenerator

    captured = {}

    class _FakeResp:
        headers = {"content-type": "image/png"}
        content = b"\x89PNG-bytes"

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers=None):
            captured["headers"] = headers
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    gen = App1HeroGenerator("http://127.0.0.1:8011/internal/hero")
    await gen.generate(
        prompt="a server, no text", style="render", dest=tmp_path / "h.png",
        user_id="gw-42", email="user@cloud.ru",
    )
    assert captured["headers"]["X-User-Id"] == "gw-42"
    assert captured["headers"]["X-User-Email"] == "user@cloud.ru"


@pytest.mark.asyncio
async def test_app1_hero_generator_omits_headers_when_no_identity(tmp_path, monkeypatch):
    """Back-compat: no user_id → no X-User-* header (App1 falls back to the
    shared lane). Sending an empty/None header would be wrong."""
    from app.services.hero_gen import App1HeroGenerator

    captured = {}

    class _FakeResp:
        headers = {"content-type": "image/png"}
        content = b"\x89PNG-bytes"

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers=None):
            captured["headers"] = headers
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    gen = App1HeroGenerator("http://127.0.0.1:8011/internal/hero")
    await gen.generate(prompt="x", style="render", dest=tmp_path / "h.png")
    assert "X-User-Id" not in (captured["headers"] or {})


def test_make_hero_generator_selects_backend():
    from app.services.hero_gen import (
        App1HeroGenerator,
        NullHeroGenerator,
        make_hero_generator,
    )

    assert isinstance(
        make_hero_generator(backend="app1", app1_hero_url="http://127.0.0.1:8011/internal/hero"),
        App1HeroGenerator,
    )
    assert isinstance(make_hero_generator(backend="none", app1_hero_url="x"), NullHeroGenerator)
    # unknown backend → safe default (manual upload)
    assert isinstance(make_hero_generator(backend="???", app1_hero_url="x"), NullHeroGenerator)


@pytest.mark.asyncio
async def test_app1_hero_generator_image_response(tmp_path, monkeypatch):
    """App1HeroGenerator writes image bytes from an image/* response to dest."""
    from app.services import hero_gen as hg

    class _Resp:
        def __init__(self, content=b"", headers=None, json_data=None):
            self.content = content
            self.headers = headers or {}
            self._json = json_data

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json, headers=None):
            return _Resp(content=b"\x89PNG-app1", headers={"content-type": "image/png"})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    gen = hg.App1HeroGenerator("http://127.0.0.1:8011/internal/hero")
    dest = tmp_path / "hero.png"
    out = await gen.generate(prompt="cloud", style="render", dest=dest)
    assert out == dest
    assert dest.read_bytes() == b"\x89PNG-app1"


@pytest.mark.asyncio
async def test_app1_hero_generator_failure_raises_unavailable(tmp_path, monkeypatch):
    from app.services import hero_gen as hg

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json, headers=None):
            raise RuntimeError("connection refused")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    gen = hg.App1HeroGenerator("http://127.0.0.1:8011/internal/hero")
    with pytest.raises(hg.HeroGenUnavailable):
        await gen.generate(prompt="x", style="render", dest=tmp_path / "h.png")


@pytest.mark.asyncio
async def test_awaiting_image_can_generate_reflects_backend(tmp_path):
    """can_generate in the awaiting_image event mirrors generator.available."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(
        Session, _ResumableGraph(), bus=bus, tmp=tmp_path / "tmp",
        hero_generator=_FakeHeroGen(),
    )
    async with Session() as s:
        s.add(models.Task(task_uid="g0", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()
    q = bus.subscribe("g0")
    from langgraph.types import Command
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="g0", label="creatives", eta_sec=None)
    await svc._run_segment("g0", "1", Command(resume={"action": "approve"}), reporter)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    awaiting = [e for e in events if e["kind"] == "awaiting_input"][0]
    assert awaiting["can_generate"] is True


@pytest.mark.asyncio
async def test_generate_decision_unavailable_raises(tmp_path):
    from app.services.hero_gen import HeroGenUnavailable

    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")  # default Null
    with pytest.raises(HeroGenUnavailable):
        await svc.generate_decision("gx", "1")


@pytest.mark.asyncio
async def test_generate_decision_success_runs_to_done(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    png = tmp_path / "r.png"
    png.write_bytes(b"x")
    zp = tmp_path / "r.zip"
    zp.write_bytes(b"z")
    graph = _TerminalGraph([{"format": "f1", "path": str(png)}], str(zp))
    # _TerminalGraph needs aget_state for prompt fetch; свежий прогон несёт
    # актуальную версию топологии — иначе гард graph_version его завернёт
    graph.values = {
        "image_prompt": "P", "image_style": "render", "graph_version": GRAPH_VERSION,
    }

    async def _aget_state(config):
        return _FakeState(graph.values)

    graph.aget_state = _aget_state
    gen = _FakeHeroGen()
    svc = _service(
        Session, graph, bus=bus, tmp=tmp_path / "tmp",
        results_dir=tmp_path / "res", hero_generator=gen,
    )
    async with Session() as s:
        s.add(models.Task(task_uid="g1", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    # generate_decision enqueues a runner on the manager; run it inline instead
    captured = {}

    async def fake_submit(user_id, runner):
        captured["runner"] = runner
        return True

    svc.manager.submit = fake_submit  # type: ignore[assignment]
    await svc.generate_decision(
        "g1", "1", end_user_id="gw-7", end_user_email="g@cloud.ru"
    )
    await captured["runner"]()

    assert gen.seen == ("P", "render")
    # end-user identity is forwarded to the hero generator (App1 per-user lane)
    assert gen.seen_identity == ("gw-7", "g@cloud.ru")
    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "g1"))
        task = res.scalar_one()
    assert task.status == "done"


@pytest.mark.asyncio
async def test_generate_decision_failure_reparks(tmp_path):
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    graph = _ResumableGraph()
    svc = _service(
        Session, graph, bus=bus, tmp=tmp_path / "tmp", hero_generator=_FakeHeroGen(fail=True)
    )
    async with Session() as s:
        s.add(models.Task(task_uid="g2", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()
    captured = {}

    async def fake_submit(user_id, runner):
        captured["runner"] = runner
        return True

    svc.manager.submit = fake_submit  # type: ignore[assignment]
    await svc.generate_decision("g2", "1")
    await captured["runner"]()

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "g2"))
        task = res.scalar_one()
    assert task.status == "awaiting_image"  # re-parked for manual upload


@pytest.mark.asyncio
async def test_submit_decision_flips_to_running_then_enqueues(tmp_path, monkeypatch):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="t4", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    submitted = {}

    async def fake_submit(user_id, runner):
        submitted["called"] = True
        return True

    monkeypatch.setattr(svc.manager, "submit", fake_submit)
    await svc.submit_decision("t4", "1", {"action": "approve", "comment": None})

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "t4"))
        task = res.scalar_one()
    # status flips to running synchronously (closes the double-submit window)
    assert task.status == "running"
    assert submitted.get("called") is True


@pytest.mark.asyncio
async def test_submit_decision_queue_full_restores_status_and_raises(tmp_path, monkeypatch):
    """If the lane queue is full, the decision must NOT be silently dropped:
    status is restored to the parked value and CapacityError is raised so the
    route can answer 429 (the graph is still parked — the user can retry)."""
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="qf1", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    async def full_submit(user_id, runner):
        return False

    monkeypatch.setattr(svc.manager, "submit", full_submit)
    with pytest.raises(CapacityError):
        await svc.submit_decision("qf1", "1", {"action": "approve"})

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "qf1"))
        task = res.scalar_one()
    assert task.status == "awaiting_text"


@pytest.mark.asyncio
async def test_generate_decision_queue_full_restores_awaiting_image(tmp_path, monkeypatch):
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp", hero_generator=_FakeHeroGen())
    async with Session() as s:
        s.add(models.Task(task_uid="qf2", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    async def full_submit(user_id, runner):
        return False

    monkeypatch.setattr(svc.manager, "submit", full_submit)
    with pytest.raises(CapacityError):
        await svc.generate_decision("qf2", "1")

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "qf2"))
        task = res.scalar_one()
    assert task.status == "awaiting_image"


@pytest.mark.asyncio
async def test_create_concurrent_never_exceeds_cap(tmp_path):
    """TOCTOU guard: N concurrent create()s for one user must not overshoot
    max_open_per_user. Without the per-user lock, several would read the same
    open-count and all insert. The lock serializes check→insert so the row
    count lands exactly at the cap."""
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _FakeGraph({"kind": "text_approve"}), tmp=tmp_path / "tmp", max_open=3)

    async def noop_submit(user_id, runner):
        return True

    svc.manager.submit = noop_submit  # type: ignore[assignment]

    async def _try_create():
        try:
            await svc.create("1", {"product": "p", "audience": "a", "emotion": "e"})
            return True
        except CapacityError:
            return False

    results = await asyncio.gather(*(_try_create() for _ in range(8)))
    assert sum(results) == 3  # exactly the cap succeeded

    async with Session() as s:
        res = await s.execute(
            select(func.count()).select_from(models.Task).where(models.Task.user_id == 1)
        )
        assert int(res.scalar_one()) == 3


@pytest.mark.asyncio
async def test_double_resume_second_loses_claim(tmp_path):
    """Double-submit guard: two submit_decision calls racing the same parked
    task — exactly one wins the atomic claim (flips to running + enqueues); the
    other raises DecisionConflict and does NOT enqueue a second resume."""
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="dr1", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    submits = {"count": 0}

    async def counting_submit(user_id, runner):
        submits["count"] += 1
        return True

    svc.manager.submit = counting_submit  # type: ignore[assignment]

    async def _try():
        try:
            await svc.submit_decision("dr1", "1", {"action": "approve"})
            return "ok"
        except DecisionConflict:
            return "conflict"

    outcomes = await asyncio.gather(_try(), _try())
    assert sorted(outcomes) == ["conflict", "ok"]
    assert submits["count"] == 1  # graph resumed exactly once

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "dr1"))
        assert res.scalar_one().status == "running"


@pytest.mark.asyncio
async def test_submit_decision_on_stale_graph_version_fails_clearly(tmp_path):
    """Парковка со старой топологией (чекпоинт без graph_version или с другой
    версией) не восстанавливается на новом графе: submit_decision НЕ бросает
    исключение и не резюмирует граф — задача завершается понятной ошибкой
    «перезапустите», таймер парковки не остаётся висеть."""
    Session = await _sessionmaker(tmp_path)
    graph = _ResumableGraph()
    graph.values = {"ranked": [{"id": "c1"}]}  # старый прогон: graph_version отсутствует
    svc = _service(Session, graph, tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="stale1", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    submits = {"count": 0}

    async def counting_submit(user_id, runner):
        submits["count"] += 1
        return True

    svc.manager.submit = counting_submit  # type: ignore[assignment]

    # не raises — ни DecisionConflict, ни что-либо ещё
    await svc.submit_decision("stale1", "1", {"action": "approve"})

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "stale1"))
        task = res.scalar_one()
    assert task.status == "failed"
    assert "перезапустите" in task.error
    assert submits["count"] == 0  # граф НЕ резюмировался на чужой топологии
    assert "stale1" not in svc._timeouts  # таймер парковки снят


@pytest.mark.asyncio
async def test_generate_decision_on_stale_graph_version_fails_clearly(tmp_path):
    """Та же дырка со стороны кнопки веб-генерации hero: парковка awaiting_image
    со старой топологией (чекпоинт без graph_version) не резюмируется через
    generate_decision — без исключений и без резюме графа задача завершается
    понятной ошибкой «перезапустите», таймер парковки снят."""
    Session = await _sessionmaker(tmp_path)
    graph = _ResumableGraph()
    # старый прогон: graph_version отсутствует
    graph.values = {"image_prompt": "P", "image_style": "render"}
    svc = _service(Session, graph, tmp=tmp_path / "tmp", hero_generator=_FakeHeroGen())
    async with Session() as s:
        s.add(models.Task(task_uid="stale2", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    submits = {"count": 0}

    async def counting_submit(user_id, runner):
        submits["count"] += 1
        return True

    svc.manager.submit = counting_submit  # type: ignore[assignment]

    # не raises — ни DecisionConflict, ни что-либо ещё
    await svc.generate_decision("stale2", "1")

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "stale2"))
        task = res.scalar_one()
    assert task.status == "failed"
    assert "перезапустите" in task.error
    assert submits["count"] == 0  # граф НЕ резюмировался на чужой топологии
    assert "stale2" not in svc._timeouts  # таймер парковки снят


@pytest.mark.asyncio
async def test_submit_decision_checkpoint_read_failure_no_stuck_running(tmp_path):
    """Если чтение чекпоинта в гарде падает (I/O, битая база чекпоинтера),
    задача не должна застрять в running с уже снятым таймером: исключение не
    выходит наружу, задача завершается понятной ошибкой «перезапустите»."""
    Session = await _sessionmaker(tmp_path)
    graph = _ResumableGraph()

    async def broken_aget_state(config):
        raise RuntimeError("checkpoint db corrupted")

    graph.aget_state = broken_aget_state  # type: ignore[assignment]
    svc = _service(Session, graph, tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="io1", user_id=1, workflow="creatives", status="awaiting_text"))
        await s.commit()

    submits = {"count": 0}

    async def counting_submit(user_id, runner):
        submits["count"] += 1
        return True

    svc.manager.submit = counting_submit  # type: ignore[assignment]

    # не raises — гард ловит исключение чтения чекпоинта сам
    await svc.submit_decision("io1", "1", {"action": "approve"})

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "io1"))
        task = res.scalar_one()
    assert task.status == "failed"  # не застряла в running
    assert "перезапустите" in task.error
    assert submits["count"] == 0
    assert "io1" not in svc._timeouts


@pytest.mark.asyncio
async def test_resume_on_non_awaiting_raises_conflict(tmp_path):
    """A resume against a task that is not awaiting_* (already running/done)
    loses the claim → DecisionConflict, so a late duplicate can't re-resume."""
    Session = await _sessionmaker(tmp_path)
    svc = _service(Session, _ResumableGraph(), tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="nr1", user_id=1, workflow="creatives", status="running"))
        await s.commit()
    with pytest.raises(DecisionConflict):
        await svc.submit_decision("nr1", "1", {"action": "approve"})


@pytest.mark.asyncio
async def test_cancelled_segment_frees_running_row(tmp_path):
    """Stuck-running guard: if a segment is cancelled mid-compute, the row must
    not stay 'running' forever (it would count against the open budget). The
    CancelledError handler flips it to failed and re-raises."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()

    class _HangGraph:
        async def astream(self, payload, config=None, stream_mode=None):
            await asyncio.sleep(3600)  # park in compute until cancelled
            yield {}

    svc = _service(Session, _HangGraph(), bus=bus, tmp=tmp_path / "tmp")
    async with Session() as s:
        s.add(models.Task(task_uid="cx1", user_id=1, workflow="creatives", status="queued"))
        await s.commit()

    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="cx1", label="creatives", eta_sec=None)
    task = asyncio.ensure_future(
        svc._run_segment("cx1", "1", {"brief": {"product": "x"}}, reporter)
    )
    await asyncio.sleep(0.05)  # let it enter the compute stretch
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with Session() as s:
        res = await s.execute(select(models.Task).where(models.Task.task_uid == "cx1"))
        row = res.scalar_one()
    assert row.status == "failed"
    assert row.error == "cancelled"


@pytest.mark.asyncio
async def test_hero_generation_publishes_incremental_progress(tmp_path):
    """Hero generation must publish step events counting completions (1/N …
    N/N), not sit at 0/N until the whole gather finishes."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    graph = _ResumableGraph()
    graph.values = {
        "image_prompts": ["P1", "P2", "P3"],
        "scenarios": ["render", "photo", "render"],
        "graph_version": GRAPH_VERSION,
    }
    svc = _service(
        Session, graph, bus=bus, tmp=tmp_path / "tmp", hero_generator=_FakeHeroGen()
    )
    async with Session() as s:
        s.add(models.Task(task_uid="pr1", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    q = bus.subscribe("pr1")
    captured = {}

    async def fake_submit(user_id, runner):
        captured["runner"] = runner
        return True

    svc.manager.submit = fake_submit  # type: ignore[assignment]
    await svc.generate_decision("pr1", "1")
    await captured["runner"]()

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    steps = [e["step"] for e in events if e.get("kind") == "step"]
    assert any("1/3" in s for s in steps)
    assert any("3/3" in s for s in steps)
    # Полоса маршрута в браузере подсвечивает остановку по числу, а не по
    # русской подписи шага: подпись — текст для человека, и её правка не должна
    # молча ломать подсветку. Генерация hero — четвёртая остановка.
    assert [e.get("stage") for e in events if e.get("kind") == "step"] == [4, 4, 4]


@pytest.mark.asyncio
async def test_step_events_carry_route_stage(tmp_path):
    """Каждое событие шага несёт номер остановки маршрута (1..5). Без него
    браузер вынужден сопоставлять русские подписи, и переименование шага на
    сервере тихо гасит подсветку в ленте."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(Session, _FakeGraph({"kind": "text_approve", "candidates": []}), bus=bus)

    q = bus.subscribe("st1")
    async with Session() as s:
        s.add(models.Task(task_uid="st1", user_id=1, workflow="creatives", status="queued"))
        await s.commit()
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="st1", label="creatives", eta_sec=None)
    await svc._run_segment("st1", "1", {}, reporter)

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    by_step = {e["step"]: e.get("stage") for e in events if e.get("kind") == "step"}
    # _FakeGraph отдаёт understand_product (остановка 2) и
    # generate_message_candidates (остановка 3).
    assert by_step == {"Изучаю продукт": 2, "Генерирую 24 предложения": 3}


@pytest.mark.asyncio
async def test_step_events_carry_substage_count(tmp_path):
    """Рядом с номером остановки едет счёт ВНУТРИ неё — числами. Пока счёт жил
    только внутри русской подписи («3/12»), браузер выковыривал его регуляркой,
    то есть разбирал текст, написанный для человека."""
    from app.services.creatives import _STAGE_HERO, _STAGE_UNITS

    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(Session, _FakeGraph({"kind": "text_approve", "candidates": []}), bus=bus)

    q = bus.subscribe("sc1")
    async with Session() as s:
        s.add(models.Task(task_uid="sc1", user_id=1, workflow="creatives", status="queued"))
        await s.commit()
    from app.tasks.status import WebStatusReporter

    reporter = WebStatusReporter(bus, task_uid="sc1", label="creatives", eta_sec=None)
    await svc._run_segment("sc1", "1", {}, reporter)

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    counts = {
        e["step"]: (e.get("stage_done"), e.get("stage_total"))
        for e in events
        if e.get("kind") == "step"
    }
    # understand_product — первый из двух узлов остановки 02;
    # generate_message_candidates — первый из трёх узлов остановки 03.
    assert counts == {"Изучаю продукт": (1, 2), "Генерирую 24 предложения": (1, 3)}
    # Остановка 04 считает узлы и картинки ОДНОЙ шкалой: два узла пишут промпт,
    # дальше двенадцать генераций. Двумя шкалами кромка откатывалась бы назад
    # ровно там, где начинается самая долгая часть прогона.
    assert _STAGE_UNITS[_STAGE_HERO] == 2 + 12


@pytest.mark.asyncio
async def test_hero_progress_continues_the_stage_scale(tmp_path):
    """Счёт генерации продолжает шкалу остановки 04, а не начинается с нуля:
    два узла этапа к этому моменту уже позади."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    graph = _ResumableGraph()
    graph.values = {
        "image_prompts": ["P1", "P2", "P3"],
        "scenarios": ["render", "photo", "render"],
        "graph_version": GRAPH_VERSION,
    }
    svc = _service(
        Session, graph, bus=bus, tmp=tmp_path / "tmp", hero_generator=_FakeHeroGen()
    )
    async with Session() as s:
        s.add(models.Task(task_uid="hp1", user_id=1, workflow="creatives", status="awaiting_image"))
        await s.commit()

    q = bus.subscribe("hp1")
    captured = {}

    async def fake_submit(user_id, runner):
        captured["runner"] = runner
        return True

    svc.manager.submit = fake_submit  # type: ignore[assignment]
    await svc.generate_decision("hp1", "1")
    await captured["runner"]()

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    start = next(e for e in events if e.get("kind") == "start")
    # Старт стоит на двух пройденных узлах из пяти единиц (2 узла + 3 картинки),
    # а не на нуле — иначе кромка прыгнула бы назад.
    assert (start["stage"], start["stage_done"], start["stage_total"]) == (4, 2, 5)
    steps = [
        (e["stage_done"], e["stage_total"]) for e in events if e.get("kind") == "step"
    ]
    assert steps == [(3, 5), (4, 5), (5, 5)]


@pytest.mark.asyncio
async def test_resume_moves_the_route_to_the_next_stop(tmp_path):
    """Возобновление после развилки сразу называет остановку, на которую
    прогон переехал. Без номера в стартовом событии полоса весь следующий
    отрезок стояла на предыдущей остановке — это и читалось как «прогресс не
    соответствует прогрессу»."""
    Session = await _sessionmaker(tmp_path)
    bus = EventBus()
    svc = _service(Session, _ResumableGraph(), bus=bus)
    async with Session() as s:
        s.add(models.Task(task_uid="rs1", user_id=1, workflow="creatives",
                          status="awaiting_persona"))
        await s.commit()

    q = bus.subscribe("rs1")
    captured = {}

    async def fake_submit(user_id, runner):
        captured["runner"] = runner
        return True

    svc.manager.submit = fake_submit  # type: ignore[assignment]
    await svc.submit_decision("rs1", "1", {"action": "approve"})
    await captured["runner"]()

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    start = next(e for e in events if e.get("kind") == "start")
    # Персона утверждена — дальше тексты, остановка 03.
    assert start["stage"] == 3


@pytest.mark.asyncio
async def test_collect_recipe_snapshots_human_decisions(tmp_path):
    """Рецепт собирает то, что решил человек: победитель, персона, карточка
    знаний, метафора и её комментарии."""
    Session = await _sessionmaker(tmp_path)

    class _G:
        values = {
            "ranked": [
                {"id": "c7", "slogan": "GPU без очереди", "anchor": "боль: очередь",
                 "desired_outcome": "обучение стартует за минуты"},
            ],
            "winner_id": "c7",
            "personas": [{"segment": "ML-инженеры"}],
            "kb_match": {"slug": "managed-rag", "name": "Managed RAG", "version": 3},
            "metaphor_meta": [{"candidate_id": "c7", "metaphor": "a bridge",
                               "intended_inference": "путь становится коротким",
                               "anti_reading": "не стройка"}],
            "metaphor_comments": ["слишком буквально"],
            "generated_heroes": [{"local_path": "/tmp/h.png"}],
        }

        async def aget_state(self, config):
            return _FakeState(self.values)

    svc = _service(Session, _G(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    recipe = await svc._collect_recipe("uid1")
    assert recipe["winner_id"] == "c7"
    assert recipe["slogan"] == "GPU без очереди"
    assert recipe["anchor"] == "боль: очередь"
    assert recipe["persona_segment"] == "ML-инженеры"
    assert recipe["kb_source"] == {"slug": "managed-rag", "name": "Managed RAG", "version": 3}
    assert recipe["metaphor"] == "a bridge"
    assert recipe["metaphor_comments"] == ["слишком буквально"]
    assert recipe["hero_source"] == "generated"


@pytest.mark.asyncio
async def test_collect_recipe_keeps_winner_id_empty_when_nobody_chose(tmp_path):
    """Человек принял набор, не ткнув в карточку: hitl_text_approve пишет
    winner_id=None. Рецепт не подставляет вместо решения ranked[0] — иначе
    слой опыта примет скоринговый порядок за человеческий выбор, а рецепт
    разошёлся бы с provenance.json из того же запуска."""
    Session = await _sessionmaker(tmp_path)

    class _G:
        values = {
            "ranked": [{"id": "c1", "slogan": "S", "desired_outcome": "быстрее"}],
            "winner_id": None,
            "image": {"local_path": "/tmp/u.png"},
        }

        async def aget_state(self, config):
            return _FakeState(self.values)

    svc = _service(Session, _G(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    recipe = await svc._collect_recipe("uid3")
    assert recipe["winner_id"] is None
    # Ведущий текст при этом всё равно назван — он берётся из ranked[0].
    assert recipe["slogan"] == "S"
    assert recipe["desired_outcome"] == "быстрее"
    assert recipe["hero_source"] == "uploaded"


@pytest.mark.asyncio
async def test_collect_recipe_hero_source_none_without_any_image(tmp_path):
    Session = await _sessionmaker(tmp_path)

    class _G:
        values = {"ranked": [{"id": "c1"}]}

        async def aget_state(self, config):
            return _FakeState(self.values)

    svc = _service(Session, _G(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    assert (await svc._collect_recipe("uid4"))["hero_source"] == "none"


@pytest.mark.asyncio
async def test_collect_recipe_survives_garbage_state(tmp_path):
    """Разбор полей тоже best-effort: _finish_terminal зовётся вне try/except
    сегмента, и исключение отсюда оставило бы задачу навсегда в running — с
    готовым ZIP на диске и без единого события в браузер."""
    Session = await _sessionmaker(tmp_path)

    class _G:
        values = {"ranked": [{"id": "c1"}], "personas": ["строка вместо персоны"],
                  "metaphor_meta": ["тоже строка"]}

        async def aget_state(self, config):
            return _FakeState(self.values)

    svc = _service(Session, _G(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    recipe = await svc._collect_recipe("uid5")
    assert recipe["persona_segment"] == "" and recipe["metaphor"] == ""


@pytest.mark.asyncio
async def test_collect_recipe_survives_broken_checkpoint(tmp_path):
    """Рецепт — best-effort: сбой чтения чекпоинта не должен ломать финиш."""
    Session = await _sessionmaker(tmp_path)

    class _Boom:
        async def aget_state(self, config):
            raise RuntimeError("checkpoint gone")

    svc = _service(Session, _Boom(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    assert await svc._collect_recipe("uid2") == {}
