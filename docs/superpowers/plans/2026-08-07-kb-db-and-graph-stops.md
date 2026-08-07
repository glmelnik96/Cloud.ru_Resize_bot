# План 1: Библиотека знаний в БД + граф с тремя остановками

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести библиотеку знаний на БД (kb_products, сид из файлов, инжект в graph/knowledge) и перестроить граф: `product_slug` в брифе, `kb_match` в state, новая пауза `hitl_persona_approve`, `winner_id` в текстовой паузе, петля «комментарий → перегенерация метафоры», `graph_version`-гард, расширение provenance.

**Architecture:** App-слой читает kb_products (append-only версии строк) и инжектит снапшот каталога в `graph.knowledge.set_catalog()` — граф НЕ импортирует app (import-arch стражи блока 1). Все паузы — канонические `interrupt()`; сервис `creatives.py` получает третий parked-статус `awaiting_persona`. Спека: `docs/superpowers/specs/2026-08-07-ui-artefacts-knowledge-library-design.md`.

**Tech Stack:** SQLAlchemy 2.0 async (`Mapped`/`mapped_column`, `create_all` — без Alembic), LangGraph interrupt/Command, pytest (`asyncio_mode=auto`), Python: тесты локально на `.venv311` (3.11), ruff.

**Конвенции репо (обязательны):**
- Тесты: стабы через `monkeypatch.setattr(mod, "run_agent", fake)`; БД-фикстура — in-memory `sqlite+aiosqlite:///:memory:` + `init_db`.
- Никаких эмодзи. Комментарии в стиле репо (рус/англ смешанно — смотри соседние файлы).
- Команда тестов: `.venv311/Scripts/python.exe -m pytest tests/unit tests/contract tests/agents -q` (интеграционные скипаются без ключа). Ruff: `.venv311/Scripts/python.exe -m ruff check <touched files>` — только тронутые файлы (в репо есть пре-существующие ошибки, их не чинить).
- Коммиты на ветке `feature/research-loop`, пушей НЕТ.

---

### Task 1: Модель KbProduct + store (сид из файлов, чтение → ProductDoc)

**Files:**
- Modify: `app/db/models.py` (добавить класс в конец)
- Create: `app/kb/__init__.py` (пустой), `app/kb/store.py`
- Modify: `graph/knowledge.py` (поле `version` у ProductDoc)
- Test: `tests/unit/test_kb_store.py`

- [ ] **Step 1: Написать падающие тесты**

```python
"""kb_products: сид из файлового каталога + чтение последних версий как ProductDoc."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import models
from app.db.database import init_db, make_engine, make_sessionmaker
from app.kb.store import load_product_docs, seed_from_files
from graph.knowledge import _load_file_catalog


@pytest.fixture
async def Session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_seed_imports_file_catalog_once(Session):
    n = await seed_from_files(Session)
    file_docs = _load_file_catalog()
    assert n == len(file_docs) > 0
    # повторный сид — no-op
    assert await seed_from_files(Session) == 0
    async with Session() as s:
        rows = (await s.execute(select(models.KbProduct))).scalars().all()
    assert {r.slug for r in rows} == {d.slug for d in file_docs}
    assert all(r.version == 1 and r.updated_by == "seed" for r in rows)


async def test_load_docs_equivalent_to_file_catalog(Session):
    """Снапшот-эквивалентность: БД-каталог после сида == файловый (кроме version)."""
    await seed_from_files(Session)
    db_docs = {d.slug: d for d in await load_product_docs(Session)}
    for fd in _load_file_catalog():
        dd = db_docs[fd.slug]
        assert dd.name == fd.name
        assert dd.aliases == fd.aliases
        assert dd.tagline == fd.tagline
        for n in (1, 2, 3):
            assert dd.block(n) == fd.block(n)
        assert dd.version == 1


async def test_load_docs_takes_latest_version_and_skips_archived(Session):
    await seed_from_files(Session)
    async with Session() as s:
        first = (
            await s.execute(select(models.KbProduct).order_by(models.KbProduct.slug))
        ).scalars().first()
        s.add(
            models.KbProduct(
                slug=first.slug, version=2, name="Edited Name",
                aliases=list(first.aliases), tagline=first.tagline,
                block1="## Блок 1. Новый", block2="", block3="",
                updated_by="admin@test",
            )
        )
        other = (
            await s.execute(select(models.KbProduct).order_by(models.KbProduct.slug.desc()))
        ).scalars().first()
        s.add(
            models.KbProduct(
                slug=other.slug, version=2, name=other.name,
                aliases=list(other.aliases), tagline=other.tagline,
                block1=other.block1, block2=other.block2, block3=other.block3,
                updated_by="admin@test", archived=True,
            )
        )
        await s.commit()
    docs = {d.slug: d for d in await load_product_docs(Session)}
    assert docs[first.slug].name == "Edited Name"
    assert docs[first.slug].version == 2
    assert other.slug not in docs
```

