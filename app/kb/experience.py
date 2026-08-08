"""Слой «опыт» библиотеки знаний: kb_runs → блок фактов в промптах.

Симметрично слою фактов (app/kb/store.py): app читает БД и инжектит снапшот в
graph.knowledge — граф не импортирует app. Здесь только запись исхода; чтение
и инжект — Task 13.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import models


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
