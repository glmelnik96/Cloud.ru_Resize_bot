"""Общие фикстуры юнит-тестов App3."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_graph_catalog():
    """Каталог графа — глобальное состояние модуля: и lifespan приложения,
    и роуты записи подменяют его снапшотом временной БД. Файлов, поднимающих
    create_app, уже несколько, и ни один за собой не убирал — без общего сброса
    первая же карточка из чужой временной БД делает тесты каталога
    (test_knowledge_catalog, test_understand_product) зависимыми от порядка."""
    from graph import knowledge

    yield
    knowledge.set_catalog(None)