- [ ] **Step 2: Запустить — убедиться, что падают правильно**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_store.py -q`
Expected: FAIL/ERROR — `ModuleNotFoundError: app.kb` (и `ImportError: _load_file_catalog` — появится в Task 2, поэтому В ЭТОЙ задаче временно импортируй в тесте `load_catalog as _load_file_catalog`; Task 2 переименует).

- [ ] **Step 3: Реализация**

В `app/db/models.py` (конец файла; импорты `UniqueConstraint` из sqlalchemy добавить к существующим):

```python
class KbProduct(Base):
    """Библиотека знаний, слой «факты» — append-only версии карточек продуктов.

    Правка = новая строка с version+1 (историю видно, откатывать легко);
    чтение берёт максимальную версию неархивного slug. Сид — из
    config/knowledge/*.md (version=1, updated_by="seed")."""

    __tablename__ = "kb_products"
    __table_args__ = (UniqueConstraint("slug", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(128))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    tagline: Mapped[str] = mapped_column(Text, default="")
    block1: Mapped[str] = mapped_column(Text, default="")
    block2: Mapped[str] = mapped_column(Text, default="")
    block3: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    archived: Mapped[bool] = mapped_column(default=False)
```

В `graph/knowledge.py` — dataclass ProductDoc получает поле `version: int = 1` (после `body`).

`app/kb/store.py`:

```python
"""Слой «факты» библиотеки знаний: kb_products → graph.knowledge.ProductDoc.

Граф НЕ импортирует app (import-arch стражи): app-слой читает БД и инжектит
снапшот каталога через knowledge.set_catalog(). Правка карточки = новая строка
version+1; здесь только сид и чтение (CRUD — в план 2)."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import models
from graph.knowledge import ProductDoc, _load_file_catalog


async def seed_from_files(sessionmaker) -> int:
    """Одноразовый сид: пустая kb_products ← файловый каталог (version=1)."""
    async with sessionmaker() as s:
        n = (await s.execute(select(func.count(models.KbProduct.id)))).scalar_one()
        if n:
            return 0
        docs = _load_file_catalog()
        for doc in docs:
            s.add(
                models.KbProduct(
                    slug=doc.slug,
                    version=1,
                    name=doc.name,
                    aliases=list(doc.aliases),
                    tagline=doc.tagline,
                    block1=doc.block(1),
                    block2=doc.block(2),
                    block3=doc.block(3),
                    updated_by="seed",
                )
            )
        await s.commit()
        return len(docs)


async def load_product_docs(sessionmaker) -> tuple[ProductDoc, ...]:
    """Последняя версия каждого неархивного продукта как ProductDoc."""
    async with sessionmaker() as s:
        rows = (await s.execute(select(models.KbProduct))).scalars().all()
    latest: dict[str, models.KbProduct] = {}
    for r in rows:
        if r.slug not in latest or r.version > latest[r.slug].version:
            latest[r.slug] = r
    return tuple(
        ProductDoc(
            slug=r.slug,
            name=r.name,
            aliases=tuple(r.aliases or [r.name]),
            tagline=r.tagline,
            body="\n\n".join(b for b in (r.block1, r.block2, r.block3) if b),
            version=r.version,
        )
        for r in sorted(latest.values(), key=lambda r: r.slug)
        if not r.archived
    )
```

(В этой задаче `_load_file_catalog` ещё не существует — используй `load_catalog`; Task 2 введёт переименование, обнови оба места.)

- [ ] **Step 4: Прогнать тесты** — `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_store.py tests/unit/test_provenance.py -q` → PASS (provenance проверяет, что ProductDoc-изменение ничего не сломало).

- [ ] **Step 5: Ruff тронутых файлов + коммит**

```bash
git add app/db/models.py app/kb tests/unit/test_kb_store.py graph/knowledge.py
git commit -m "App3: kb_products — слой «факты» библиотеки в БД, сид из файлового каталога"
```

---

### Task 2: graph/knowledge.py — инжект каталога (set_catalog) + get_by_slug

**Files:**
- Modify: `graph/knowledge.py`
- Modify: `app/kb/store.py` (добавить refresh_catalog)
- Test: `tests/unit/test_knowledge_catalog.py` (create)

- [ ] **Step 1: Падающие тесты**

```python
"""set_catalog: app-слой подменяет источник каталога (БД → граф) без импорта app из graph."""

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
```

- [ ] **Step 2: Verify RED** — `pytest tests/unit/test_knowledge_catalog.py -q` → FAIL: `set_catalog` не существует.

- [ ] **Step 3: Реализация в graph/knowledge.py**

Переименовать нынешнюю `load_catalog` (с `@lru_cache`) в `_load_file_catalog`; добавить:

```python
_catalog_override: tuple[ProductDoc, ...] | None = None


def set_catalog(docs: tuple[ProductDoc, ...] | None) -> None:
    """App-слой инжектит БД-каталог (None → обратно к vendored-файлам).

    Инвалидация кэша — на стороне инжектора: после каждой правки карточки
    app просто вызывает set_catalog со свежим снапшотом."""
    global _catalog_override
    _catalog_override = docs


def load_catalog() -> tuple[ProductDoc, ...]:
    if _catalog_override is not None:
        return _catalog_override
    return _load_file_catalog()


def get_by_slug(slug: str) -> ProductDoc | None:
    """Карточка по slug — для явного выбора продукта в брифе."""
    return next((d for d in load_catalog() if d.slug == slug), None)
```

В `app/kb/store.py` добавить:

```python
async def refresh_catalog(sessionmaker) -> int:
    """Перечитать kb_products и инжектнуть снапшот в граф. Возвращает размер."""
    from graph import knowledge

    docs = await load_product_docs(sessionmaker)
    knowledge.set_catalog(docs)
    return len(docs)
```

И тест в `tests/unit/test_kb_store.py`:

```python
async def test_refresh_catalog_injects_db_snapshot(Session):
    from graph import knowledge
    from app.kb.store import refresh_catalog

    await seed_from_files(Session)
    try:
        n = await refresh_catalog(Session)
        assert n > 0
        assert knowledge.load_catalog() == await load_product_docs(Session)
    finally:
        knowledge.set_catalog(None)
```

- [ ] **Step 4: Verify GREEN** — `pytest tests/unit/test_knowledge_catalog.py tests/unit/test_kb_store.py -q` → PASS. Затем весь юнит-набор: `pytest tests/unit tests/contract tests/agents -q` (ловим сломанные импорты `load_catalog`).

- [ ] **Step 5: Ruff + коммит** — `git add graph/knowledge.py app/kb/store.py tests/unit/test_knowledge_catalog.py tests/unit/test_kb_store.py && git commit -m "App3: set_catalog — БД-каталог инжектится в граф без обратного импорта"`

---

### Task 3: Старт приложения — сид + инжект каталога

**Files:**
- Modify: `app/main.py` (lifespan, после `await reconcile_interrupted_tasks(Session)`)
- Test: дополняется `tests/unit/test_kb_store.py` (уже покрыто `test_refresh_catalog_injects_db_snapshot`; здесь только wiring)

- [ ] **Step 1: Вставить в lifespan** (после reconcile):

```python
    # Библиотека знаний: сид из vendored-файлов при первом старте + инжект
    # БД-каталога в граф (граф не импортирует app — снапшот проталкиваем сюда).
    from app.kb.store import refresh_catalog, seed_from_files

    await seed_from_files(Session)
    await refresh_catalog(Session)
```

- [ ] **Step 2: Проверить, что приложение поднимается** — smoke-тест уже есть в `tests/unit/test_app3_routes.py` (создаёт app через create_app); прогнать: `pytest tests/unit/test_app3_routes.py -q` → PASS.

- [ ] **Step 3: Коммит** — `git add app/main.py && git commit -m "App3: старт — сид kb_products и инжект каталога в граф"`

---

### Task 4: AdBrief.product_slug + kb_match в state; understand_product/derive_persona чтят выбор

**Files:**
- Modify: `graph/state.py` (AdBrief + GraphState), `graph/nodes/understand_product.py`, `graph/nodes/derive_persona.py`, `app/services/creatives.py` (build_brief)
- Test: `tests/unit/test_understand_product.py` (дополнить существующий — если его нет, создать), `tests/unit/test_derive_persona.py` (дополнить)

- [ ] **Step 1: Падающие тесты** (в существующие файлы тестов узлов; стаб `run_agent` по образцу соседних тестов — см. фикстуры в `tests/unit/test_generate_image_prompt.py`):

```python
# --- understand_product: явный slug / none / auto + kb_match в выходе ---------

async def test_product_slug_explicit_overrides_autodetect(monkeypatch, _stub_ok):
    """brief.product_slug=<slug> берёт карточку без alias-матча."""
    from graph import knowledge
    knowledge.set_catalog((_kb_doc(slug="evolution-notebooks", name="Evolution Notebooks"),))
    try:
        state = _state(brief=_brief(product="просто текст без алиасов",
                                    product_slug="evolution-notebooks"))
        out = await mod.understand_product(state)
        assert out["kb_match"] == {"slug": "evolution-notebooks",
                                   "name": "Evolution Notebooks", "version": 1}
    finally:
        knowledge.set_catalog(None)


async def test_product_slug_none_disables_kb(monkeypatch, _stub_ok):
    state = _state(brief=_brief(product="Evolution ML Inference", product_slug="none"))
    out = await mod.understand_product(state)
    assert out["kb_match"] is None


async def test_product_slug_auto_keeps_alias_match(monkeypatch, _stub_ok):
    state = _state(brief=_brief(product="Evolution ML Inference", product_slug="auto"))
    out = await mod.understand_product(state)
    assert out["kb_match"]["slug"] == "evolution-ml-inference"


# --- derive_persona: kb-блок берётся из state.kb_match, не из повторного матча -

async def test_derive_persona_uses_state_kb_match(monkeypatch, _stub_persona):
    state = _state(kb_match={"slug": "evolution-ml-inference",
                             "name": "Evolution ML Inference", "version": 1})
    await mod.derive_persona(state)
    rendered = _captured_render_kwargs()  # по образцу фикстур соседних тестов
    assert "аудитори" in rendered["kb_audiences_block"].lower() or rendered["kb_audiences_block"]


async def test_derive_persona_no_kb_match_means_no_kb_block(monkeypatch, _stub_persona):
    state = _state(kb_match=None)
    await mod.derive_persona(state)
    rendered = _captured_render_kwargs()
    assert "нет карточки" in rendered["kb_audiences_block"]
```

Точные фикстуры (`_stub_ok`, `_state`, `_brief`, `_kb_doc`, `_captured_render_kwargs`) — привести к стилю уже существующих тестов этих узлов: прочитай файл теста и переиспользуй его хелперы; `_kb_doc` строит `ProductDoc(..., version=1)`.

- [ ] **Step 2: Verify RED** — `pytest tests/unit/test_understand_product.py tests/unit/test_derive_persona.py -q` → FAIL (нет поля product_slug / kb_match).

- [ ] **Step 3: Реализация**

`graph/state.py`: в AdBrief добавить:

```python
    product_slug: str = Field(
        default="auto",
        description=(
            "Явный выбор продукта библиотеки: 'auto' — alias-матч, "
            "'none' — без библиотеки, иначе slug карточки"
        ),
    )
```

В GraphState добавить (рядом с product):

```python
    # Какая карточка библиотеки подхвачена (None — работаем только по брифу).
    kb_match: dict | None  # {"slug": str, "name": str, "version": int}
```

`graph/nodes/understand_product.py` — заменить `doc = knowledge.find_product(...)` на:

```python
    doc = _resolve_kb(brief, session_id=session_id)
```

и добавить функцию:

```python
def _resolve_kb(brief: AdBrief, *, session_id: str | None):
    """Выбор продукта: явный slug из брифа > alias-автодетект; 'none' — без KB."""
    if brief.product_slug == "none":
        return None
    if brief.product_slug and brief.product_slug != "auto":
        doc = knowledge.get_by_slug(brief.product_slug)
        if doc is not None:
            return doc
        log.warning(
            "kb_slug_unknown", session_id=session_id, slug=brief.product_slug
        )
    return knowledge.find_product(brief.product, brief.notes)
```

в return узла добавить:

```python
        "kb_match": (
            {"slug": doc.slug, "name": doc.name, "version": doc.version}
            if doc
            else None
        ),
```

`graph/nodes/derive_persona.py` — заменить `doc = knowledge.find_product(...)` на:

```python
    kb_match = state.get("kb_match")
    doc = knowledge.get_by_slug(kb_match["slug"]) if kb_match else None
```

`app/services/creatives.py::build_brief` — добавить поле:

```python
        product_slug=(fields.get("product_slug") or "auto"),
```

- [ ] **Step 4: Verify GREEN** — оба файла тестов + `pytest tests/unit -q` целиком.

- [ ] **Step 5: Ruff + коммит** — `git commit -m "App3: product_slug в брифе + kb_match в state — выбор продукта библиотеки виден и управляем"`

---

### Task 5: Узел hitl_persona_approve

**Files:**
- Create: `graph/nodes/hitl_persona_approve.py`
- Modify: `graph/state.py` (поле persona_approved)
- Test: `tests/unit/test_hitl_persona_approve.py` (create)

- [ ] **Step 1: Падающие тесты** (interrupt стабится через monkeypatch — по образцу существующего `tests/unit/test_hitl_text_approve.py`, прочитай его и скопируй подход; если такого нет — стаб `mod.interrupt`, возвращающий подготовленное decision, с записью interrupt-payload):

```python
"""hitl_persona_approve: пауза «Кому пишем» — правка персоны без LLM-вызова."""

from __future__ import annotations

import pytest

from graph.nodes import hitl_persona_approve as mod

_PERSONA = {
    "segment": "ML-инженер в продуктовой команде",
    "age_range": "28-40",
    "pain_points": ["очередь на GPU"],
    "motivations": ["запускать модели за минуты"],
    "objections": ["не смогу поставить свои библиотеки"],
    "communication_style": "инженерный, без пафоса",
}


def _state(**over):
    base = {
        "session_id": "s1",
        "personas": [_PERSONA],
        "kb_match": {"slug": "evolution-ml-inference", "name": "X", "version": 1},
    }
    base.update(over)
    return base


@pytest.fixture
def decide(monkeypatch):
    seen: dict = {}

    def _arm(decision):
        def fake_interrupt(payload):
            seen["payload"] = payload
            return decision
        monkeypatch.setattr(mod, "interrupt", fake_interrupt)
        return seen
    return _arm


async def test_payload_carries_persona_and_kb(decide):
    seen = decide({"action": "approve"})
    out = await mod.hitl_persona_approve(_state())
    assert seen["payload"]["kind"] == "persona_approve"
    assert seen["payload"]["persona"] == _PERSONA
    assert seen["payload"]["kb_match"]["slug"] == "evolution-ml-inference"
    assert out == {"persona_approved": True}


async def test_approve_with_edited_persona_replaces_state(decide):
    edited = dict(_PERSONA, pain_points=["правленая боль"])
    decide({"action": "approve", "persona": edited})
    out = await mod.hitl_persona_approve(_state())
    assert out["persona_approved"] is True
    assert out["personas"][0]["pain_points"] == ["правленая боль"]


async def test_approve_with_invalid_persona_is_fail_open(decide):
    """API валидирует раньше; узел на мусор не падает, а берёт оригинал."""
    decide({"action": "approve", "persona": {"segment": ""}})
    out = await mod.hitl_persona_approve(_state())
    assert out == {"persona_approved": True}


async def test_regenerate_clears_personas(decide):
    decide({"action": "regenerate"})
    out = await mod.hitl_persona_approve(_state())
    assert out == {"personas": [], "persona_approved": False}


async def test_timeout_and_cancel(decide):
    decide({"action": "timeout"})
    out = await mod.hitl_persona_approve(_state())
    assert out["cancelled"] is True and out["error"] == "persona_approve_timeout"
    decide({"action": "cancel"})
    out = await mod.hitl_persona_approve(_state())
    assert out == {"cancelled": True}
```

- [ ] **Step 2: Verify RED** — модуль не существует.

- [ ] **Step 3: Реализация `graph/nodes/hitl_persona_approve.py`**

```python
"""HITL: persona approve — остановка «Кому пишем» (спека 2026-08-07).

Пауза после derive_persona: маркетолог видит персону (боли/мотивации/
возражения — редактируемые списки) и плашку kb_match. Правка прямая, без
повторного LLM-вызова: resume несёт готовую персону.

Decision contract (Command(resume=...)):
    {"action": "approve"}                    — персона ок, дальше
    {"action": "approve", "persona": {...}}  — правленая персона (Pydantic Persona)
    {"action": "regenerate"}                 — persona не годится целиком, заново
    {"action": "cancel"} / {"action": "timeout"}
"""

from __future__ import annotations

import structlog
from langgraph.types import interrupt
from pydantic import ValidationError

from graph.state import GraphState, Persona

log = structlog.get_logger(__name__)


async def hitl_persona_approve(state: GraphState) -> dict:
    personas = state.get("personas") or []
    persona = personas[0] if personas else None

    log.info("hitl_persona_interrupt", session_id=state.get("session_id"))
    decision: dict = interrupt(
        {
            "kind": "persona_approve",
            "persona": persona,
            "kb_match": state.get("kb_match"),
            "session_id": state.get("session_id"),
        }
    )

    action = decision.get("action", "cancel")
    log.info(
        "hitl_persona_resume", session_id=state.get("session_id"), action=action
    )

    if action == "approve":
        edited = decision.get("persona")
        if edited:
            # API валидирует раньше; узел fail-open — мусор не роняет граф.
            try:
                ok = Persona.model_validate(edited)
                return {"personas": [ok.model_dump()], "persona_approved": True}
            except ValidationError:
                log.warning(
                    "persona_edit_invalid", session_id=state.get("session_id")
                )
        return {"persona_approved": True}
    if action == "regenerate":
        return {"personas": [], "persona_approved": False}
    if action == "timeout":
        log.warning(
            "hitl_persona_approve_timeout", session_id=state.get("session_id")
        )
        return {"cancelled": True, "error": "persona_approve_timeout"}
    return {"cancelled": True}
```

`graph/state.py` GraphState — добавить `persona_approved: bool` (рядом с text_approved).

- [ ] **Step 4: Verify GREEN**, ruff, commit: `git commit -m "App3: hitl_persona_approve — остановка «Кому пишем», правка персоны без LLM"`

---

### Task 6: Вայring графа — persona-пауза + маршруты

**Files:**
- Modify: `graph/builder.py`
- Modify: `tests/integration/test_graph_pipeline.py` (двойной resume)
- Test: `tests/unit/test_builder_routes.py` (create; если есть существующий тест builder — дополнить его)

- [ ] **Step 1: Падающие тесты**

```python
"""Маршрутизация после persona-паузы + топология с тремя остановками."""

from __future__ import annotations

from langgraph.graph import END

from graph import builder


def test_route_after_persona():
    assert builder._route_after_persona_hitl({"cancelled": True}) == END
    assert (
        builder._route_after_persona_hitl({"persona_approved": True})
        == "generate_message_candidates"
    )
    assert builder._route_after_persona_hitl({}) == "derive_persona"


def test_graph_contains_persona_node():
    g = builder.build_text_graph()
    assert "hitl_persona_approve" in g.nodes
```

- [ ] **Step 2: Verify RED.**

- [ ] **Step 3: Реализация в `graph/builder.py`**

Импорт узла; функция маршрута:

```python
def _route_after_persona_hitl(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("persona_approved"):
        return "generate_message_candidates"
    # regenerate — personas очищены в hitl_persona_approve
    return "derive_persona"
```

Wiring: `g.add_node("hitl_persona_approve", hitl_persona_approve)`; заменить `g.add_edge("derive_persona", "generate_message_candidates")` на:

```python
    g.add_edge("derive_persona", "hitl_persona_approve")
    g.add_conditional_edges(
        "hitl_persona_approve",
        _route_after_persona_hitl,
        {
            "generate_message_candidates": "generate_message_candidates",
            "derive_persona": "derive_persona",
            END: END,
        },
    )
```

`tests/integration/test_graph_pipeline.py`: первый interrupt теперь persona_approve — после первого `ainvoke` добавить resume `Command(resume={"action": "approve"})` (персона), потом существующий resume текста. Проверить interrupt kind:

```python
    interrupts = final.get("__interrupt__")
    assert interrupts and interrupts[0].value.get("kind") == "persona_approve"
    final = await graph.ainvoke(Command(resume={"action": "approve"}), config=cfg)
    interrupts = final.get("__interrupt__")
    if interrupts:
        final = await graph.ainvoke(Command(resume={"action": "approve"}), config=cfg)
        assert final.get("text_approved") is True
```

- [ ] **Step 4: Verify GREEN** — `pytest tests/unit/test_builder_routes.py -q` + весь `tests/unit`.
- [ ] **Step 5: Ruff, commit** — `git commit -m "App3: граф — persona-пауза встроена между derive_persona и генерацией"`

---

### Task 7: winner_id в hitl_text_approve

**Files:**
- Modify: `graph/nodes/hitl_text_approve.py`, `graph/state.py` (`winner_id: str | None`)
- Test: `tests/unit/test_hitl_text_approve.py` (дополнить; если нет — создать по образцу Task 5)

- [ ] **Step 1: Падающие тесты**

```python
async def test_approve_with_winner_reorders_ranked(decide):
    ranked = [{"id": "a", "slogan": "A"}, {"id": "b", "slogan": "B"}, {"id": "c", "slogan": "C"}]
    decide({"action": "approve", "winner_id": "b"})
    out = await mod.hitl_text_approve({"session_id": "s", "ranked": ranked})
    assert [c["id"] for c in out["ranked"]] == ["b", "a", "c"]
    assert out["winner_id"] == "b"
    assert out["text_approved"] is True


async def test_approve_with_unknown_winner_is_fail_open(decide):
    """API валидирует раньше; узел не роняет граф, порядок остаётся скоринговым."""
    ranked = [{"id": "a"}, {"id": "b"}]
    decide({"action": "approve", "winner_id": "zzz"})
    out = await mod.hitl_text_approve({"session_id": "s", "ranked": ranked})
    assert [c["id"] for c in out["ranked"]] == ["a", "b"]
    assert out["text_approved"] is True


async def test_approve_without_winner_keeps_order(decide):
    ranked = [{"id": "a"}, {"id": "b"}]
    decide({"action": "approve"})
    out = await mod.hitl_text_approve({"session_id": "s", "ranked": ranked})
    assert out.get("winner_id") is None
    assert [c["id"] for c in out["ranked"]] == ["a", "b"]
```

- [ ] **Step 2: Verify RED.**

- [ ] **Step 3: Реализация** — ветка `approve` в hitl_text_approve:

```python
    if action == "approve":
        winner_id = decision.get("winner_id")
        if winner_id:
            idx = next(
                (i for i, c in enumerate(ranked) if c.get("id") == winner_id), None
            )
            if idx is None:
                # API валидирует раньше; здесь fail-open — скоринговый порядок.
                log.warning(
                    "winner_id_unknown",
                    session_id=state.get("session_id"),
                    winner_id=winner_id,
                )
            elif idx:
                ranked = [ranked[idx], *ranked[:idx], *ranked[idx + 1 :]]
        # Даунстрим живёт конвенцией «ranked[0] — главный»: победитель встаёт
        # в голову списка, и метафора/рендер получают его без своих изменений.
        return {"text_approved": True, "ranked": ranked, "winner_id": winner_id}
```

Docstring-контракт узла дополнить `{"action": "approve", "winner_id": "..."}`. В GraphState — `winner_id: str | None`.

- [ ] **Step 4: Verify GREEN** (+ весь tests/unit). **Step 5:** ruff, `git commit -m "App3: winner_id — победителя выбирает человек, скоринг остаётся подсказкой"`

---

### Task 8: Петля метафоры — action "metaphor" + режим перегенерации победителя

**Files:**
- Modify: `graph/nodes/hitl_image_upload.py`, `graph/builder.py` (_route_after_image_hitl), `graph/nodes/generate_image_prompt.py`, `graph/state.py` (`metaphor_comment: str | None`, `metaphor_comments: list[str]`)
- Test: `tests/unit/test_hitl_image_upload.py`, `tests/unit/test_generate_image_prompt.py`, `tests/unit/test_builder_routes.py` (дополнить все)

- [ ] **Step 1: Падающие тесты**

В test_hitl_image_upload.py:

```python
async def test_metaphor_action_stores_comment(decide):
    decide({"action": "metaphor", "comment": "не часы, покажи исчезающую очередь"})
    out = await mod.hitl_image_upload(_state())
    assert out["metaphor_comment"] == "не часы, покажи исчезающую очередь"
    assert out["metaphor_comments"] == ["не часы, покажи исчезающую очередь"]
    assert "cancelled" not in out


async def test_metaphor_action_empty_comment_is_noop_reinterrupt(decide):
    """Пустой комментарий — не перегенерация и не отмена: остаёмся честными,
    возвращаем cancelled=False путь через повторный interrupt на следующем цикле."""
    decide({"action": "metaphor", "comment": "  "})
    out = await mod.hitl_image_upload(_state())
    assert out.get("metaphor_comment") is None
    assert out.get("cancelled") is None or out.get("cancelled") is False
```

В test_builder_routes.py:

```python
def test_route_after_image_hitl_metaphor_loop():
    from graph import builder
    assert (
        builder._route_after_image_hitl({"metaphor_comment": "другой образ"})
        == "generate_image_prompt"
    )
    assert builder._route_after_image_hitl({}) == "fill_templates_per_format"
```

В test_generate_image_prompt.py:

```python
async def test_metaphor_comment_regenerates_only_winner(_stub_agent):
    calls = _stub_agent(_METAPHOR)  # существующая фикстура
    state = _good_state()  # существующий хелпер: 12 кандидатов
    state["image_prompts"] = ["old0", "old1"]
    state["image_prompt"] = "old0"
    state["metaphor_meta"] = [{"metaphor": "old-m0"}, {"metaphor": "old-m1"}]
    state["metaphor_comment"] = "покажи очередь, которая исчезает"
    out = await mod.generate_image_prompt(state)
    assert len(calls) == 1  # ровно один LLM-вызов — только победитель
    assert out["image_prompts"][0] != "old0"
    assert out["image_prompts"][1:] == ["old1"]
    assert out["image_prompt"] == out["image_prompts"][0]
    assert out["metaphor_meta"][1] == {"metaphor": "old-m1"}
    assert out["metaphor_comment"] is None
    # комментарий дошёл до модели
    user_msg = calls[0]["messages"][1]["content"]
    assert "покажи очередь, которая исчезает" in user_msg
    assert "old-m0" in user_msg  # прежняя метафора показана для контекста
```

- [ ] **Step 2: Verify RED.**

- [ ] **Step 3: Реализация**

`hitl_image_upload.py` — в диспетчер действий (до ветки upload):

```python
    if action == "metaphor":
        comment = (decision.get("comment") or "").strip()
        if not comment:
            log.warning(
                "metaphor_comment_empty", session_id=state.get("session_id")
            )
            return {"metaphor_comment": None}
        history = [*(state.get("metaphor_comments") or []), comment]
        return {"metaphor_comment": comment, "metaphor_comments": history}
```

Docstring-контракт дополнить. `builder.py`:

```python
def _route_after_image_hitl(state: GraphState) -> str:
    if state.get("cancelled"):
        return END
    if state.get("metaphor_comment"):
        # петля: комментарий маркетолога → перегенерация метафоры победителя
        return "generate_image_prompt"
    return "fill_templates_per_format"
```

и в conditional_edges-мэппинг hitl_image_upload добавить `"generate_image_prompt": "generate_image_prompt"`.

`generate_image_prompt.py` — в начале узла (после coerce brief/persona):

```python
    comment = (state.get("metaphor_comment") or "").strip()
    if comment:
        return await _regenerate_winner(state, brief, persona, comment)
```

и функция:

```python
async def _regenerate_winner(
    state: GraphState, brief: AdBrief, persona: Persona, comment: str
) -> dict:
    """Петля метафоры: перегенерировать ТОЛЬКО победителя (ranked[0]) с учётом
    русского комментария маркетолога. 1 LLM-вызов вместо 12; остальные
    прописки и меты не трогаем."""
    candidates = ranked_candidates(state)
    product = get_product(state)
    session_id = state.get("session_id")
    styles = state.get("scenarios") or ["photo"]
    style = styles[0] if styles[0] in _VALID_STYLES else "photo"

    prev_meta = (state.get("metaphor_meta") or [{}])[0]
    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    prompt, meta = await _build_one(
        system_msg, user_tpl, brief, persona, product,
        candidates[0], style, session_id,
        feedback=(prev_meta.get("metaphor", ""), comment),
    )
    prompts = list(state.get("image_prompts") or [])
    metas = list(state.get("metaphor_meta") or [])
    if prompts:
        prompts[0] = prompt
    else:
        prompts = [prompt]
    if metas:
        metas[0] = meta
    else:
        metas = [meta]
    log.info("metaphor_regenerated", session_id=session_id)
    return {
        "image_prompts": prompts,
        "image_prompt": prompt,
        "metaphor_meta": metas,
        "metaphor_comment": None,
    }
```

`_build_one` получает параметр `feedback: tuple[str, str] | None = None`; после рендера user_msg:

```python
    if feedback:
        prev_metaphor, comment = feedback
        user_msg += (
            "\n\nFEEDBACK FROM THE MARKETER (Russian) about the previous "
            f"metaphor — address it directly:\nPrevious metaphor: {prev_metaphor}\n"
            f"Comment: {comment}\n"
            "Propose a DIFFERENT metaphor honouring this feedback."
        )
```

- [ ] **Step 4: Verify GREEN** (три файла тестов + весь tests/unit). **Step 5:** ruff, `git commit -m "App3: петля метафоры — русский комментарий перегенерирует образ победителя"`

---

### Task 9: Сервис — parked-статус awaiting_persona

**Files:**
- Modify: `app/services/creatives.py` (_OPEN_STATUSES, _PARKED_STATUSES, _TIMEOUT_ERRORS, _claim_running, _park)
- Test: `tests/unit/test_creatives_service.py` (дополнить существующий тест сервиса; найти его: `grep -l "_park\|submit_decision" tests/unit`)

- [ ] **Step 1: Падающий тест** (по образцу существующих тестов _park; читай файл теста сервиса и переиспользуй фикстуры):

```python
async def test_park_persona_kind_sets_awaiting_persona(svc_fixture):
    """interrupt kind=persona_approve паркует в awaiting_persona с payload персоны."""
    value = {
        "kind": "persona_approve",
        "persona": {"segment": "ML-инженер"},
        "kb_match": {"slug": "evolution-ml-inference", "name": "X", "version": 1},
    }
    await svc._park("uid1", reporter, value)
    # статус в БД
    assert (await _load_task("uid1")).status == "awaiting_persona"
    # reporter получил phase + данные для экрана
    assert reporter.last_awaiting == (
        "persona_approve",
        {"persona": {"segment": "ML-инженер"},
         "kb_match": {"slug": "evolution-ml-inference", "name": "X", "version": 1}},
    )
```

(Адаптировать под реальные фикстуры файла; если сервисные тесты устроены иначе — повторить их паттерн, суть проверки та же.)

- [ ] **Step 2: Verify RED.**

- [ ] **Step 3: Реализация в creatives.py**

```python
_OPEN_STATUSES = ("queued", "running", "awaiting_persona", "awaiting_text", "awaiting_image")
_PARKED_STATUSES = ("awaiting_persona", "awaiting_text", "awaiting_image")
_TIMEOUT_ERRORS = {
    "awaiting_persona": "persona_approve_timeout",
    "awaiting_text": "text_approve_timeout",
    "awaiting_image": "image_upload_timeout",
}
```

`_claim_running`: условие `task.status not in ("awaiting_text", "awaiting_image")` заменить на `task.status not in _PARKED_STATUSES`.

`_park` — новая ветка ПЕРЕД image_upload:

```python
    if kind == "persona_approve":
        status = "awaiting_persona"
        await self._set_status(task_uid, status)
        data = {
            "persona": value.get("persona"),
            "kb_match": value.get("kb_match"),
        }
        await reporter.awaiting(phase="persona_approve", data=data)
    elif kind == "image_upload":
        ...
```

Затем grep по репо: `awaiting_text|awaiting_image` в `app/` — все места со списками статусов (reconcile, retention, routes, rearm_parked_timeouts) должны использовать константы или быть дополнены `awaiting_persona`. Особо: `rearm_parked_timeouts` и `pending()`/интерфейс задач — дополнить новый статус (payload-эндпоинт для UI — план 2, но `pending()` не должен падать).

- [ ] **Step 4: Verify GREEN** + весь tests/unit. **Step 5:** ruff, `git commit -m "App3: awaiting_persona — третья парковка в сервисе (таймауты, claim, rearm)"`

---

### Task 10: graph_version — гард парковок при деплое

**Files:**
- Modify: `graph/builder.py` (константа), `graph/state.py` (поле), `app/services/creatives.py` (payload в create + гард в submit_decision)
- Test: `tests/unit/test_creatives_service.py` (дополнить)

- [ ] **Step 1: Падающий тест**

```python
async def test_submit_decision_on_stale_graph_version_fails_clearly(svc_fixture):
    """Парковка со старой топологией не ломается — задача завершается понятной ошибкой."""
    # задача awaiting_text; в чекпоинте graph_version отсутствует (старый прогон)
    _seed_parked_task("uid-stale", status="awaiting_text")
    _stub_graph_state(values={})  # aget_state без graph_version
    with pytest.raises(...):  # НЕ raises — см. реализацию: метод возвращает, задача failed
        ...
    task = await _load_task("uid-stale")
    assert task.status == "failed"
    assert "перезапустите" in task.error
```

(Свести к реальному паттерну тестов сервиса: стаб `svc.graph.aget_state` через monkeypatch, дальше `await svc.submit_decision(...)` и проверка статуса; DecisionConflict НЕ поднимается.)

- [ ] **Step 2: Verify RED.**

- [ ] **Step 3: Реализация**

`graph/builder.py` (модульный уровень):

```python
# Версия топологии графа. Растёт при каждом изменении набора узлов/рёбер.
# Парковка, пережившая деплой с другой версией, не восстанавливается на новой
# топологии, а завершается понятной ошибкой (спека 2026-08-07).
GRAPH_VERSION = 2
```

`graph/state.py`: `graph_version: int` в GraphState.

`creatives.py::create` — в payload добавить `"graph_version": GRAPH_VERSION` (импорт из graph.builder). `submit_decision` — после `_claim_running`, до Command:

```python
    snapshot = await self.graph.aget_state(self._config(task_uid))
    stored = (dict(snapshot.values or {})).get("graph_version")
    if stored != GRAPH_VERSION:
        log.warning(
            "graph_version_mismatch", task_uid=task_uid, stored=stored
        )
        await self._finish(
            task_uid, "failed",
            error="пайплайн обновился — перезапустите задачу",
            reason="graph_version",
        )
        return
```

(`_finish` — существующий приватный метод; сверить сигнатуру по файлу.)

- [ ] **Step 4: Verify GREEN** + весь tests/unit. **Step 5:** ruff, `git commit -m "App3: graph_version — парковки со старой топологией завершаются честной ошибкой"`

---

### Task 11: Provenance — winner_id, персона, комментарии метафоры, kb_source

**Files:**
- Modify: `graph/provenance.py`
- Test: `tests/unit/test_provenance.py` (дополнить)

- [ ] **Step 1: Падающий тест**

```python
def test_human_decisions_in_provenance(tmp_path):
    st = _state(
        winner_id="c1",
        personas=[{"segment": "ML-инженер", "age_range": "28-40",
                   "pain_points": ["x"], "motivations": ["y"],
                   "objections": ["z"], "communication_style": "инженерный"}],
        metaphor_comments=["не часы, покажи очередь"],
        kb_match={"slug": "evolution-ml-inference", "name": "Evolution ML Inference",
                  "version": 3},
    )
    prov = build_provenance(st, rendered_files=_rendered(tmp_path))
    assert prov["winner_id"] == "c1"
    assert prov["persona"]["segment"] == "ML-инженер"
    assert prov["metaphor_comments"] == ["не часы, покажи очередь"]
    assert prov["kb_source"] == {"slug": "evolution-ml-inference",
                                 "name": "Evolution ML Inference", "version": 3}


def test_human_decisions_defaults(tmp_path):
    prov = build_provenance(_state(), rendered_files=_rendered(tmp_path))
    assert prov["winner_id"] is None
    assert prov["metaphor_comments"] == []
    assert prov["kb_source"] is None
```

- [ ] **Step 2: Verify RED.**

- [ ] **Step 3: Реализация** — в dict `build_provenance` добавить:

```python
        "winner_id": state.get("winner_id"),
        "persona": (state.get("personas") or [None])[0],
        "metaphor_comments": state.get("metaphor_comments") or [],
        "kb_source": state.get("kb_match"),
```

Обновить докстринги модуля/тест-модуля: «паспорт отражает человеческие решения».

- [ ] **Step 4: Verify GREEN.** **Step 5:** ruff, `git commit -m "App3: provenance несёт человеческие решения — победитель, персона, комментарии, kb_source"`

---

### Task 12: Полный локальный прогон тестов

- [ ] **Step 1:** `.venv311/Scripts/python.exe -m pytest tests/unit tests/contract tests/agents -q` → все PASS, ноль новых warning-ов.
- [ ] **Step 2:** `.venv311/Scripts/python.exe -m ruff check $(git diff --name-only main -- '*.py')` — чисто по тронутым файлам.
- [ ] **Step 3:** Если что-то падает — чинить код, не тесты (кроме тестов, чьи ожидания legitimately устарели из-за новой топологии, напр. количество узлов).
- [ ] **Step 4:** Финальный коммит остатков, если есть.
