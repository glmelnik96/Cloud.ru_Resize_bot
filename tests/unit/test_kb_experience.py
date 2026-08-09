"""kb_runs: отметка исхода запуска — единственный вход в слой опыта."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import models
from app.db.database import init_db, make_engine, make_sessionmaker
from app.kb.experience import record_outcome

_RECIPE = {
    "kb_source": {"slug": "managed-rag", "name": "Managed RAG", "version": 3},
    "persona_segment": "ML-инженеры",
    "slogan": "GPU без очереди",
    "anchor": "боль: очередь на GPU",
    "desired_outcome": "обучение стартует за минуты",
    "metaphor": "a bridge across a canyon",
}


@pytest.fixture
async def Session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_record_outcome_writes_one_row_from_recipe(Session):
    assert await record_outcome(
        Session, session_id="run1", outcome="shipped",
        comment="ушло в кампанию как есть", recipe=_RECIPE,
    ) is True
    async with Session() as s:
        rows = (await s.execute(select(models.KbRun))).scalars().all()
    assert len(rows) == 1
    r = rows[0]
    assert r.slug == "managed-rag" and r.outcome == "shipped"
    assert r.slogan == "GPU без очереди"
    assert r.anchor == "боль: очередь на GPU"
    assert r.persona_segment == "ML-инженеры"
    assert r.desired_outcome == "обучение стартует за минуты"
    assert r.metaphor == "a bridge across a canyon"
    assert r.comment == "ушло в кампанию как есть"


async def test_record_outcome_updates_existing_row(Session):
    """Одна строка на запуск: передумал — исход перезаписывается, а не
    двоится. Возврат False означает «строка была, обновили»."""
    await record_outcome(Session, session_id="run1", outcome="shipped", comment="", recipe=_RECIPE)
    assert await record_outcome(
        Session, session_id="run1", outcome="rejected", comment="передумал", recipe=_RECIPE
    ) is False
    async with Session() as s:
        rows = (await s.execute(select(models.KbRun))).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == "rejected" and rows[0].comment == "передумал"


async def test_record_outcome_without_kb_source_keeps_empty_slug(Session):
    assert await record_outcome(
        Session, session_id="run2", outcome="rejected", comment="", recipe={"slogan": "S"}
    ) is True
    async with Session() as s:
        row = (await s.execute(select(models.KbRun))).scalars().one()
    assert row.slug == "" and row.slogan == "S"


async def test_record_outcome_survives_concurrent_first_marks(tmp_path):
    """Две вкладки (или ретрай прокси) отмечают исход одновременно: уникальный
    session_id ловит дубль на INSERT, но для человека это не ошибка, а смена
    мнения — обе отметки должны вернуться, строка остаться одна.

    Своя БД файлом, а не общая ин-мемори фикстура: на `:memory:` SQLAlchemy
    держит один общий коннект (StaticPool), поэтому обе сессии сидят в одной
    транзакции и rollback одной сносит вставку другой — гонки как в проде там
    не воспроизвести.
    """
    import asyncio

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    await init_db(engine)
    Session = make_sessionmaker(engine)
    try:
        results = await asyncio.gather(
            record_outcome(
                Session, session_id="race", outcome="shipped", comment="a", recipe=_RECIPE
            ),
            record_outcome(
                Session, session_id="race", outcome="rejected", comment="b", recipe=_RECIPE
            ),
        )
        assert sorted(results) == [False, True]
        async with Session() as s:
            rows = (await s.execute(select(models.KbRun))).scalars().all()
        assert len(rows) == 1
    finally:
        await engine.dispose()


async def test_load_experience_is_newest_first_and_maps_fields(Session):
    from app.kb.experience import load_experience

    await record_outcome(
        Session, session_id="r1", outcome="shipped", comment="первый", recipe=_RECIPE
    )
    await record_outcome(
        Session, session_id="r2", outcome="rejected", comment="второй", recipe=_RECIPE
    )
    notes = await load_experience(Session)
    assert [n.comment for n in notes] == ["второй", "первый"]
    assert notes[0].slug == "managed-rag"
    assert notes[0].slogan == "GPU без очереди"
    assert notes[0].metaphor == "a bridge across a canyon"


async def test_refresh_experience_injects_into_graph(Session):
    from app.kb.experience import refresh_experience
    from graph import knowledge

    await record_outcome(
        Session, session_id="r1", outcome="shipped", comment="", recipe=_RECIPE
    )
    try:
        n = await refresh_experience(Session)
        assert n == 1
        assert knowledge.experience_for("managed-rag")[0].slogan == "GPU без очереди"
    finally:
        knowledge.set_experience(())
