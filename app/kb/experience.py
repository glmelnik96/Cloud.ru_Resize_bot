"""Слой «опыт» библиотеки знаний: kb_runs → блок фактов в промптах.

Симметрично слою фактов (app/kb/store.py): app читает БД и инжектит снапшот в
graph.knowledge — граф не импортирует app. Здесь и запись исхода
(record_outcome), и чтение снапшота с инжектом в граф (refresh_experience).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import models
from graph import knowledge
from graph.knowledge import ExperienceNote


async def record_outcome(
    sessionmaker, *, session_id: str, outcome: str, comment: str, recipe: dict
) -> bool:
    """Отметить исход запуска. True — строка создана, False — обновлена.

    Одна строка на session_id: человек имеет право передумать («вроде ок» →
    через день «не пошло»), и правдой должна остаться последняя отметка, а не
    первая. Поля берутся из рецепта (Task 8), а не из чекпоинта: чекпоинт
    может быть уже подчищен, а рецепт лежит в самой задаче.
    """
    recipe = recipe or {}
    # kb_source в рецепте — либо словарь {slug,name,version}, либо его нет:
    # запуск без совпадения по каталогу тоже попадает в опыт, просто без slug.
    kb = recipe.get("kb_source")
    kb = kb if isinstance(kb, dict) else {}

    def _fill(row: models.KbRun) -> None:
        row.slug = kb.get("slug") or ""
        row.outcome = outcome
        row.comment = comment or ""
        row.slogan = recipe.get("slogan") or ""
        row.anchor = recipe.get("anchor") or ""
        row.desired_outcome = recipe.get("desired_outcome") or ""
        row.metaphor = recipe.get("metaphor") or ""
        row.persona_segment = recipe.get("persona_segment") or ""

    async def _find(s) -> models.KbRun | None:
        res = await s.execute(
            select(models.KbRun).where(models.KbRun.session_id == session_id)
        )
        return res.scalars().first()

    async with sessionmaker() as s:
        row = await _find(s)
        if row is not None:
            _fill(row)
            await s.commit()
            return False
        row = models.KbRun(session_id=session_id)
        s.add(row)
        _fill(row)
        try:
            await s.commit()
        except IntegrityError:
            # Между SELECT и INSERT кто-то успел отметить тот же запуск: две
            # вкладки, ретрай прокси. Уникальный session_id ловит дубль, но для
            # человека это не ошибка, а смена мнения — дочитываем чужую строку и
            # переписываем её, как при обычном повторе.
            await s.rollback()
            row = await _find(s)
            if row is None:
                raise
            _fill(row)
            await s.commit()
            return False
        return True


# Сколько последних исходов держим в снапшоте. Больше не нужно: в промпт
# уходит максимум 5 по одному продукту, а таблица растёт бесконечно.
_SNAPSHOT_LIMIT = 200


async def load_experience(sessionmaker) -> tuple[ExperienceNote, ...]:
    """Принятые исходы, свежие первыми (порядок важен: experience_for режет
    хвост limit'ом и должен оставлять самое свежее).

    Снапшот односторонний — только outcome="shipped". Забракованное в промпт
    не идёт по решению спеки, а тянуть его сюда значит съедать окно: отклонений
    в живой работе больше, чем принятого (в этом смысл инструмента), и общие
    200 строк оставили бы копирайтеру десятки годных на десяток продуктов —
    опыт по редко используемому продукту молча выпал бы через полгода.
    Человеческую ленту (там видны оба исхода) обслуживает отдельный запрос в
    app/api/routes_kb.py::list_experience.

    Свежесть считаем по updated_at, а не created_at: смена мнения переписывает
    строку по месту, и по дате первой отметки сегодняшнее решение выглядело бы
    месячным.
    """
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(models.KbRun)
                .where(models.KbRun.outcome == "shipped")
                .order_by(models.KbRun.updated_at.desc(), models.KbRun.id.desc())
                .limit(_SNAPSHOT_LIMIT)
            )
        ).scalars().all()
    return tuple(
        ExperienceNote(
            slug=r.slug or "",
            outcome=r.outcome,
            slogan=r.slogan or "",
            anchor=r.anchor or "",
            desired_outcome=r.desired_outcome or "",
            metaphor=r.metaphor or "",
            persona_segment=r.persona_segment or "",
            comment=r.comment or "",
        )
        for r in rows
    )


async def refresh_experience(sessionmaker) -> int:
    """Инжект снапшота в граф (граф не импортирует app — толкаем отсюда)."""
    notes = await load_experience(sessionmaker)
    knowledge.set_experience(notes)
    return len(notes)
