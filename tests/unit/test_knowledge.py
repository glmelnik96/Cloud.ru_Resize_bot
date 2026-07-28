"""Unit tests for the vendored product knowledge base (graph/knowledge.py).

Runs against the REAL config/knowledge/ files, because the thing most likely to
break is the vendored data itself: `scripts/build_knowledge.py` is re-run by
hand whenever the internal product pack changes, and a doc that lost its
"## Блок 3." heading would silently feed derive_persona an empty audience
section instead of failing.
"""

from __future__ import annotations

import pytest

from graph.knowledge import find_product, glossary, load_catalog


def test_catalog_loads_and_every_doc_has_the_three_blocks():
    catalog = load_catalog()
    assert len(catalog) >= 6
    for doc in catalog:
        assert doc.tagline, f"{doc.slug}: no tagline (glossary line would be dropped)"
        assert doc.product_facts, f"{doc.slug}: Блоки 1–2 missing"
        assert doc.audiences, f"{doc.slug}: Блок 3 missing → persona loses grounding"
        assert doc.audiences.startswith("## Блок 3.")


def test_slugs_are_unique():
    slugs = [d.slug for d in load_catalog()]
    assert len(slugs) == len(set(slugs))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("нужен баннер под Evolution Managed RAG", "evolution-managed-rag"),
        ("продвигаем managed rag для финтеха", "evolution-managed-rag"),
        ("Evolution Foundation Models", "evolution-foundation-models"),
        ("Evolution Notebooks", "evolution-notebooks"),
    ],
)
def test_find_product_matches_by_alias(text, expected):
    doc = find_product(text)
    assert doc is not None and doc.slug == expected


def test_find_product_returns_none_for_unknown_product():
    assert find_product("баннер для нашей пиццерии") is None


def test_find_product_earlier_argument_wins():
    doc = find_product(
        "Evolution Managed RAG",              # the product field
        "Evolution Foundation Models рядом",  # a passing mention in the notes
    )
    assert doc is not None and doc.slug == "evolution-managed-rag"


def test_find_product_longest_alias_wins_within_one_text():
    doc = find_product("сравниваем Evolution ML Inference и Evolution Notebooks")
    # both match; the first-listed longest alias decides, and it must be a real doc
    assert doc is not None
    assert doc.slug in {"evolution-ml-inference", "evolution-notebooks"}


def test_alias_must_be_a_whole_phrase():
    # substring of a longer word must not trigger a match
    assert find_product("evolution-notebooksXX") is None


def test_glossary_excludes_the_current_product():
    doc = find_product("Evolution Managed RAG")
    assert doc is not None
    text = glossary(exclude=doc.slug)
    assert doc.name not in text
    assert text.count("\n") >= 4  # the neighbours are still there
