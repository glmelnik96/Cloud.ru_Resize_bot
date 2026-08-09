"""Общие фикстуры юнит-тестов App3."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_graph_state():
    """Оба слоя знаний графа — глобальное состояние модуля: и lifespan
    приложения, и роуты записи подменяют их снапшотом временной БД. Файлов,
    поднимающих create_app, уже несколько, и ни один за собой не убирал — без
    общего сброса первая же карточка (или отметка исхода) из чужой временной БД
    делает тесты каталога (test_knowledge_catalog, test_understand_product) и
    опыта (test_experience_block, тесты узлов) зависимыми от порядка запуска.

    Слоя два: факты (set_catalog) и опыт (set_experience) — второй завёл Task 13
    и подключил ровно к тому же lifespan, так что течёт он точно так же."""
    from graph import knowledge

    yield
    knowledge.set_catalog(None)
    knowledge.set_experience(())
