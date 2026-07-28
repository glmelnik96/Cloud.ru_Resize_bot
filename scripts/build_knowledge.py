"""Convert the product knowledge base (.docx) into vendored markdown + index.

The source .docx pack lives outside the repo (Глеб's Downloads); the VM has no
access to it, so the KB is converted once and the RESULT is committed under
``config/knowledge/``. Re-run this script whenever the source pack changes:

    python scripts/build_knowledge.py "C:/Users/Глеб/Downloads/База"

Each source document has the same three-block shape:

    <Product name>
    Блок 1. Описание продукта      -> what it is, capabilities, added value
    Блок 2. Описание задач         -> problems solved, scenarios, industries
    Блок 3. Описание аудитории     -> audiences 1..4, "что хотят / что предлагаем"

The markdown keeps that shape with stable ``## Блок N.`` headings so
``graph/knowledge.py`` can slice a document into the parts a given node needs
(Блок 3 for the persona, Блоки 1–2 for the message generator).

Only the stdlib is used — a .docx is a zip with an XML part, and we need plain
text, not layout.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "config" / "knowledge"

_PARA_RE = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_BR_RE = re.compile(r"<w:(?:br|cr)[^>]*/>")
_TAB_RE = re.compile(r"<w:tab[^>]*/>")
_TOKEN_RE = re.compile(r"<w:t[^>]*>(.*?)</w:t>|(\n)", re.S)
_BLOCK_RE = re.compile(r"^Блок\s+(\d+)\.\s*(.+)$")

_XML_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


def _unescape(text: str) -> str:
    for entity, char in _XML_ENTITIES.items():
        text = text.replace(entity, char)
    return text


def _paragraphs(docx: Path) -> list[str]:
    """Plain-text paragraphs; soft line breaks inside a paragraph are kept."""
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    out: list[str] = []
    for para in _PARA_RE.findall(xml):
        para = _BR_RE.sub("\n", para)
        para = _TAB_RE.sub(" ", para)
        text = "".join(a or b for a, b in _TOKEN_RE.findall(para))
        text = _unescape(text).replace("\xa0", " ")
        lines = [ln.strip() for ln in text.split("\n")]
        text = "\n".join(ln for ln in lines if ln)
        if text:
            out.append(text)
    return out


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _aliases(name: str) -> list[str]:
    """Match keys for free-text product lookup, longest first.

    The full name is the reliable key; the ``Evolution``-less short form is how
    people actually write it in a brief ("нужен баннер под Managed RAG").
    Single generic words ("RAG", "Agents") are deliberately NOT aliases —
    they collide across documents and with ordinary prose.
    """
    out = [name]
    short = re.sub(r"^Evolution\s+", "", name).strip()
    if short and short != name:
        out.append(short)
    return sorted(set(out), key=len, reverse=True)


def _first_sentence(text: str) -> str:
    body = text.split("\n", 1)[-1].strip()
    match = re.search(r"^(.+?[.!?])(\s|$)", body, re.S)
    return (match.group(1) if match else body).strip()


def _to_markdown(name: str, paragraphs: list[str]) -> tuple[str, str]:
    """Return (markdown, tagline)."""
    lines = [f"# {name}", ""]
    tagline = ""
    for para in paragraphs[1:]:
        block = _BLOCK_RE.match(para.split("\n", 1)[0])
        if block:
            lines += [f"## Блок {block.group(1)}. {block.group(2)}", ""]
            continue
        head, _, body = para.partition("\n")
        if body:
            if not tagline:
                tagline = _first_sentence(para)
            lines += [f"### {head}", "", body.strip(), ""]
        else:
            lines += [para.strip(), ""]
    markdown = "\n".join(lines).rstrip() + "\n"
    return markdown, tagline


def build(source_dir: Path) -> list[dict]:
    docs = sorted(source_dir.glob("*.docx"))
    if not docs:
        raise SystemExit(f"no .docx found in {source_dir}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    for docx in docs:
        paragraphs = _paragraphs(docx)
        if not paragraphs:
            raise SystemExit(f"{docx.name}: no text extracted")
        name = paragraphs[0].strip()
        markdown, tagline = _to_markdown(name, paragraphs)
        slug = _slug(name)
        (OUT_DIR / f"{slug}.md").write_text(markdown, encoding="utf-8")
        index.append(
            {
                "slug": slug,
                "name": name,
                "aliases": _aliases(name),
                "tagline": tagline,
                "file": f"{slug}.md",
            }
        )
        print(f"{docx.name} -> {slug}.md ({len(markdown)} chars)")

    (OUT_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"index.json -> {len(index)} products")
    return index


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/build_knowledge.py <source_dir>")
    build(Path(sys.argv[1]))
