"""Cloud.ru Evolution product knowledge base — lookup + prompt slicing.

The pipeline's weakest point used to be that the LLM only ever saw the product
NAME. "Evolution Managed RAG" means nothing to a model that was never told
what it is, so slogans came out generic or, worse, invented capabilities.

``config/knowledge/`` holds a vendored markdown copy of the internal product
pack (regenerate with ``scripts/build_knowledge.py``). Each document has the
same three-block shape, and different nodes need different blocks:

  - Блок 1–2 (what it is, capabilities, problems solved, scenarios, industries)
    → the message generator, as factual anchors it must not contradict
  - Блок 3 (audiences and what each one wants)
    → derive_persona, so the persona is grounded in real segments

A compact glossary of ALL products is always injected: the documents
cross-reference each other ("Managed RAG передает контекст в Foundation
Models"), so a run about one product still needs the one-line meaning of its
neighbours.

Lookup is deliberately conservative: an alias must appear as a whole phrase in
the product text. No match → the caller gets ``None`` and the pipeline runs
exactly as it did before the KB existed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_KB_DIR = Path(__file__).resolve().parent.parent / "config" / "knowledge"
_INDEX_FILE = _KB_DIR / "index.json"

_BLOCK_RE = re.compile(r"^## Блок (\d+)\.", re.M)


@dataclass(frozen=True)
class ProductDoc:
    slug: str
    name: str
    aliases: tuple[str, ...]
    tagline: str
    body: str
    version: int = 1

    def block(self, number: int) -> str:
        """Text of ``## Блок <number>. …`` including its heading, or ``""``."""
        bounds = [(m.group(1), m.start()) for m in _BLOCK_RE.finditer(self.body)]
        for i, (num, start) in enumerate(bounds):
            if int(num) != number:
                continue
            end = bounds[i + 1][1] if i + 1 < len(bounds) else len(self.body)
            return self.body[start:end].strip()
        return ""

    @property
    def product_facts(self) -> str:
        """Блоки 1–2: what it is, what it can do, what it solves."""
        return "\n\n".join(p for p in (self.block(1), self.block(2)) if p)

    @property
    def audiences(self) -> str:
        """Блок 3: real audience segments and what each one wants."""
        return self.block(3)


@lru_cache(maxsize=1)
def load_catalog() -> tuple[ProductDoc, ...]:
    """All KB documents, longest alias first so lookup prefers exact names."""
    if not _INDEX_FILE.exists():
        log.warning("knowledge_index_missing", path=str(_INDEX_FILE))
        return ()
    entries = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    docs = []
    for entry in entries:
        path = _KB_DIR / entry["file"]
        if not path.exists():
            log.warning("knowledge_doc_missing", file=entry["file"])
            continue
        docs.append(
            ProductDoc(
                slug=entry["slug"],
                name=entry["name"],
                aliases=tuple(entry.get("aliases") or [entry["name"]]),
                tagline=entry.get("tagline", ""),
                body=path.read_text(encoding="utf-8"),
            )
        )
    return tuple(docs)


def find_product(*texts: str | None) -> ProductDoc | None:
    """Best-matching KB document for the given free text, or ``None``.

    Earlier arguments win, and within one text the longest alias wins — so a
    product field saying "Evolution Managed RAG" beats a note that merely
    mentions "Foundation Models" in passing.
    """
    catalog = load_catalog()
    if not catalog:
        return None
    for text in texts:
        if not text:
            continue
        hay = text.lower()
        best: tuple[int, ProductDoc] | None = None
        for doc in catalog:
            for alias in doc.aliases:
                if not _contains_phrase(hay, alias.lower()):
                    continue
                if best is None or len(alias) > best[0]:
                    best = (len(alias), doc)
        if best is not None:
            return best[1]
    return None


def glossary(exclude: str | None = None) -> str:
    """One line per product — the shared vocabulary of the Evolution platform."""
    lines = [
        f"- {doc.name}: {doc.tagline}"
        for doc in load_catalog()
        if doc.slug != exclude and doc.tagline
    ]
    return "\n".join(lines)


def _contains_phrase(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None
