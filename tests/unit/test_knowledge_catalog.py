"""set_catalog: app-слой подменяет источник каталога (БД -> граф) без импорта app из graph."""

from __future__ import annotations

import pytest

from graph import knowledge
from graph.knowledge import ProductDoc


@pytest.fixture(autouse=True)
def _reset_catalog():
    yield
    knowledge.set_catalog(None)


def _doc(slug="test-prod", name="Test Prod", alias="TestProd"):
    return ProductDoc(
        slug=slug, name=name, aliases=(name, alias), tagline="тестовый продукт",
        body="## Блок 1. Описание\nтело", version=3,
    )


def test_set_catalog_overrides_file_source():
    knowledge.set_catalog((_doc(),))
    assert [d.slug for d in knowledge.load_catalog()] == ["test-prod"]
    found = knowledge.find_product("хочу Test Prod в проде")
    assert found is not None and found.slug == "test-prod"
    assert "тестовый продукт" in knowledge.glossary()


def test_set_catalog_none_restores_files():
    knowledge.set_catalog((_doc(),))
    knowledge.set_catalog(None)
    slugs = {d.slug for d in knowledge.load_catalog()}
    assert "evolution-ml-inference" in slugs


def test_get_by_slug():
    knowledge.set_catalog((_doc(),))
    assert knowledge.get_by_slug("test-prod").name == "Test Prod"
    assert knowledge.get_by_slug("nope") is None
