# План 2: App3 — API, UI, роли, опыт в промптах

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести до человека то, что План 1 сделал в графе: выбор карточки знаний в брифе, экран «Кому пишем», выбор победителя текста, разговор о метафоре, «Как сделан этот баннер», отметка исхода, роли/страница библиотеки знаний и слой опыта, который возвращается в промпты.

**Architecture:** Граф уже умеет всё (три остановки, `winner_id`, петля метафоры, `kb_match`) — план 2 добавляет только app-слой: схемы/роуты FastAPI, vanilla-JS панели, две новые таблицы (`kb_runs` — опыт, `user_roles` — доступ) и инжект опыта в `graph.knowledge` тем же приёмом, что и каталог (`set_catalog`). Граф НЕ импортирует `app`. Спека: `docs/superpowers/specs/2026-08-07-ui-artefacts-knowledge-library-design.md`.

**Tech Stack:** FastAPI + Pydantic v2, SQLAlchemy 2.0 async (`Mapped`/`mapped_column`, `create_all` — без Alembic), LangGraph interrupt/Command, vanilla JS (без сборки), pytest (`asyncio_mode=auto`), ruff.

**Конвенции репо (обязательны):**
- Тесты роутов — по образцу `tests/unit/test_app3_routes.py`: `monkeypatch.setattr(creatives_mod, "init_graph", fake)`, `TestClient`, заголовки `{"X-User-Id": "5", "X-User-Email": "u@cloud.ru"}`. Тесты БД — in-memory `sqlite+aiosqlite:///:memory:` + `init_db` (образец `tests/unit/test_kb_store.py`).
- Никаких эмодзи — ни в UI, ни в коде, ни в комментариях.
- Команда тестов: `.venv311/Scripts/python.exe -m pytest tests/unit tests/contract tests/agents -q`. Ruff: `.venv311/Scripts/python.exe -m ruff check <тронутые файлы>` — только тронутые (в репо есть пре-существующие ошибки, их не чинить). Планка — НОЛЬ новых находок, как в Плане 1.
- Аннотации в новом коде — современные: `list[str]`, `dict[str, Any]`, `str | None`. В `app/api/schemas.py` и `app/db/models.py` исторически много `typing.List`/`Optional`, ruff (`UP` включён) на них ругается — это принятый долг, но дописывать его нельзя: ещё один `Optional[...]` = ещё одна находка. Существующие строки при этом не переписываем.
- Каждая правка `app/static/*.js` или `app/templates/*.html` требует поднять cache-buster: `?v=20260805v1` → `?v=20260808v1` (в шаблоне, у css и js).
- Коммиты на ветке `feature/research-loop`, пушей и деплоя НЕТ.
- Топология графа не меняется — `GRAPH_VERSION` остаётся 2 во всех задачах этого плана.

**Порядок задач (жёсткий):** 1-2 (чтение библиотеки) → 3 (выбор продукта) → 4-5 (персона) → 6 (победитель) → 7 (метафора) → 8 (рецепт) → 9 (исход, нужен рецепт из 8) → 10 (роли) → 11 (правка карточек, нужны роли из 10) → 12 (страница библиотеки) → 13 (опыт в промптах, нужен `kb_runs` из 9).

---

### Task 1: kb store — чтение строк, история, создание и правка версий

**Files:**
- Modify: `app/kb/store.py`
- Test: `tests/unit/test_kb_store.py` (дописать в конец)

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/unit/test_kb_store.py`:

```python
async def test_latest_rows_hides_archived_by_default(Session):
    from app.kb.store import latest_rows, update_product

    await seed_from_files(Session)
    rows = await latest_rows(Session)
    victim = rows[0].slug
    await update_product(
        Session, slug=victim, fields={"archived": True}, updated_by="admin@test"
    )
    assert victim not in {r.slug for r in await latest_rows(Session)}
    archived = {r.slug: r for r in await latest_rows(Session, include_archived=True)}
    assert archived[victim].archived is True
    assert archived[victim].version == 2


async def test_update_product_appends_version_and_keeps_untouched_fields(Session):
    from app.kb.store import latest_rows, update_product

    await seed_from_files(Session)
    row = (await latest_rows(Session))[0]
    new_version = await update_product(
        Session,
        slug=row.slug,
        fields={"tagline": "новый tagline"},
        updated_by="admin@test",
    )
    assert new_version == row.version + 1
    fresh = {r.slug: r for r in await latest_rows(Session)}[row.slug]
    assert fresh.tagline == "новый tagline"
    assert fresh.name == row.name          # не тронутое поле переносится
    assert fresh.block1 == row.block1
    assert fresh.updated_by == "admin@test"


async def test_update_product_unknown_slug_raises(Session):
    from app.kb.store import KbNotFound, update_product

    await seed_from_files(Session)
    with pytest.raises(KbNotFound):
        await update_product(
            Session, slug="no-such-product", fields={"tagline": "x"}, updated_by="a@b"
        )


async def test_create_product_starts_at_version_1_and_rejects_duplicate(Session):
    from app.kb.store import KbConflict, create_product, latest_rows

    await seed_from_files(Session)
    v = await create_product(
        Session,
        slug="test-product",
        fields={"name": "Test Product", "tagline": "тест", "block1": "## Блок 1. Что это"},
        updated_by="admin@test",
    )
    assert v == 1
    fresh = {r.slug: r for r in await latest_rows(Session)}["test-product"]
    assert fresh.name == "Test Product"
    assert fresh.aliases == ["Test Product"]  # алиас по умолчанию — имя
    with pytest.raises(KbConflict):
        await create_product(
            Session, slug="test-product", fields={"name": "Dup"}, updated_by="a@b"
        )


async def test_history_is_newest_first(Session):
    from app.kb.store import history, latest_rows, update_product

    await seed_from_files(Session)
    slug = (await latest_rows(Session))[0].slug
    await update_product(Session, slug=slug, fields={"tagline": "v2"}, updated_by="a@b")
    await update_product(Session, slug=slug, fields={"tagline": "v3"}, updated_by="a@b")
    versions = [r.version for r in await history(Session, slug)]
    assert versions == [3, 2, 1]
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'latest_rows' from 'app.kb.store'`

- [ ] **Step 3: Реализовать в `app/kb/store.py`**

Заменить докстроку модуля (последняя фраза про «CRUD — в план 2» больше не верна):

```python
"""Слой «факты» библиотеки знаний: kb_products → graph.knowledge.ProductDoc.

Граф НЕ импортирует app (import-arch стражи): app-слой читает БД и инжектит
снапшот каталога через knowledge.set_catalog(). Правка карточки = новая строка
version+1: история видна целиком, откат — это ещё одна правка."""
```

Добавить после импортов:

```python
class KbNotFound(Exception):
    """Правка карточки, которой нет ни в одной версии."""


class KbConflict(Exception):
    """Создание карточки с уже занятым slug."""


# Поля, которые редактирует человек. Всё остальное (slug/version/updated_*)
# ставит сам store — иначе можно было бы переписать историю через API.
_EDITABLE = ("name", "aliases", "tagline", "block1", "block2", "block3", "archived")
```

Добавить в конец файла:

```python
async def latest_rows(sessionmaker, *, include_archived: bool = False) -> list[models.KbProduct]:
    """Последняя версия каждого slug, по алфавиту. Архивные — по флагу."""
    async with sessionmaker() as s:
        rows = (await s.execute(select(models.KbProduct))).scalars().all()
    latest: dict[str, models.KbProduct] = {}
    for r in rows:
        if r.slug not in latest or r.version > latest[r.slug].version:
            latest[r.slug] = r
    out = sorted(latest.values(), key=lambda r: r.slug)
    return out if include_archived else [r for r in out if not r.archived]


async def history(sessionmaker, slug: str) -> list[models.KbProduct]:
    """Все версии карточки, свежая первой."""
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(models.KbProduct)
                .where(models.KbProduct.slug == slug)
                .order_by(models.KbProduct.version.desc())
            )
        ).scalars().all()
    return list(rows)


async def create_product(sessionmaker, *, slug: str, fields: dict, updated_by: str) -> int:
    """Новая карточка (version=1). KbConflict, если slug уже занят."""
    async with sessionmaker() as s:
        exists = (
            await s.execute(select(models.KbProduct.id).where(models.KbProduct.slug == slug))
        ).first()
        if exists:
            raise KbConflict(slug)
        name = fields.get("name") or slug
        s.add(
            models.KbProduct(
                slug=slug,
                version=1,
                name=name,
                aliases=list(fields.get("aliases") or [name]),
                tagline=fields.get("tagline") or "",
                block1=fields.get("block1") or "",
                block2=fields.get("block2") or "",
                block3=fields.get("block3") or "",
                updated_by=updated_by,
            )
        )
        await s.commit()
        return 1


async def update_product(sessionmaker, *, slug: str, fields: dict, updated_by: str) -> int:
    """Правка = строка version+1: непереданные поля переносятся из последней
    версии. Возвращает номер новой версии. KbNotFound, если slug неизвестен."""
    async with sessionmaker() as s:
        rows = (
            await s.execute(select(models.KbProduct).where(models.KbProduct.slug == slug))
        ).scalars().all()
        if not rows:
            raise KbNotFound(slug)
        prev = max(rows, key=lambda r: r.version)
        data = {k: getattr(prev, k) for k in _EDITABLE}
        data["aliases"] = list(prev.aliases or [])
        data.update({k: v for k, v in fields.items() if k in _EDITABLE and v is not None})
        s.add(
            models.KbProduct(
                slug=slug, version=prev.version + 1, updated_by=updated_by, **data
            )
        )
        await s.commit()
        return prev.version + 1
```

Переписать `load_product_docs` через `latest_rows` (та же семантика, один источник правды о «последней неархивной версии»):

```python
async def load_product_docs(sessionmaker) -> tuple[ProductDoc, ...]:
    """Последняя версия каждого неархивного продукта как ProductDoc."""
    rows = await latest_rows(sessionmaker)
    return tuple(
        ProductDoc(
            slug=r.slug,
            name=r.name,
            aliases=tuple(r.aliases or [r.name]),
            tagline=r.tagline,
            body="\n\n".join(b for b in (r.block1, r.block2, r.block3) if b),
            version=r.version,
        )
        for r in rows
    )
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_store.py -q`
Expected: PASS (9 тестов)

- [ ] **Step 5: Ruff + коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/kb/store.py tests/unit/test_kb_store.py
git add app/kb/store.py tests/unit/test_kb_store.py
git commit -m "App3: kb store — latest_rows/history/create_product/update_product (append-only версии)"
```

---

### Task 2: GET /api/kb/products — каталог для брифа и библиотеки

**Files:**
- Modify: `app/api/schemas.py` (добавить `KbProductOut`)
- Create: `app/api/routes_kb.py`
- Modify: `app/main.py` (зарегистрировать роутер)
- Test: `tests/unit/test_kb_routes.py`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/unit/test_kb_routes.py`:

```python
"""Библиотека знаний по HTTP: чтение каталога (правка — Task 11)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "5", "X-User-Email": "u@cloud.ru"}


def _app(tmp_path, monkeypatch):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'r.db'}"})


def test_products_list_is_seeded_and_shaped(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/api/kb/products", headers=_HDR)
        assert r.status_code == 200
        items = r.json()
        assert items, "сид библиотеки знаний не отработал"
        first = items[0]
        assert set(first) >= {"slug", "name", "version", "tagline", "aliases", "archived"}
        assert first["version"] == 1
        assert first["archived"] is False


def test_products_list_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/api/kb/products").status_code == 401
```

- [ ] **Step 2: Запустить — тест падает**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py -q`
Expected: FAIL — 404 на `/api/kb/products`

- [ ] **Step 3: Схема + роутер + регистрация**

В `app/api/schemas.py` добавить в конец:

```python
class KbProductOut(BaseModel):
    """Карточка продукта в её последней версии.

    Блоки отдаются целиком: каталог небольшой (десяток продуктов), а страница
    библиотеки и так показывает их полностью — второй «короткий» эндпоинт был
    бы лишней сущностью."""

    slug: str
    name: str
    version: int
    aliases: list[str] = Field(default_factory=list)
    tagline: str = ""
    archived: bool = False
    updated_by: str = ""
    updated_at: str | None = None
    block1: str = ""
    block2: str = ""
    block3: str = ""
```

Создать `app/api/routes_kb.py`:

```python
"""Библиотека знаний по HTTP — чтение каталога.

Читать может любой авторизованный пользователь: карточки продуктов это не
секрет, а общий словарь команды. Правка и история — Task 11 (под ролями).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import KbProductOut
from app.auth.deps import get_current_user
from app.db import models
from app.kb.store import latest_rows

router = APIRouter(prefix="/api/kb", tags=["kb"])


def kb_out(r: models.KbProduct) -> KbProductOut:
    return KbProductOut(
        slug=r.slug,
        name=r.name,
        version=r.version,
        aliases=list(r.aliases or []),
        tagline=r.tagline or "",
        archived=bool(r.archived),
        updated_by=r.updated_by or "",
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
        block1=r.block1 or "",
        block2=r.block2 or "",
        block3=r.block3 or "",
    )


@router.get("/products")
async def list_products(request: Request, include_archived: bool = False):
    await get_current_user(request)
    rows = await latest_rows(
        request.app.state.sessionmaker, include_archived=include_archived
    )
    return [kb_out(r) for r in rows]
```

В `app/main.py` — импорт рядом с остальными роутерами:

```python
    from app.api.routes_auth import router as auth_router
    from app.api.routes_kb import router as kb_router
    from app.api.routes_pages import router as pages_router
```

и регистрация после `tasks_router`:

```python
    app.include_router(tasks_router)
    app.include_router(kb_router)
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py -q`
Expected: PASS (2 теста)

- [ ] **Step 5: Ruff + коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/api/routes_kb.py app/api/schemas.py app/main.py tests/unit/test_kb_routes.py
git add app/api/routes_kb.py app/api/schemas.py app/main.py tests/unit/test_kb_routes.py
git commit -m "App3: GET /api/kb/products — каталог библиотеки знаний"
```

---

### Task 3: product_slug в брифе — API-валидация и выпадающий список

**Files:**
- Modify: `app/api/schemas.py` (`CreateTaskIn.product_slug`)
- Modify: `app/api/routes_tasks.py` (валидация slug в `create_task`, `_BRIEF_KEYS`)
- Modify: `app/templates/creatives.html` (селект в briefPanel + cache-buster)
- Modify: `app/static/creatives.js` (загрузка списка, отправка поля)
- Test: `tests/unit/test_app3_routes.py` (дописать)

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/unit/test_app3_routes.py`:

```python
def test_create_passes_product_slug_to_service(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        class _Stub:
            async def create(self, user_id, fields):
                self.seen = fields
                return "deadbeef0002"

        stub = _Stub()
        app.state.creatives = stub
        slug = c.get("/api/kb/products", headers=_HDR).json()[0]["slug"]
        r = c.post(
            "/api/tasks",
            json={"product": "p", "audience": "a", "emotion": "e", "product_slug": slug},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert stub.seen["product_slug"] == slug


def test_create_rejects_unknown_product_slug(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        class _Stub:
            async def create(self, user_id, fields):
                raise AssertionError("сервис не должен вызываться при неизвестном slug")

        app.state.creatives = _Stub()
        r = c.post(
            "/api/tasks",
            json={"product": "p", "audience": "a", "emotion": "e", "product_slug": "nope"},
            headers=_HDR,
        )
        assert r.status_code == 422


def test_create_defaults_product_slug_to_auto(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        class _Stub:
            async def create(self, user_id, fields):
                self.seen = fields
                return "deadbeef0003"

        stub = _Stub()
        app.state.creatives = stub
        r = c.post(
            "/api/tasks",
            json={"product": "p", "audience": "a", "emotion": "e"},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert stub.seen["product_slug"] == "auto"
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_routes.py -q -k product_slug`
Expected: FAIL — `KeyError: 'product_slug'` / 200 вместо 422

- [ ] **Step 3: Реализовать**

В `app/api/schemas.py`, в `CreateTaskIn`, добавить поле и дописать докстроку:

```python
    product: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    emotion: str = Field(min_length=1)
    notes: str = Field(default="", max_length=4000)
    source_url: str = Field(default="", max_length=2000)
    # Карточка библиотеки знаний: "auto" — найти по тексту брифа (как было),
    # "none" — не подмешивать карточку вовсе, иначе конкретный slug.
    product_slug: str = Field(default="auto", max_length=64)
```

В `app/api/routes_tasks.py` — импорт и валидация:

```python
from app.kb.store import latest_rows
```

```python
_BRIEF_KEYS = ("product", "audience", "emotion", "notes", "source_url", "product_slug")

# Служебные значения product_slug, которые не обязаны существовать в каталоге.
_SLUG_SENTINELS = ("auto", "none")
```

```python
@router.post("/tasks")
async def create_task(body: CreateTaskIn, request: Request):
    user = await get_current_user(request)
    service = getattr(request.app.state, "creatives", None)
    if service is None:
        raise HTTPException(503, "service unavailable (graph not initialised)")
    slug = body.product_slug or "auto"
    if slug not in _SLUG_SENTINELS:
        rows = await latest_rows(request.app.state.sessionmaker)
        if slug not in {r.slug for r in rows}:
            raise HTTPException(422, f"unknown product_slug: {slug}")
    try:
        task_uid = await service.create(str(user.id), body.model_dump())
    except CapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"task_uid": task_uid}
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_routes.py -q`
Expected: PASS

- [ ] **Step 5: Селект в брифе (HTML)**

В `app/templates/creatives.html`, в `#briefPanel`, сразу после `<h2>01 · Бриф</h2>`:

```html
      <label for="productSlug">Карточка из библиотеки знаний</label>
      <select id="productSlug">
        <option value="auto">Определить автоматически</option>
        <option value="none">Не использовать</option>
      </select>
      <p class="page-sub">Факты о продукте, на которые пайплайн опирается и за которые не выходит. Автоопределение ищет продукт по тексту брифа.</p>
```

Поднять cache-buster в обеих ссылках:

```html
  <link rel="stylesheet" href="{{ prefix }}/static/app.css?v=20260808v1">
```
```html
  <script src="{{ prefix }}/static/creatives.js?v=20260808v1"></script>
```

- [ ] **Step 6: Загрузка каталога и отправка поля (JS)**

В `app/static/creatives.js`, в обработчике `startBtn`, добавить чтение и отправку:

```javascript
    const source_url = $("sourceUrl").value.trim();
    const product_slug = $("productSlug").value || "auto";
```
```javascript
        body: JSON.stringify({ product, audience, emotion, notes, source_url, product_slug }),
```

Добавить загрузчик каталога перед секцией `// ── SSE`:

```javascript
  // ── библиотека знаний: карточки в селект брифа ──────────
  // Сбой не должен ломать бриф — остаются "auto"/"none".
  async function loadProducts() {
    try {
      const r = await fetch(`${P}/api/kb/products`);
      if (!r.ok) return;
      const items = await r.json();
      const sel = $("productSlug");
      items.forEach((p) => {
        const o = document.createElement("option");
        o.value = p.slug;
        o.textContent = p.tagline ? `${p.name} — ${p.tagline}` : p.name;
        sel.appendChild(o);
      });
    } catch (_) {}
  }
```

и вызвать её рядом с остальным стартом (последние строки файла):

```javascript
  rehydrate();
  loadRecentTasks();
  loadProducts();
})();
```

- [ ] **Step 7: Ruff + коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/api/schemas.py app/api/routes_tasks.py tests/unit/test_app3_routes.py
git add app/api/schemas.py app/api/routes_tasks.py app/templates/creatives.html app/static/creatives.js tests/unit/test_app3_routes.py
git commit -m "App3: product_slug в брифе — валидация по каталогу + селект в UI"
```

---

### Task 4: POST /api/tasks/{uid}/decision/persona — остановка «Кому пишем»

**Files:**
- Modify: `app/api/schemas.py` (`PersonaIn`, `PersonaDecisionIn`)
- Modify: `app/api/routes_tasks.py` (роут `decide_persona`)
- Test: `tests/unit/test_app3_routes.py` (дописать)

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/unit/test_app3_routes.py`:

```python
_PERSONA = {
    "segment": "ML-инженеры",
    "age_range": "25-40",
    "pain_points": ["очередь на GPU"],
    "motivations": ["быстрее катить итерации"],
    "objections": ["дорого экспериментировать"],
    "communication_style": "по делу, без маркетинга",
}


def _stub_decisions(app):
    """Подменяет оркестратор на заглушку, которая только запоминает решение."""

    class _Stub:
        seen = None

        async def submit_decision(self, uid, user_id, decision):
            self.seen = (uid, user_id, decision)

    stub = _Stub()
    app.state.creatives = stub
    return stub


def test_decide_persona_forwards_edited_persona(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "persona01", "awaiting_persona", me["id"])
        stub = _stub_decisions(app)
        r = c.post(
            "/api/tasks/persona01/decision/persona",
            json={"action": "approve", "persona": _PERSONA},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert stub.seen[2]["action"] == "approve"
        assert stub.seen[2]["persona"]["segment"] == "ML-инженеры"


def test_decide_persona_regenerate_carries_no_persona(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "persona02", "awaiting_persona", me["id"])
        stub = _stub_decisions(app)
        r = c.post(
            "/api/tasks/persona02/decision/persona",
            json={"action": "regenerate"},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert stub.seen[2] == {"action": "regenerate"}


def test_decide_persona_wrong_status_is_409(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "persona03", "awaiting_text", me["id"])
        _stub_decisions(app)
        r = c.post(
            "/api/tasks/persona03/decision/persona",
            json={"action": "approve"},
            headers=_HDR,
        )
        assert r.status_code == 409


def test_decide_persona_rejects_empty_anchor_lists(tmp_path, monkeypatch):
    """Персона без болей и мотиваций обнуляет весь текстовый этап — 422 здесь,
    а не тихий мусор в графе."""
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "persona04", "awaiting_persona", me["id"])
        _stub_decisions(app)
        bad = {**_PERSONA, "pain_points": []}
        r = c.post(
            "/api/tasks/persona04/decision/persona",
            json={"action": "approve", "persona": bad},
            headers=_HDR,
        )
        assert r.status_code == 422
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_routes.py -q -k persona`
Expected: FAIL — 404/405 на `/decision/persona`

- [ ] **Step 3: Схемы**

В `app/api/schemas.py` добавить перед `TextDecisionIn`:

```python
class PersonaIn(BaseModel):
    """Персона, отредактированная человеком на остановке «Кому пишем».

    Поля повторяют graph.state.Persona, но списки якорей обязательны:
    из болей/мотиваций/возражений собираются 24 черновика, и пустая персона
    просто сожгла бы этап. Возражения допускаются пустыми — они есть не всегда.
    """

    segment: str = Field(min_length=1, max_length=200)
    age_range: str = Field(default="", max_length=64)
    pain_points: list[str] = Field(min_length=1)
    motivations: list[str] = Field(min_length=1)
    objections: list[str] = Field(default_factory=list)
    communication_style: str = Field(default="", max_length=600)


class PersonaDecisionIn(BaseModel):
    """Resume остановки «Кому пишем».

    approve без persona — согласие с тем, что вывел граф; approve с persona —
    правка без повторного LLM-вызова; regenerate — вывести персону заново.
    """

    action: Literal["approve", "regenerate", "cancel"]
    persona: PersonaIn | None = None
```

- [ ] **Step 4: Роут**

В `app/api/routes_tasks.py` расширить импорт схем:

```python
from app.api.schemas import CreateTaskIn, PersonaDecisionIn, TaskOut, TextDecisionIn
```

и добавить роут перед `decide_text`:

```python
@router.post("/tasks/{uid}/decision/persona")
async def decide_persona(uid: str, body: PersonaDecisionIn, request: Request):
    """Resume остановки «Кому пишем» (первая пауза HITL)."""
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status != "awaiting_persona":
        raise HTTPException(409, f"task not awaiting persona (status={task.status})")
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")
    decision: dict = {"action": body.action}
    if body.action == "approve" and body.persona is not None:
        decision["persona"] = body.persona.model_dump()
    try:
        await service.submit_decision(uid, str(user.id), decision)
    except DecisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"ok": True, "action": body.action}
```

- [ ] **Step 5: Запустить — тесты проходят**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_routes.py -q`
Expected: PASS

- [ ] **Step 6: Ruff + коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/api/schemas.py app/api/routes_tasks.py tests/unit/test_app3_routes.py
git add app/api/schemas.py app/api/routes_tasks.py tests/unit/test_app3_routes.py
git commit -m "App3: POST /decision/persona — приём решения по персоне (в т.ч. правленой)"
```

---

### Task 5: Экран «Кому пишем» — редактируемая персона в UI

**Files:**
- Modify: `app/templates/creatives.html` (панель `#personaPanel`)
- Modify: `app/static/creatives.js` (`onAwaiting` ветка `persona_approve`, отправка решения)
- Modify: `app/static/app.css` (стили плашки kb_match)

- [ ] **Step 1: Панель в шаблоне**

В `app/templates/creatives.html`, в `workspace__output`, между `#progressPanel` и `#textPanel`:

```html
    <!-- 2.5 persona HITL -->
    <section class="panel hidden" id="personaPanel">
      <h2>02 · Кому пишем</h2>
      <p class="page-sub">Так пайплайн понял вашу аудиторию. Боли, мотивации и возражения — это якоря, из которых собираются все тексты: правьте прямо здесь, каждая строка с новой строки.</p>
      <div class="kb-badge" id="personaKb"></div>
      <label for="personaSegment">Сегмент</label>
      <input type="text" id="personaSegment">
      <label for="personaAge">Возраст</label>
      <input type="text" id="personaAge">
      <label for="personaPains">Боли</label>
      <textarea id="personaPains"></textarea>
      <label for="personaMotivations">Мотивации</label>
      <textarea id="personaMotivations"></textarea>
      <label for="personaObjections">Возражения</label>
      <textarea id="personaObjections"></textarea>
      <label for="personaStyle">Как с ними говорить</label>
      <textarea id="personaStyle"></textarea>
      <div class="btn-row">
        <button class="btn btn--accent" data-pact="approve">Продолжить</button>
        <button class="btn" data-pact="regenerate">Вывести заново</button>
        <button class="btn btn--danger" data-pact="cancel">Отменить</button>
      </div>
      <div class="status" id="personaStatus"></div>
    </section>
```

Обновить список шагов в `#emptyState` (второй пункт становится персоной, остальные сдвигаются):

```html
        <li><b>01 · Бриф</b> — продукт, аудитория, эмоция/образ; плюс необязательные свободное поле и ссылка на страницу продукта, которую пайплайн прочитает сам.</li>
        <li><b>02 · Кому пишем</b> — пайплайн выводит персону аудитории; вы правите боли, мотивации и возражения, из которых потом собираются тексты.</li>
        <li><b>03 · Тексты</b> — маркетолог пишет 24 черновика, персона ЦА отбирает из них 12 лучших от своего лица; вы выбираете, какой текст ведёт.</li>
        <li><b>04 · Hero</b> — под каждое предложение генерируется своя картинка (render или photo — выбирается автоматически), либо вы загружаете одну свою.</li>
        <li><b>05 · Баннеры</b> — 12 готовых 300×600, текст, CTA и логотип уже наложены композером.</li>
```

Заголовки соседних панелей сдвинуть по нумерации: `#textPanel` → `<h2>03 · Тексты — 12 предложений под аудиторию</h2>`, `#imagePanel` → `<h2>04 · Hero-картинки</h2>`, `#resultsPanel` → `<h2>05 · Баннеры</h2>`.

- [ ] **Step 2: JS — рендер, отправка, участие в общих списках панелей**

В `app/static/creatives.js`:

добавить панель в список выходных панелей и в скрытие HITL:

```javascript
  const OUTPUT_PANELS = ["progressPanel", "personaPanel", "textPanel", "imagePanel", "resultsPanel", "tasksPanel"];
```
```javascript
  function hideHitl() { hide($("personaPanel")); hide($("textPanel")); hide($("imagePanel")); }
```

в `onAwaiting` добавить ветку первой:

```javascript
  function onAwaiting(d) {
    hide($("progressPanel"));
    if (d.phase === "persona_approve") {
      renderPersona(d.persona || {}, d.kb_match);
      setBusy($("personaPanel"), false);
      hide($("textPanel")); hide($("imagePanel")); show($("personaPanel"));
      focusPanel($("personaPanel"));
    } else if (d.phase === "text_approve") {
```

добавить рендер и отправку после `renderCandidates` / `kv`:

```javascript
  // Персона: списки якорей редактируются как многострочный текст — одна
  // строка = один якорь. Это ровно та форма, в которой их читает промпт.
  const linesOf = (v) => (Array.isArray(v) ? v.join("\n") : "");
  const listOf = (id) => $(id).value.split("\n").map((s) => s.trim()).filter(Boolean);

  function renderPersona(p, kb) {
    $("personaSegment").value = p.segment || "";
    $("personaAge").value = p.age_range || "";
    $("personaPains").value = linesOf(p.pain_points);
    $("personaMotivations").value = linesOf(p.motivations);
    $("personaObjections").value = linesOf(p.objections);
    $("personaStyle").value = p.communication_style || "";
    const badge = $("personaKb");
    badge.textContent = kb && kb.slug
      ? `Карточка знаний: ${kb.name} (версия ${kb.version})`
      : "Карточка знаний не подобрана — тексты опираются только на бриф.";
    badge.classList.toggle("kb-badge--none", !(kb && kb.slug));
  }

  document.querySelectorAll("#personaPanel [data-pact]").forEach((btn) => {
    btn.addEventListener("click", () => sendPersona(btn.dataset.pact));
  });

  async function sendPersona(action) {
    if (action === "cancel" && !window.confirm("Отменить задачу? Прогресс будет потерян.")) return;
    const pains = listOf("personaPains");
    const motivations = listOf("personaMotivations");
    if (action === "approve" && (!pains.length || !motivations.length)) {
      $("personaStatus").innerHTML = '<span class="err">Нужна хотя бы одна боль и одна мотивация — из них собираются тексты.</span>';
      return;
    }
    const body = { action };
    if (action === "approve") {
      body.persona = {
        segment: $("personaSegment").value.trim(),
        age_range: $("personaAge").value.trim(),
        pain_points: pains,
        motivations: motivations,
        objections: listOf("personaObjections"),
        communication_style: $("personaStyle").value.trim(),
      };
    }
    const panel = $("personaPanel");
    $("personaStatus").textContent = "";
    setBusy(panel, true);
    const r = await post(`${P}/api/tasks/${taskUid}/decision/persona`, body);
    if (r && r.ok) {
      setBusy(panel, false);
      hideHitl(); setStep("Пишу тексты…");
      return;
    }
    setBusy(panel, false);
    $("personaStatus").innerHTML = `<span class="err">${escapeHtml(errText(r ? r.status : 0))}</span>`;
  }
```

- [ ] **Step 3: Стили плашки**

В конец `app/static/app.css`:

```css
/* Плашка карточки знаний на экране персоны */
.kb-badge { margin: 8px 0 16px; padding: 8px 12px; border-radius: 8px;
  background: var(--surface-2, #f2f4f7); font-size: 13px; }
.kb-badge--none { opacity: .7; }
```

- [ ] **Step 4: Проверить, что ничего не сломалось**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit -q`
Expected: PASS (UI-код тестами не покрыт; проверка — что бэкенд не тронут)

- [ ] **Step 5: Коммит**

```bash
git add app/templates/creatives.html app/static/creatives.js app/static/app.css
git commit -m "App3 UI: экран «Кому пишем» — редактируемая персона + плашка карточки знаний"
```

---

### Task 6: Победитель текста — winner_id в API и «Почему такой текст» в UI

**Files:**
- Modify: `app/api/schemas.py` (`TextDecisionIn.winner_id`)
- Modify: `app/api/routes_tasks.py` (`decide_text`)
- Modify: `app/static/creatives.js` (`renderCandidates`, `sendText`)
- Modify: `app/static/app.css` (кнопка выбора, раскрывающееся обоснование)
- Test: `tests/unit/test_app3_routes.py` (дописать)

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/unit/test_app3_routes.py`:

```python
def test_decide_text_forwards_winner_id(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "text01", "awaiting_text", me["id"])
        stub = _stub_decisions(app)
        r = c.post(
            "/api/tasks/text01/decision/text",
            json={"action": "approve", "winner_id": "cand42"},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert stub.seen[2] == {"action": "approve", "winner_id": "cand42"}


def test_decide_text_without_winner_id_is_unchanged(tmp_path, monkeypatch):
    """Совместимость: «принять как есть» по-прежнему шлёт только action."""
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "text02", "awaiting_text", me["id"])
        stub = _stub_decisions(app)
        r = c.post(
            "/api/tasks/text02/decision/text", json={"action": "approve"}, headers=_HDR
        )
        assert r.status_code == 200
        assert stub.seen[2] == {"action": "approve"}


def test_decide_text_ignores_winner_id_on_regenerate(tmp_path, monkeypatch):
    """Победитель у выброшенного набора бессмысленен — не пропускаем в граф."""
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "text03", "awaiting_text", me["id"])
        stub = _stub_decisions(app)
        r = c.post(
            "/api/tasks/text03/decision/text",
            json={"action": "regenerate", "winner_id": "cand42"},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert stub.seen[2] == {"action": "regenerate"}
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_routes.py -q -k winner`
Expected: FAIL — решение приходит без `winner_id`

- [ ] **Step 3: Схема + роут**

В `app/api/schemas.py` — `TextDecisionIn`:

```python
class TextDecisionIn(BaseModel):
    """Resume остановки «Выбор текстов».

    Человек смотрит 12 предложений и либо принимает набор, либо просит
    сгенерировать заново, либо отменяет. `winner_id` (2026-08-07) помечает,
    какой текст ведёт: граф ставит его в ranked[0], и метафора/рендер строятся
    вокруг него. Без winner_id остаётся скоринговый порядок.
    """

    action: Literal["approve", "regenerate", "cancel"]
    winner_id: str | None = Field(default=None, max_length=64)
```

В `app/api/routes_tasks.py` — тело `decide_text`:

```python
    decision: dict = {"action": body.action}
    # winner_id имеет смысл только при approve: при regenerate набор
    # выбрасывается целиком, и указатель на карточку из него — мусор.
    if body.action == "approve" and body.winner_id:
        decision["winner_id"] = body.winner_id
```

- [ ] **Step 4: Запустить — тесты проходят**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_routes.py -q`
Expected: PASS

- [ ] **Step 5: UI — «Взять эту» и «Почему такой текст»**

В `app/static/creatives.js` заменить `renderCandidates` и добавить состояние выбора:

```javascript
  // Выбранный победитель: ranked[0] по умолчанию (скоринговый порядок), пока
  // человек не ткнул в другую карточку.
  let winnerId = null;

  function renderCandidates(list) {
    winnerId = list.length ? (list[0].id || null) : null;
    if (!list.length) { $("candidates").innerHTML = "<p class=\"page-sub\">Нет предложений.</p>"; return; }
    $("candidates").innerHTML = list.map((c, i) => {
      const rank = i + 1;
      const score = (typeof c.score === "number") ? c.score.toFixed(1) : "";
      const id = c.id || "";
      const head = `<div class="cand-head"><span class="cand-rank">#${rank}</span>` +
        `<span class="cand-slogan">${escapeHtml(c.slogan || "")}</span>` +
        (score ? `<span class="cand-score">${score}</span>` : "") + `</div>`;
      // Флажки линта (блок 3): информируют, не гейтят — решает человек.
      const flags = (Array.isArray(c.lint_flags) && c.lint_flags.length)
        ? `<div class="cand-flags">${c.lint_flags.map((f) => `<span class="cand-flag">${escapeHtml(f)}</span>`).join("")}</div>`
        : "";
      // Обоснование под спойлером: якорь персоны и обещанный результат —
      // то, из чего кандидат вырос, а не пересказ слогана.
      const why = (c.anchor || c.desired_outcome || c.reason)
        ? `<details class="cand-why"><summary>Почему такой текст</summary>` +
          kv("якорь персоны", c.anchor) + kv("что человек получит", c.desired_outcome) +
          kv("почему зайдёт ЦА", c.reason) + `</details>`
        : "";
      const pick = id
        ? `<button class="btn cand-pick${i === 0 ? " is-winner" : ""}" data-pick="${escapeHtml(id)}">` +
          `${i === 0 ? "Ведёт эта" : "Взять эту"}</button>`
        : "";
      return `<div class="cand-card" data-cand="${escapeHtml(id)}">${head}` +
        kv("cta", c.cta) + kv("hook", c.hook_angle) + kv("идея", c.body) +
        why + flags + pick + `</div>`;
    }).join("");
  }

  // Делегированный выбор победителя: перекрашиваем кнопки, ничего не шлём —
  // решение уходит одним запросом по «Принять».
  $("candidates").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-pick]");
    if (!btn) return;
    winnerId = btn.dataset.pick;
    $("candidates").querySelectorAll(".cand-pick").forEach((b) => {
      const on = b.dataset.pick === winnerId;
      b.classList.toggle("is-winner", on);
      b.textContent = on ? "Ведёт эта" : "Взять эту";
    });
  });
```

в `sendText` передавать победителя:

```javascript
    const body = { action };
    if (action === "approve" && winnerId) body.winner_id = winnerId;
    const r = await post(`${P}/api/tasks/${taskUid}/decision/text`, body);
```

Текст кнопки принятия в `app/templates/creatives.html` — «Принять все» больше не описывает действие:

```html
        <button class="btn btn--accent" data-act="approve">Принять</button>
```

- [ ] **Step 6: Стили**

В конец `app/static/app.css`:

```css
/* Выбор ведущего текста + обоснование кандидата */
.cand-pick { margin-top: 10px; }
.cand-pick.is-winner { border-color: var(--accent, #6b5bff); font-weight: 600; }
.cand-why { margin-top: 8px; font-size: 13px; }
.cand-why summary { cursor: pointer; }
```

- [ ] **Step 7: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/api/schemas.py app/api/routes_tasks.py tests/unit/test_app3_routes.py
git add app/api/schemas.py app/api/routes_tasks.py app/static/creatives.js app/static/app.css app/templates/creatives.html tests/unit/test_app3_routes.py
git commit -m "App3: winner_id в решении по текстам + выбор ведущего текста и обоснование в UI"
```

---

### Task 7: Разговор о метафоре — задумка на экране и комментарий обратно в граф

**Files:**
- Modify: `graph/nodes/hitl_image_upload.py` (метафора в payload interrupt)
- Modify: `app/services/creatives.py` (`_park`, `pending`)
- Modify: `app/api/routes_tasks.py` (`decide_image`: whitelist действий + `comment`)
- Modify: `app/templates/creatives.html`, `app/static/creatives.js`, `app/static/app.css`
- Test: `tests/unit/test_hitl_image_upload_node.py`, `tests/unit/test_app3_routes.py`

Топология графа не меняется (петля метафоры собрана в Плане 1) — `GRAPH_VERSION` остаётся 2.

- [ ] **Step 1: Тест на payload узла**

Дописать в конец `tests/unit/test_hitl_image_upload_node.py`:

```python
@pytest.mark.asyncio
async def test_interrupt_payload_carries_metaphor_meta(monkeypatch):
    """Человек должен видеть, ЧТО за образ ему предлагают, иначе комментировать
    метафору нечем."""
    fake = _patch_interrupt(monkeypatch, {"action": "cancel"})
    await mod.hitl_image_upload(
        _state(
            metaphor_meta=[
                {
                    "candidate_id": "c1",
                    "metaphor": "a bridge across a canyon",
                    "rationale": "переход от хаоса к порядку",
                    "intended_inference": "с продуктом путь становится коротким",
                    "anti_reading": "не должно читаться как стройка",
                }
            ]
        )
    )
    p = fake.last_payload
    assert p["metaphor"] == "a bridge across a canyon"
    assert p["intended_inference"] == "с продуктом путь становится коротким"
    assert p["anti_reading"] == "не должно читаться как стройка"


@pytest.mark.asyncio
async def test_interrupt_payload_without_metaphor_meta_is_empty_strings(monkeypatch):
    fake = _patch_interrupt(monkeypatch, {"action": "cancel"})
    await mod.hitl_image_upload(_state())
    p = fake.last_payload
    assert p["metaphor"] == "" and p["anti_reading"] == ""
```

- [ ] **Step 2: Запустить — падает**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_hitl_image_upload_node.py -q`
Expected: FAIL — `KeyError: 'metaphor'`

- [ ] **Step 3: Расширить payload узла**

В `graph/nodes/hitl_image_upload.py`, перед `decision: dict = interrupt(...)`:

```python
    # Метафора победителя — то, что человек обсуждает на этой остановке.
    # Пусто, если generate_image_prompt по какой-то причине не отдал meta:
    # экран тогда просто показывает промпт, как до 2026-08-07.
    meta = (state.get("metaphor_meta") or [{}])[0] or {}
```

и сам вызов:

```python
    decision: dict = interrupt(
        {
            "kind": "image_upload",
            "image_prompt": image_prompt,
            "image_style": image_style,
            "metaphor": meta.get("metaphor", ""),
            "intended_inference": meta.get("intended_inference", ""),
            "anti_reading": meta.get("anti_reading", ""),
            "session_id": state.get("session_id"),
        }
    )
```

- [ ] **Step 4: Запустить — проходит**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_hitl_image_upload_node.py -q`
Expected: PASS

- [ ] **Step 5: Пробросить метафору в SSE и в /pending**

В `app/services/creatives.py`, `_park`, ветка `image_upload`:

```python
        elif kind == "image_upload":
            status = "awaiting_image"
            await self._set_status(task_uid, status)
            data = {
                "image_prompt": value.get("image_prompt", ""),
                "image_style": value.get("image_style", ""),
                "metaphor": value.get("metaphor", ""),
                "intended_inference": value.get("intended_inference", ""),
                "anti_reading": value.get("anti_reading", ""),
                "can_generate": self.hero_generator.available,
            }
            await reporter.awaiting(phase="image_upload", data=data)
```

в `pending`, ветка `awaiting_image` (payload interrupt не хранится — собираем из state):

```python
        if status == "awaiting_image":
            meta = (values.get("metaphor_meta") or [{}])[0] or {}
            return {
                "phase": "image_upload",
                "image_prompt": values.get("image_prompt", ""),
                "image_style": values.get("image_style", ""),
                "metaphor": meta.get("metaphor", ""),
                "intended_inference": meta.get("intended_inference", ""),
                "anti_reading": meta.get("anti_reading", ""),
                "can_generate": True,
            }
```

- [ ] **Step 6: Тест роута на действие metaphor**

Дописать в конец `tests/unit/test_app3_routes.py`:

```python
def test_decide_image_metaphor_forwards_comment(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "img01", "awaiting_image", me["id"])
        stub = _stub_decisions(app)
        r = c.post(
            "/api/tasks/img01/decision/image",
            data={"action": "metaphor", "comment": "слишком буквально, дай абстракцию"},
            headers=_HDR,
        )
        assert r.status_code == 200
        assert stub.seen[2] == {
            "action": "metaphor",
            "comment": "слишком буквально, дай абстракцию",
        }


def test_decide_image_metaphor_requires_comment(tmp_path, monkeypatch):
    """Пустой комментарий увёл бы граф на повторный interrupt без причины."""
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "img02", "awaiting_image", me["id"])
        _stub_decisions(app)
        r = c.post(
            "/api/tasks/img02/decision/image",
            data={"action": "metaphor", "comment": "   "},
            headers=_HDR,
        )
        assert r.status_code == 422


def test_decide_image_rejects_unknown_action(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "img03", "awaiting_image", me["id"])
        _stub_decisions(app)
        r = c.post(
            "/api/tasks/img03/decision/image", data={"action": "nope"}, headers=_HDR
        )
        assert r.status_code == 422
```

- [ ] **Step 7: Запустить — падает**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_routes.py -q -k image`
Expected: FAIL — `action=metaphor` уходит в ветку upload и падает на отсутствующем файле

- [ ] **Step 8: Роут `decide_image`**

В `app/api/routes_tasks.py` заменить сигнатуру и начало `decide_image`:

```python
_IMAGE_ACTIONS = ("upload", "generate", "cancel", "metaphor")


@router.post("/tasks/{uid}/decision/image")
async def decide_image(
    uid: str,
    request: Request,
    action: str = Form("upload"),
    comment: str = Form(""),
    file: UploadFile | None = File(None),
):
    """Resume остановки «Картинка».

    multipart form:
      - action=upload + file    → сохранить загрузку, резюмить с local_path
      - action=generate         → серверная генерация 12 hero
      - action=metaphor + comment → перегенерация образа победителя (1 вызов LLM)
      - action=cancel           → отмена задачи
    """
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status != "awaiting_image":
        raise HTTPException(409, f"task not awaiting image (status={task.status})")
    if action not in _IMAGE_ACTIONS:
        raise HTTPException(422, f"unknown action: {action}")
    service = request.app.state.creatives
    if service is None:
        raise HTTPException(503, "service unavailable")

    if action == "metaphor":
        text = comment.strip()
        if not text:
            raise HTTPException(422, "metaphor comment is empty")
        try:
            await service.submit_decision(
                uid, str(user.id), {"action": "metaphor", "comment": text}
            )
        except DecisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except CapacityError as exc:
            raise HTTPException(429, str(exc)) from exc
        return {"ok": True, "action": "metaphor"}

    if action == "cancel":
```

(дальше существующие ветки cancel/generate/upload без изменений)

- [ ] **Step 9: Запустить — проходит**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 10: UI — задумка и комментарий**

В `app/templates/creatives.html`, в `#imagePanel`, перед `<pre class="prompt" id="imagePrompt"></pre>`:

```html
      <div class="metaphor hidden" id="metaphorBox">
        <div class="kv"><b>Задумка:</b> <span id="mIdea"></span></div>
        <div class="kv"><b>Читатель должен подумать:</b> <span id="mInfer"></span></div>
        <div class="kv"><b>Не должно читаться как:</b> <span id="mAnti"></span></div>
        <label for="metaphorComment">Что не так с образом <span class="page-sub">(необязательно)</span></label>
        <textarea id="metaphorComment" placeholder="Слишком буквально; хочу без людей; ближе к идее скорости"></textarea>
        <div class="btn-row">
          <button class="btn" id="metaphorBtn">Переделать образ</button>
        </div>
      </div>
```

В `app/static/creatives.js`, ветка `image_upload` в `onAwaiting` — после установки промпта:

```javascript
      $("imagePrompt").textContent = d.image_prompt || "(пусто)";
      renderMetaphor(d);
```

и новые функции рядом с `sendImage`:

```javascript
  function renderMetaphor(d) {
    const box = $("metaphorBox");
    if (!d.metaphor) { hide(box); return; }
    $("mIdea").textContent = d.metaphor;
    $("mInfer").textContent = d.intended_inference || "—";
    $("mAnti").textContent = d.anti_reading || "—";
    $("metaphorComment").value = "";
    box.classList.remove("hidden");
  }

  $("metaphorBtn").addEventListener("click", () => {
    const comment = $("metaphorComment").value.trim();
    if (!comment) {
      $("imageStatus").innerHTML = '<span class="err">Напиши, что не так с образом — иначе переделывать нечего.</span>';
      return;
    }
    const fd = new FormData();
    fd.append("action", "metaphor");
    fd.append("comment", comment);
    sendImage(fd, "Переделываю образ…");
  });
```

- [ ] **Step 11: Стили**

В конец `app/static/app.css`:

```css
/* Задумка метафоры на экране картинки */
.metaphor { margin: 12px 0; padding: 12px; border-radius: 8px;
  background: var(--surface-2, #f2f4f7); }
.metaphor .kv { margin-bottom: 6px; }
```

- [ ] **Step 12: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check graph/nodes/hitl_image_upload.py app/services/creatives.py app/api/routes_tasks.py tests/unit/test_hitl_image_upload_node.py tests/unit/test_app3_routes.py
git add graph/nodes/hitl_image_upload.py app/services/creatives.py app/api/routes_tasks.py app/templates/creatives.html app/static/creatives.js app/static/app.css tests/unit/test_hitl_image_upload_node.py tests/unit/test_app3_routes.py
git commit -m "App3: разговор о метафоре — задумка на экране картинки + комментарий в петлю графа"
```

---

### Task 8: «Как сделан этот баннер» — рецепт запуска в задаче и на экране

**Files:**
- Modify: `app/services/creatives.py` (`_collect_recipe`, `_finish_terminal`, `_finish`)
- Modify: `app/api/schemas.py` (`TaskOut.recipe`), `app/api/routes_tasks.py` (`_task_recipe`)
- Modify: `app/templates/creatives.html`, `app/static/creatives.js`, `app/static/app.css`
- Test: `tests/unit/test_app3_orchestrator.py` (дописать)

Рецепт — это тот же снимок решений, что уходит в `provenance.json` внутри ZIP, но доступный без скачивания архива и переживающий его удаление ретеншеном (он лежит в `tasks.params`). Он же — источник полей для слоя опыта в Task 9.

- [ ] **Step 1: Написать падающий тест**

Дописать в конец `tests/unit/test_app3_orchestrator.py`:

```python
async def test_collect_recipe_snapshots_human_decisions(tmp_path):
    """Рецепт собирает то, что решил человек: победитель, персона, карточка
    знаний, метафора и её комментарии."""
    Session = await _sessionmaker(tmp_path)

    class _G:
        values = {
            "ranked": [
                {"id": "c7", "slogan": "GPU без очереди", "anchor": "боль: очередь",
                 "desired_outcome": "обучение стартует за минуты"},
            ],
            "winner_id": "c7",
            "personas": [{"segment": "ML-инженеры"}],
            "kb_match": {"slug": "managed-rag", "name": "Managed RAG", "version": 3},
            "metaphor_meta": [{"candidate_id": "c7", "metaphor": "a bridge",
                               "intended_inference": "путь становится коротким",
                               "anti_reading": "не стройка"}],
            "metaphor_comments": ["слишком буквально"],
            "generated_heroes": [{"local_path": "/tmp/h.png"}],
        }

        async def aget_state(self, config):
            return _FakeState(self.values)

    svc = _service(Session, _G(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    recipe = await svc._collect_recipe("uid1")
    assert recipe["winner_id"] == "c7"
    assert recipe["slogan"] == "GPU без очереди"
    assert recipe["anchor"] == "боль: очередь"
    assert recipe["persona_segment"] == "ML-инженеры"
    assert recipe["kb_source"] == {"slug": "managed-rag", "name": "Managed RAG", "version": 3}
    assert recipe["metaphor"] == "a bridge"
    assert recipe["metaphor_comments"] == ["слишком буквально"]
    assert recipe["hero_source"] == "generated"


async def test_collect_recipe_survives_broken_checkpoint(tmp_path):
    """Рецепт — best-effort: сбой чтения чекпоинта не должен ломать финиш."""
    Session = await _sessionmaker(tmp_path)

    class _Boom:
        async def aget_state(self, config):
            raise RuntimeError("checkpoint gone")

    svc = _service(Session, _Boom(), tmp=tmp_path / "tmp", results_dir=tmp_path / "res")
    assert await svc._collect_recipe("uid2") == {}
```

- [ ] **Step 2: Запустить — падает**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_orchestrator.py -q -k recipe`
Expected: FAIL — `AttributeError: 'CreativesService' object has no attribute '_collect_recipe'`

- [ ] **Step 3: Реализовать сбор рецепта**

В `app/services/creatives.py` добавить после `_collect_cards`:

```python
    async def _collect_recipe(self, task_uid: str) -> dict:
        """Снимок решений запуска — «как сделан этот баннер».

        Те же поля, что в provenance.json внутри ZIP, но живут в tasks.params:
        архив ретеншен удалит через сутки, а объяснение результата останется.
        Best-effort: сбой чтения чекпоинта → пустой рецепт, не сломанный финиш.
        """
        try:
            snapshot = await self.graph.aget_state(self._config(task_uid))
            values = dict(snapshot.values or {})
        except Exception:  # noqa: BLE001
            log.warning("collect_recipe_failed", task_uid=task_uid, exc_info=True)
            return {}
        ranked = values.get("ranked") or []
        winner = ranked[0] if ranked and isinstance(ranked[0], dict) else {}
        meta = (values.get("metaphor_meta") or [{}])[0] or {}
        persona = (values.get("personas") or [None])[0] or {}
        heroes = values.get("generated_heroes")
        return {
            "kb_source": values.get("kb_match"),
            "persona_segment": persona.get("segment", ""),
            "winner_id": values.get("winner_id"),
            "slogan": winner.get("slogan", ""),
            "anchor": winner.get("anchor", ""),
            "desired_outcome": winner.get("desired_outcome", ""),
            "metaphor": meta.get("metaphor", ""),
            "intended_inference": meta.get("intended_inference", ""),
            "anti_reading": meta.get("anti_reading", ""),
            "metaphor_comments": values.get("metaphor_comments") or [],
            "hero_source": "generated" if heroes else ("uploaded" if values.get("image") else "none"),
        }
```

В `_finish_terminal` собрать и передать:

```python
        result_url = self._collect_results(task_uid, final)
        cards = await self._collect_cards(task_uid)
        recipe = await self._collect_recipe(task_uid)
        await reporter.done(result_url=result_url)
        meta = {"ratios": ["300x600"], "count": len(final.get("rendered_files") or [])}
        await self._finish(
            task_uid, "done", result_url=result_url, meta=meta, cards=cards, recipe=recipe
        )
```

В `_finish` добавить параметр и запись:

```python
        cards: list[dict] | None = None,
        recipe: dict | None = None,
        reason: str | None = None,
```
```python
            if cards:
                # reassign (not mutate) so the JSON column change is tracked
                task.params = {**(task.params or {}), "cards": cards}
            if recipe:
                task.params = {**(task.params or {}), "recipe": recipe}
```

- [ ] **Step 4: Запустить — проходит**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_app3_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Отдать рецепт в API**

В `app/api/schemas.py`, `TaskOut`, добавить поле:

```python
    cards: list[dict[str, Any]] = Field(default_factory=list)
    # «Как сделан этот баннер»: победитель, персона, карточка знаний, метафора.
    recipe: dict[str, Any] = Field(default_factory=dict)
```

В `app/api/routes_tasks.py` добавить рядом с `_task_cards`:

```python
_RECIPE_KEYS = (
    "kb_source", "persona_segment", "winner_id", "slogan", "anchor",
    "desired_outcome", "metaphor", "intended_inference", "anti_reading",
    "metaphor_comments", "hero_source",
)


def _task_recipe(t: models.Task) -> dict:
    """Снимок решений запуска, whitelist — как у карточек."""
    recipe = (t.params or {}).get("recipe")
    if not isinstance(recipe, dict):
        return {}
    return {k: recipe[k] for k in _RECIPE_KEYS if k in recipe}
```

и в `_task_out`:

```python
        cards=_task_cards(t),
        recipe=_task_recipe(t),
```

- [ ] **Step 6: Панель на экране результата**

В `app/templates/creatives.html`, внутри `#resultsPanel` после `<div id="resultMsg"></div>`:

```html
      <details class="recipe hidden" id="recipePanel">
        <summary>Как сделан этот баннер</summary>
        <div id="recipeBody"></div>
      </details>
```

В `app/static/creatives.js`, в `onDone`, после отрисовки ссылки:

```javascript
    $("startBtn").disabled = false;
    loadRecipe(d.task_uid || taskUid);
    loadRecentTasks();
```

и функции рядом:

```javascript
  // Рецепт лежит в задаче (не в SSE-событии) — дочитываем его после финиша.
  async function loadRecipe(uid) {
    const box = $("recipePanel");
    hide(box);
    if (!uid) return;
    try {
      const r = await fetch(`${P}/api/tasks/${uid}`);
      if (!r.ok) return;
      const t = await r.json();
      const html = recipeHtml(t.recipe);
      if (!html) return;
      $("recipeBody").innerHTML = html;
      box.classList.remove("hidden");
    } catch (_) {}
  }

  function recipeHtml(rec) {
    if (!rec || !Object.keys(rec).length) return "";
    const kbs = rec.kb_source;
    const HERO = { generated: "сгенерирован на сервере", uploaded: "загружен вручную", none: "нет" };
    const comments = Array.isArray(rec.metaphor_comments) ? rec.metaphor_comments.join("; ") : "";
    return (
      kv("карточка знаний", kbs ? `${kbs.name} (версия ${kbs.version})` : "не использовалась") +
      kv("персона", rec.persona_segment) +
      kv("ведущий текст", rec.slogan) +
      kv("якорь персоны", rec.anchor) +
      kv("что человек получит", rec.desired_outcome) +
      kv("образ", rec.metaphor) +
      kv("читатель должен подумать", rec.intended_inference) +
      kv("не должно читаться как", rec.anti_reading) +
      kv("комментарии к образу", comments) +
      kv("hero", HERO[rec.hero_source] || rec.hero_source)
    );
  }
```

- [ ] **Step 7: Стили**

В конец `app/static/app.css`:

```css
/* Рецепт запуска на экране результата */
.recipe { margin: 12px 0; font-size: 13px; }
.recipe summary { cursor: pointer; font-weight: 600; }
```

- [ ] **Step 8: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/services/creatives.py app/api/routes_tasks.py app/api/schemas.py tests/unit/test_app3_orchestrator.py
git add app/services/creatives.py app/api/routes_tasks.py app/api/schemas.py app/templates/creatives.html app/static/creatives.js app/static/app.css tests/unit/test_app3_orchestrator.py
git commit -m "App3: рецепт запуска в задаче + панель «Как сделан этот баннер»"
```

---

### Task 9: Отметка исхода — таблица kb_runs и единственный вход в слой опыта

**Files:**
- Modify: `app/db/models.py` (`KbRun`)
- Create: `app/kb/experience.py`
- Modify: `app/api/schemas.py` (`OutcomeIn`), `app/api/routes_tasks.py` (роут `set_outcome`)
- Modify: `app/templates/creatives.html`, `app/static/creatives.js`
- Test: `tests/unit/test_kb_experience.py`, `tests/unit/test_app3_routes.py`

Запись в опыт возможна ТОЛЬКО отсюда: без отметки человека строки нет, значит опыт не копит шум из брошенных запусков (решение спеки 2026-08-07).

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/unit/test_kb_experience.py`:

```python
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
```

- [ ] **Step 2: Запустить — падает**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_experience.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.kb.experience'`

- [ ] **Step 3: Модель**

В конец `app/db/models.py`:

```python
class KbRun(Base):
    """Библиотека знаний, слой «опыт» — один ряд на ОТМЕЧЕННЫЙ человеком запуск.

    Пишется только когда на экране результата нажали «пошло в кампанию» или
    «отклонили»: без отметки строки нет, поэтому опыт не копит шум из брошенных
    и случайных запусков. Читается при сборке промптов (experience_block)."""

    __tablename__ = "kb_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(64), index=True, default="")
    outcome: Mapped[str] = mapped_column(String(16))  # shipped | rejected
    comment: Mapped[str] = mapped_column(Text, default="")
    slogan: Mapped[str] = mapped_column(Text, default="")
    anchor: Mapped[str] = mapped_column(Text, default="")
    desired_outcome: Mapped[str] = mapped_column(Text, default="")
    metaphor: Mapped[str] = mapped_column(Text, default="")
    persona_segment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
```

- [ ] **Step 4: Запись исхода**

Создать `app/kb/experience.py`:

```python
"""Слой «опыт» библиотеки знаний: kb_runs → блок фактов в промптах.

Симметрично слою фактов (app/kb/store.py): app читает БД и инжектит снапшот в
graph.knowledge — граф не импортирует app. Здесь только запись исхода; чтение
и инжект — Task 13.
"""

from __future__ import annotations

from sqlalchemy import select

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
    kb = (recipe or {}).get("kb_source") or {}
    async with sessionmaker() as s:
        row = (
            await s.execute(
                select(models.KbRun).where(models.KbRun.session_id == session_id)
            )
        ).scalars().first()
        created = row is None
        if row is None:
            row = models.KbRun(session_id=session_id)
            s.add(row)
        row.slug = kb.get("slug") or ""
        row.outcome = outcome
        row.comment = comment or ""
        row.slogan = (recipe or {}).get("slogan") or ""
        row.anchor = (recipe or {}).get("anchor") or ""
        row.desired_outcome = (recipe or {}).get("desired_outcome") or ""
        row.metaphor = (recipe or {}).get("metaphor") or ""
        row.persona_segment = (recipe or {}).get("persona_segment") or ""
        await s.commit()
        return created
```

- [ ] **Step 5: Запустить — проходит**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_experience.py -q`
Expected: PASS (3 теста)

- [ ] **Step 6: Тест роута**

Дописать в конец `tests/unit/test_app3_routes.py`:

```python
def test_outcome_records_and_second_call_updates(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(
            db, "done01", "done", me["id"],
            params={"recipe": {"slogan": "S", "kb_source": {"slug": "rag", "name": "R", "version": 1}}},
        )
        r = c.post(
            "/api/tasks/done01/outcome",
            json={"outcome": "shipped", "comment": "взяли в кампанию"},
            headers=_HDR,
        )
        assert r.status_code == 200 and r.json()["recorded"] is True
        again = c.post("/api/tasks/done01/outcome", json={"outcome": "rejected"}, headers=_HDR)
        assert again.status_code == 200 and again.json()["recorded"] is False
        assert again.json()["outcome"] == "rejected"


def test_outcome_requires_finished_task(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "run01", "running", me["id"])
        r = c.post("/api/tasks/run01/outcome", json={"outcome": "shipped"}, headers=_HDR)
        assert r.status_code == 409


def test_outcome_rejects_unknown_value(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    app = _app(tmp_path, monkeypatch, graph_ok=True)
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        _seed_task(db, "done02", "done", me["id"])
        r = c.post("/api/tasks/done02/outcome", json={"outcome": "maybe"}, headers=_HDR)
        assert r.status_code == 422
```

- [ ] **Step 7: Схема + роут**

В `app/api/schemas.py` добавить в конец:

```python
class OutcomeIn(BaseModel):
    """Отметка исхода на экране результата — единственный вход в слой опыта."""

    outcome: Literal["shipped", "rejected"]
    comment: str = Field(default="", max_length=2000)
```

В `app/api/routes_tasks.py`:

```python
from app.api.schemas import (
    CreateTaskIn,
    OutcomeIn,
    PersonaDecisionIn,
    TaskOut,
    TextDecisionIn,
)
from app.kb.experience import record_outcome
```

```python
@router.post("/tasks/{uid}/outcome")
async def set_outcome(uid: str, body: OutcomeIn, request: Request):
    """Отметить, что стало с результатом: пошёл в кампанию или отклонили.

    Единственный путь записи в слой опыта — поэтому опыт состоит только из
    запусков, о которых человек что-то сказал. Повтор — не ошибка, а смена
    мнения: строка одна на запуск, последняя отметка выигрывает; отвечаем 200
    с recorded=false («не создана, а обновлена»).
    """
    user = await get_current_user(request)
    task = await _load_owned(request, uid, user)
    if task.status != "done":
        raise HTTPException(409, f"task is not finished (status={task.status})")
    recorded = await record_outcome(
        request.app.state.sessionmaker,
        session_id=uid,
        outcome=body.outcome,
        comment=body.comment,
        recipe=_task_recipe(task),
    )
    return {"ok": True, "recorded": recorded, "outcome": body.outcome}
```

- [ ] **Step 8: Запустить — проходит**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit -q`
Expected: PASS

- [ ] **Step 9: Кнопки на экране результата**

В `app/templates/creatives.html`, в `#resultsPanel` после `#recipePanel`:

```html
      <div class="outcome" id="outcomeBox">
        <p class="page-sub">Что стало с этим баннером? Ответ попадёт в опыт и повлияет на следующие запуски по этому продукту.</p>
        <label for="outcomeComment">Комментарий <span class="page-sub">(необязательно)</span></label>
        <textarea id="outcomeComment" placeholder="Почему взяли или почему нет"></textarea>
        <div class="btn-row">
          <button class="btn btn--accent" data-outcome="shipped">Пошёл в кампанию</button>
          <button class="btn" data-outcome="rejected">Отклонили</button>
        </div>
        <div class="status" id="outcomeStatus"></div>
      </div>
```

В `app/static/creatives.js`:

```javascript
  // Отметка исхода: результат уже готов, поэтому ошибка здесь — не сбой
  // задачи, а неудачная запись опыта; текст об этом так и говорит.
  document.querySelectorAll("#outcomeBox [data-outcome]").forEach((btn) => {
    btn.addEventListener("click", () => sendOutcome(btn.dataset.outcome));
  });

  async function sendOutcome(outcome) {
    const box = $("outcomeBox");
    setBusy(box, true);
    const r = await post(`${P}/api/tasks/${taskUid}/outcome`, {
      outcome, comment: $("outcomeComment").value.trim(),
    });
    setBusy(box, false);
    if (r && r.ok) {
      const d = await r.json();
      $("outcomeStatus").textContent = d.recorded ? "Записано в опыт." : "Исход обновлён.";
      return;
    }
    $("outcomeStatus").innerHTML = `<span class="err">Не удалось записать: ${escapeHtml(errText(r ? r.status : 0))}</span>`;
  }
```

и в `onDone` сбрасывать состояние блока:

```javascript
    $("outcomeStatus").textContent = "";
    $("outcomeComment").value = "";
```

- [ ] **Step 10: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/db/models.py app/kb/experience.py app/api/routes_tasks.py app/api/schemas.py tests/unit/test_kb_experience.py tests/unit/test_app3_routes.py
git add app/db/models.py app/kb/experience.py app/api/routes_tasks.py app/api/schemas.py app/templates/creatives.html app/static/creatives.js tests/unit/test_kb_experience.py tests/unit/test_app3_routes.py
git commit -m "App3: kb_runs + POST /outcome — отметка исхода как единственный вход в слой опыта"
```

---

### Task 10: Роли — кто может править библиотеку

Библиотеку знаний правит не каждый. Две роли и один флаг: `admin` (правит карточки и раздаёт роли), `user` (только читает), плюс флаг `kb_editor` (правит карточки, роли не раздаёт). Отсутствие строки в `user_roles` = обычный пользователь, поэтому миграции не нужны: на существующей БД все остаются читателями. Первый админ поднимается из `APP3_BOOTSTRAP_ADMIN` — иначе на пустой БД роли раздать некому.

**Files:**
- Modify: `app/db/models.py` (в конец файла)
- Create: `app/auth/roles.py`
- Modify: `app/config.py:37` (рядом с `dev_user`), `app/main.py:186-203` (`_resolve_settings`)
- Modify: `app/api/schemas.py:9-12` (`UserOut`), `app/api/routes_auth.py`
- Create: `app/api/routes_admin.py`
- Modify: `app/main.py:160-172` (регистрация роутера)
- Test: `tests/unit/test_roles.py`

- [ ] **Step 1: Пишем падающий тест на резолв доступа**

Создать `tests/unit/test_roles.py`:

```python
"""Роли App3: user по умолчанию, bootstrap-админ, kb_editor, /api/me и /api/admin/roles."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.auth.roles import resolve_access  # noqa: E402
from app.db import models  # noqa: E402
from app.db.database import init_db, make_engine, make_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "5", "X-User-Email": "u@cloud.ru"}
_BOSS = {"X-User-Id": "1", "X-User-Email": "boss@cloud.ru"}


@pytest.fixture
async def Session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def _user(Session, email: str) -> models.User:
    async with Session() as s:
        u = models.User(gateway_user_id=email, yandex_id=f"y:{email}", email=email)
        s.add(u)
        await s.commit()
        return u


async def test_no_row_means_plain_user(Session):
    u = await _user(Session, "u@cloud.ru")
    async with Session() as s:
        acc = await resolve_access(s, u, bootstrap_admin="boss@cloud.ru")
    assert acc.role == "user"
    assert acc.is_admin is False
    assert acc.can_edit_kb is False


async def test_bootstrap_admin_gets_row_once(Session):
    u = await _user(Session, "Boss@Cloud.ru")  # регистр не важен
    async with Session() as s:
        acc = await resolve_access(s, u, bootstrap_admin="boss@cloud.ru")
        await s.commit()
    assert acc.is_admin is True and acc.can_edit_kb is True
    async with Session() as s:
        row = await s.get(models.UserRole, u.id)
    assert row is not None and row.role == "admin" and row.updated_by == "bootstrap"


async def test_kb_editor_flag_allows_edit_but_not_admin(Session):
    u = await _user(Session, "editor@cloud.ru")
    async with Session() as s:
        s.add(models.UserRole(user_id=u.id, role="user", kb_editor=True))
        await s.commit()
    async with Session() as s:
        acc = await resolve_access(s, u)
    assert acc.can_edit_kb is True
    assert acc.is_admin is False
```

- [ ] **Step 2: Запустить — упадёт**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_roles.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth.roles'`

- [ ] **Step 3: Модель `UserRole`**

В конец `app/db/models.py`:

```python
class UserRole(Base):
    """Роль пользователя App3 (одна строка на пользователя).

    Отсутствие строки = обычный пользователь: на существующей БД никто не
    теряет доступ и миграция не нужна. `role` — admin|user (админ раздаёт
    роли), `kb_editor` — отдельный флаг «правит карточки знаний», чтобы
    редактор библиотеки не получал заодно право раздавать доступы."""

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="user")
    kb_editor: Mapped[bool] = mapped_column(default=False)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
```

- [ ] **Step 4: `app/auth/roles.py`**

```python
"""Роли и права: кто читает библиотеку знаний, а кто её правит.

Роль лежит в `user_roles` (PK = users.id); отсутствие строки = обычный
пользователь. Первый админ поднимается из APP3_BOOTSTRAP_ADMIN при первом же
обращении этого email — на пустой БД иначе некому раздать роли.

Гейты (`require_admin`, `require_kb_edit`) — обычные async-функции, а не
FastAPI-Depends: остальные роуты App3 так же вызывают `get_current_user(request)`
руками, и смешивать два стиля в одном приложении хуже, чем повторить `await`.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db import models

ADMIN = "admin"
USER = "user"
ROLES = (ADMIN, USER)


@dataclass(frozen=True)
class Access:
    role: str
    kb_editor: bool

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    @property
    def can_edit_kb(self) -> bool:
        return self.is_admin or self.kb_editor


async def resolve_access(
    session: AsyncSession, user: models.User, *, bootstrap_admin: str = ""
) -> Access:
    row = await session.get(models.UserRole, user.id)
    if row is None:
        if bootstrap_admin and _same_email(user.email, bootstrap_admin):
            row = models.UserRole(user_id=user.id, role=ADMIN, updated_by="bootstrap")
            session.add(row)
            await session.flush()
        else:
            return Access(role=USER, kb_editor=False)
    return Access(role=row.role, kb_editor=bool(row.kb_editor))


def _same_email(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


async def current_access(request: Request) -> tuple[models.User, Access]:
    user = await get_current_user(request)
    cfg = getattr(request.app.state, "settings", {}) or {}
    Session = request.app.state.sessionmaker
    async with Session() as s:
        access = await resolve_access(
            s, user, bootstrap_admin=cfg.get("bootstrap_admin", "")
        )
        await s.commit()
    return user, access


async def require_admin(request: Request) -> models.User:
    user, access = await current_access(request)
    if not access.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


async def require_kb_edit(request: Request) -> models.User:
    user, access = await current_access(request)
    if not access.can_edit_kb:
        raise HTTPException(status_code=403, detail="kb edit not allowed")
    return user
```

- [ ] **Step 5: Запустить — три теста зелёные**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_roles.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Настройка `APP3_BOOTSTRAP_ADMIN`**

В `app/config.py` после `dev_user` (строка 37):

```python
    # Первый админ библиотеки знаний: email, который получает роль admin при
    # первом обращении. Нужен ровно один раз — дальше админ раздаёт роли из UI.
    bootstrap_admin: str = Field("", alias="APP3_BOOTSTRAP_ADMIN")
```

В `app/main.py`, в словарь `_resolve_settings` рядом с `"dev_user"`:

```python
        "bootstrap_admin": settings.bootstrap_admin,
```

- [ ] **Step 7: Тест на `/api/me` с ролью и на админский роутер**

Дописать в `tests/unit/test_roles.py`:

```python
def _app(tmp_path, monkeypatch, **extra):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    cfg = {"db_url": f"sqlite+aiosqlite:///{tmp_path / 'roles.db'}"}
    cfg.update(extra)
    return create_app(cfg)


def test_me_exposes_role(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        me = c.get("/api/me", headers=_HDR).json()
        assert me["role"] == "user" and me["can_edit_kb"] is False
        boss = c.get("/api/me", headers=_BOSS).json()
        assert boss["role"] == "admin" and boss["can_edit_kb"] is True


def test_admin_roles_list_and_grant(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        c.get("/api/me", headers=_HDR)      # оба пользователя появились в БД
        c.get("/api/me", headers=_BOSS)     # и админ поднялся из bootstrap

        assert c.get("/api/admin/roles", headers=_HDR).status_code == 403

        r = c.get("/api/admin/roles", headers=_BOSS)
        assert r.status_code == 200
        emails = {row["email"]: row for row in r.json()}
        assert emails["u@cloud.ru"]["role"] == "user"
        assert emails["boss@cloud.ru"]["role"] == "admin"

        r = c.put(
            "/api/admin/roles",
            json={"email": "u@cloud.ru", "role": "user", "kb_editor": True},
            headers=_BOSS,
        )
        assert r.status_code == 200 and r.json()["kb_editor"] is True
        assert c.get("/api/me", headers=_HDR).json()["can_edit_kb"] is True


def test_admin_roles_rejects_unknown_email_and_role(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, bootstrap_admin="boss@cloud.ru")
    with TestClient(app) as c:
        c.get("/api/me", headers=_BOSS)
        r = c.put(
            "/api/admin/roles",
            json={"email": "ghost@cloud.ru", "role": "admin"},
            headers=_BOSS,
        )
        assert r.status_code == 404
        r = c.put(
            "/api/admin/roles",
            json={"email": "boss@cloud.ru", "role": "root"},
            headers=_BOSS,
        )
        assert r.status_code == 422
```

- [ ] **Step 8: Запустить — упадёт**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_roles.py -q`
Expected: FAIL — `KeyError: 'role'` в `test_me_exposes_role` и 404 на `/api/admin/roles`

- [ ] **Step 9: Роль в `/api/me`**

`app/api/schemas.py` — расширить `UserOut`:

```python
class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str = "user"
    can_edit_kb: bool = False
```

и добавить схемы админского роутера (рядом с остальными In-схемами):

```python
class RoleIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: Literal["admin", "user"] = "user"
    kb_editor: bool = False


class RoleOut(BaseModel):
    email: str
    role: str
    kb_editor: bool
```

(`Literal` уже импортирован в модуле схем; если импорта нет — добавить `from typing import Literal`.)

`app/api/routes_auth.py` целиком:

```python
"""Auth route: current user resolved from the trusted gateway header (App1)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import UserOut
from app.auth.roles import current_access

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/me", response_model=UserOut)
async def me(request: Request):
    # Роль отдаём вместе с профилем: страница библиотеки решает по ней, что
    # показывать — только чтение или кнопки правки.
    user, access = await current_access(request)
    return UserOut(
        id=user.id, email=user.email, display_name=user.display_name,
        role=access.role, can_edit_kb=access.can_edit_kb,
    )
```

- [ ] **Step 10: Роутер `/api/admin/roles`**

Создать `app/api/routes_admin.py`:

```python
"""Админский роутер: раздача ролей. Доступ — только role=admin."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.schemas import RoleIn, RoleOut
from app.auth.roles import require_admin
from app.db import models

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(request: Request):
    await require_admin(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        users = (
            await s.execute(select(models.User).order_by(models.User.email))
        ).scalars().all()
        rows = {
            r.user_id: r
            for r in (await s.execute(select(models.UserRole))).scalars().all()
        }
    out = []
    for u in users:
        r = rows.get(u.id)
        out.append(
            RoleOut(
                email=u.email,
                role=r.role if r else "user",
                kb_editor=bool(r.kb_editor) if r else False,
            )
        )
    return out


@router.put("/roles", response_model=RoleOut)
async def set_role(request: Request, body: RoleIn):
    admin = await require_admin(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        target = (
            await s.execute(
                select(models.User).where(models.User.email == body.email)
            )
        ).scalars().first()
        if target is None:
            # Пользователь заводится в БД при первом входе через шлюз — роль
            # заранее выдать некому, и молча создавать пустышку хуже, чем 404.
            raise HTTPException(status_code=404, detail="user not found")
        row = await s.get(models.UserRole, target.id)
        if row is None:
            row = models.UserRole(user_id=target.id)
            s.add(row)
        row.role = body.role
        row.kb_editor = body.kb_editor
        row.updated_by = admin.email
        await s.commit()
    return RoleOut(email=target.email, role=body.role, kb_editor=body.kb_editor)
```

Зарегистрировать в `app/main.py` (в блоке импортов роутеров и в `include_router`, сразу после auth):

```python
    from app.api.routes_admin import router as admin_router
```
```python
    app.include_router(admin_router)
```

- [ ] **Step 11: Запустить — зелёное**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_roles.py tests/unit/test_app3_routes.py -q`
Expected: PASS

- [ ] **Step 12: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/auth/roles.py app/api/routes_admin.py app/api/routes_auth.py app/api/schemas.py app/db/models.py app/config.py app/main.py tests/unit/test_roles.py
git add app/auth/roles.py app/api/routes_admin.py app/api/routes_auth.py app/api/schemas.py app/db/models.py app/config.py app/main.py tests/unit/test_roles.py
git commit -m "App3: роли admin/user + флаг kb_editor, bootstrap-админ из env, /api/admin/roles"
```

---

### Task 11: Правка карточек знаний + свежий `kb_match` в графе

Запись в библиотеку — под ролями из Task 10. Ключевое требование спеки: правка видна **следующему же запуску**, без рестарта сервиса. Значит после каждой записи дёргаем `refresh_catalog` (он инжектит снапшот БД в `graph.knowledge`), а узел `derive_persona`, который и так перечитывает карточку по slug, обязан вернуть свежий `kb_match` в state — иначе паспорт (`provenance.kb_source`) и рецепт покажут версию, которой уже нет. Это пункт (f) чеклиста финального ревью Плана 1.

**Files:**
- Modify: `app/api/schemas.py` (добавить `KbProductIn`, `KbProductPatch`, `KbVersionOut`)
- Modify: `app/api/routes_kb.py` (Task 2)
- Modify: `graph/nodes/derive_persona.py:48-54,90-92`
- Test: `tests/unit/test_kb_routes.py`, `tests/unit/test_derive_persona.py`

- [ ] **Step 1: Тесты на запись — доступ, версии, история**

Дописать в `tests/unit/test_kb_routes.py` (заменив `_app` на версию с ролями):

```python
_BOSS = {"X-User-Id": "1", "X-User-Email": "boss@cloud.ru"}


def _admin_app(tmp_path, monkeypatch):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'kbw.db'}",
        "bootstrap_admin": "boss@cloud.ru",
    })


def _first_slug(c) -> str:
    return c.get("/api/kb/products", headers=_BOSS).json()[0]["slug"]


def test_write_requires_role(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        slug = _first_slug(c)
        r = c.put(f"/api/kb/products/{slug}", json={"tagline": "нельзя"}, headers=_HDR)
        assert r.status_code == 403
        r = c.post(
            "/api/kb/products",
            json={"slug": "new-one", "name": "New One"},
            headers=_HDR,
        )
        assert r.status_code == 403
        # История — тоже редакторский инструмент
        assert c.get(f"/api/kb/products/{slug}/history", headers=_HDR).status_code == 403
        # но сам каталог читают все
        assert c.get("/api/kb/products", headers=_HDR).status_code == 200


def test_update_appends_version_and_history(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        slug = _first_slug(c)
        r = c.put(
            f"/api/kb/products/{slug}",
            json={"tagline": "Новый тэглайн", "block1": "## Блок 1. Что это\nНовое."},
            headers=_BOSS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 2 and body["tagline"] == "Новый тэглайн"
        assert body["updated_by"] == "boss@cloud.ru"

        h = c.get(f"/api/kb/products/{slug}/history", headers=_BOSS).json()
        assert [v["version"] for v in h] == [2, 1]


def test_create_and_archive(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        r = c.post(
            "/api/kb/products",
            json={
                "slug": "evolution-demo", "name": "Evolution Demo",
                "aliases": ["демо"], "tagline": "Демо-продукт",
                "block1": "## Блок 1. Что это\nДемо.",
            },
            headers=_BOSS,
        )
        assert r.status_code == 201 and r.json()["version"] == 1

        # повторный slug — конфликт, а не тихая перезапись
        assert c.post(
            "/api/kb/products",
            json={"slug": "evolution-demo", "name": "Dup"},
            headers=_BOSS,
        ).status_code == 409

        assert c.put(
            "/api/kb/products/evolution-demo", json={"archived": True}, headers=_BOSS
        ).status_code == 200
        slugs = {p["slug"] for p in c.get("/api/kb/products", headers=_BOSS).json()}
        assert "evolution-demo" not in slugs
        slugs = {
            p["slug"]
            for p in c.get(
                "/api/kb/products?include_archived=true", headers=_BOSS
            ).json()
        }
        assert "evolution-demo" in slugs


def test_update_unknown_slug_404(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        assert c.put(
            "/api/kb/products/ghost", json={"tagline": "x"}, headers=_BOSS
        ).status_code == 404
```

- [ ] **Step 2: Запустить — упадёт**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py -q`
Expected: FAIL — 405/404 на POST/PUT `/api/kb/products`

- [ ] **Step 3: Схемы записи**

В `app/api/schemas.py` рядом с `KbProductOut`:

```python
class KbProductIn(BaseModel):
    """Новая карточка. Блоки — markdown из шаблона библиотеки; пустые
    допустимы, карточку часто заводят «скелетом» и дописывают позже."""

    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=2, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    tagline: str = Field("", max_length=500)
    block1: str = Field("", max_length=20000)
    block2: str = Field("", max_length=20000)
    block3: str = Field("", max_length=20000)


class KbProductPatch(BaseModel):
    """Правка: только присланные поля меняются, остальные переносятся из
    предыдущей версии (см. store.update_product). None = «не трогать»."""

    name: str | None = Field(None, min_length=2, max_length=128)
    aliases: list[str] | None = None
    tagline: str | None = Field(None, max_length=500)
    block1: str | None = Field(None, max_length=20000)
    block2: str | None = Field(None, max_length=20000)
    block3: str | None = Field(None, max_length=20000)
    archived: bool | None = None


class KbVersionOut(BaseModel):
    """Строка истории: что и когда поменялось. Тела блоков тоже отдаём —
    иначе «посмотреть старую версию» превращается во второй запрос."""

    version: int
    name: str
    tagline: str = ""
    archived: bool = False
    updated_by: str = ""
    updated_at: str | None = None
    block1: str = ""
    block2: str = ""
    block3: str = ""
```

- [ ] **Step 4: Роуты записи и истории**

В `app/api/routes_kb.py` — расширить импорты и дописать три роута:

```python
from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import KbProductIn, KbProductOut, KbProductPatch, KbVersionOut
from app.auth.deps import get_current_user
from app.auth.roles import require_kb_edit
from app.db import models
from app.kb.store import (
    KbConflict,
    KbNotFound,
    create_product,
    history,
    latest_rows,
    refresh_catalog,
    update_product,
)
```

```python
def version_out(r: models.KbProduct) -> KbVersionOut:
    return KbVersionOut(
        version=r.version,
        name=r.name,
        tagline=r.tagline or "",
        archived=bool(r.archived),
        updated_by=r.updated_by or "",
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
        block1=r.block1 or "",
        block2=r.block2 or "",
        block3=r.block3 or "",
    )


async def _latest(Session, slug: str) -> models.KbProduct:
    rows = await history(Session, slug)
    if not rows:
        raise HTTPException(status_code=404, detail="product not found")
    return rows[0]


@router.post("/products", response_model=KbProductOut, status_code=201)
async def create(request: Request, body: KbProductIn):
    editor = await require_kb_edit(request)
    Session = request.app.state.sessionmaker
    try:
        await create_product(
            Session,
            slug=body.slug,
            fields=body.model_dump(exclude={"slug"}),
            updated_by=editor.email,
        )
    except KbConflict as exc:
        # Тихо перезаписать чужую карточку — худший из возможных исходов:
        # правка ушла бы в граф, а автор оригинала об этом не узнал.
        raise HTTPException(status_code=409, detail="slug already exists") from exc
    # Правка должна быть видна СЛЕДУЮЩЕМУ запуску без рестарта — снапшот БД
    # инжектим в graph.knowledge сразу после записи.
    await refresh_catalog(Session)
    return kb_out(await _latest(Session, body.slug))


@router.put("/products/{slug}", response_model=KbProductOut)
async def update(request: Request, slug: str, body: KbProductPatch):
    editor = await require_kb_edit(request)
    Session = request.app.state.sessionmaker
    try:
        await update_product(
            Session,
            slug=slug,
            fields=body.model_dump(exclude_none=True),
            updated_by=editor.email,
        )
    except KbNotFound as exc:
        raise HTTPException(status_code=404, detail="product not found") from exc
    await refresh_catalog(Session)
    return kb_out(await _latest(Session, slug))


@router.get("/products/{slug}/history", response_model=list[KbVersionOut])
async def product_history(request: Request, slug: str):
    # История — редакторский инструмент (спека отдаёт её админу): читателю
    # важен текущий текст, а не кто и когда его правил.
    await require_kb_edit(request)
    rows = await history(request.app.state.sessionmaker, slug)
    if not rows:
        raise HTTPException(status_code=404, detail="product not found")
    return [version_out(r) for r in rows]
```

- [ ] **Step 5: Запустить — зелёное**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py -q`
Expected: PASS

- [ ] **Step 6: Обязательный e2e-тест «правка доехала до графа»**

Это проверка того, ради чего вся задача: после PUT граф должен видеть новую версию **без рестарта**. Дописать в `tests/unit/test_kb_routes.py`:

```python
def test_edit_reaches_graph_catalog_without_restart(tmp_path, monkeypatch):
    """PUT → version+1 → refresh_catalog → graph.knowledge отдаёт новый текст."""
    from graph import knowledge

    try:
        with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
            slug = _first_slug(c)
            before = knowledge.get_by_slug(slug)
            assert before is not None and before.version == 1

            r = c.put(
                f"/api/kb/products/{slug}",
                json={"tagline": "Свежий тэглайн из UI"},
                headers=_BOSS,
            )
            assert r.status_code == 200 and r.json()["version"] == 2

            after = knowledge.get_by_slug(slug)
            assert after is not None
            assert after.version == 2
            assert after.tagline == "Свежий тэглайн из UI"
    finally:
        # lifespan инжектил снапшот в глобальный модуль графа — снимаем,
        # чтобы соседние тесты видели файловый каталог.
        knowledge.set_catalog(None)
```

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py -q`
Expected: PASS

- [ ] **Step 7: Тест на свежий `kb_match` из `derive_persona`** (пункт f чеклиста)

Сначала — расширить хелпер `_make_doc` в `tests/unit/test_derive_persona.py:76-86` параметром версии (`ProductDoc.version` уже есть, default 1, поэтому существующие вызовы не меняются):

```python
def _make_doc(slug: str, name: str, version: int = 1) -> ProductDoc:
    return ProductDoc(
        slug=slug,
        name=name,
        aliases=(name,),
        tagline=f"{name} tagline",
        body=(
            "## Блок 1. Что это\nОписание продукта.\n\n"
            "## Блок 3. Аудитории\nАудитория-специфично для " + name + ".\n"
        ),
        version=version,
    )
```

Затем дописать в конец файла:

```python
async def test_derive_persona_returns_refreshed_kb_match(captured):
    """Карточку могли отредактировать после understand_product — узел
    возвращает kb_match той версии, текст которой реально ушёл в промпт."""
    doc = _make_doc("evolution-ml-inference", "Evolution ML Inference v2", version=2)
    prev = knowledge._catalog_override
    try:
        knowledge.set_catalog((doc,))
        state = _state(
            kb_match={
                "slug": "evolution-ml-inference",
                "name": "Evolution ML Inference",
                "version": 1,
            }
        )
        out = await mod.derive_persona(state)
    finally:
        knowledge.set_catalog(prev)

    assert out["kb_match"]["slug"] == "evolution-ml-inference"
    assert out["kb_match"]["name"] == "Evolution ML Inference v2"
    assert out["kb_match"]["version"] == 2


async def test_derive_persona_keeps_stale_kb_match_when_card_gone(captured):
    """Карточку заархивировали между узлами — врать про свежесть нельзя,
    но и терять след источника тоже: возвращаем то, что было в state."""
    prev = knowledge._catalog_override
    try:
        knowledge.set_catalog(())
        state = _state(kb_match={"slug": "gone", "name": "Gone", "version": 1})
        out = await mod.derive_persona(state)
    finally:
        knowledge.set_catalog(prev)

    assert out["kb_match"] == {"slug": "gone", "name": "Gone", "version": 1}


async def test_derive_persona_without_kb_match_writes_nothing(captured):
    out = await mod.derive_persona(_state(kb_match=None))
    assert "kb_match" not in out
```

- [ ] **Step 8: Запустить — упадёт**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_derive_persona.py -q`
Expected: FAIL — `KeyError: 'kb_match'` (узел его не возвращает)

- [ ] **Step 9: Узел возвращает свежий `kb_match`**

В `graph/nodes/derive_persona.py` заменить блок 48-54:

```python
    kb_match = state.get("kb_match")
    # Карточка могла обновиться между узлами — берём свежую версию по slug и
    # возвращаем её в state: паспорт и рецепт должны показывать ту версию,
    # текст которой реально ушёл в промпт, а не ту, что нашёл understand_product.
    doc = knowledge.get_by_slug(kb_match["slug"]) if kb_match else None
    if kb_match and doc is None:
        log.warning(
            "kb_match_stale", session_id=state.get("session_id"), slug=kb_match["slug"]
        )
    fresh_match = (
        {"slug": doc.slug, "name": doc.name, "version": doc.version}
        if doc
        else kb_match
    )
```

и `return` (строки 90-92):

```python
    out: dict = {"personas": [p.model_dump() for p in persona_set.personas]}
    if fresh_match is not None:
        out["kb_match"] = fresh_match
    return out
```

- [ ] **Step 10: Запустить — зелёное**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_derive_persona.py tests/unit/test_kb_routes.py tests/unit/test_kb_store.py -q`
Expected: PASS

- [ ] **Step 11: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/api/routes_kb.py app/api/schemas.py graph/nodes/derive_persona.py tests/unit/test_kb_routes.py tests/unit/test_derive_persona.py
git add app/api/routes_kb.py app/api/schemas.py graph/nodes/derive_persona.py tests/unit/test_kb_routes.py tests/unit/test_derive_persona.py
git commit -m "App3: правка карточек знаний под ролями + derive_persona возвращает свежий kb_match"
```

---

### Task 12: Страница библиотеки знаний

Отдельная страница `/creatives/library`: читают все (общий словарь команды), правят — `admin`/`kb_editor`, роли раздаёт только `admin`. Страница самодостаточная, как `webinar.html`: тот же канон-топбар, тот же `app.css`, свой `library.js`.

**Files:**
- Modify: `app/api/routes_pages.py` (роут `/library`)
- Create: `app/templates/library.html`
- Create: `app/static/library.js`
- Modify: `app/templates/creatives.html:12-18`, `app/templates/webinar.html:12-18` (ссылка в топнаве)
- Test: `tests/unit/test_kb_routes.py`

- [ ] **Step 1: Тест на страницу**

Дописать в `tests/unit/test_kb_routes.py`:

```python
def test_library_page_renders_and_requires_auth(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        assert c.get("/library").status_code == 401
        r = c.get("/library", headers=_BOSS)
        assert r.status_code == 200
        assert "library.js" in r.text
        assert "Библиотека знаний" in r.text
```

- [ ] **Step 2: Запустить — упадёт**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py::test_library_page_renders_and_requires_auth -q`
Expected: FAIL — 404 на `/library`

- [ ] **Step 3: Роут страницы**

В конец `app/api/routes_pages.py`:

```python
@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    """Библиотека знаний: карточки продуктов, история версий, роли."""
    user = await get_current_user(request)
    cfg = getattr(request.app.state, "settings", {}) or {}
    prefix = cfg.get("prefix", "/creatives")
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="library.html",
        context={"email": user.email, "prefix": prefix},
    )
```

- [ ] **Step 4: Шаблон `app/templates/library.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Библиотека знаний — Cloud.ru Design</title>
  <link rel="stylesheet" href="{{ prefix }}/static/app.css?v=20260808v1">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/">Cloud.ru <span>Design</span></a>
    <nav class="topnav">
      <a href="/images" class="topnav__link">Картинки</a>
      <a href="/slides" class="topnav__link">Слайды</a>
      <a href="/creatives" class="topnav__link">Креативы</a>
      <a href="/present" class="topnav__link">Онлайн трансляции презентаций</a>
      <a href="/creatives/webinar" class="topnav__link">Вебинары</a>
      <a href="/creatives/library" class="topnav__link is-active">Библиотека</a>
    </nav>
    <div class="topbar__auth">
      <span class="topbar__user">{{ email }}</span>
      <form action="/logout" method="post" class="logout-form"><button type="submit">Выйти</button></form>
    </div>
  </header>

  <main class="wrap">
    <h1 class="page-title">Библиотека знаний</h1>
    <p class="page-sub">Факты о продуктах, из которых пайплайн строит предложения. Что здесь написано — то и обещают баннеры: правка карточки меняет следующий же запуск.</p>

    <div class="workspace">
    <div class="workspace__controls">

    <section class="panel" id="listPanel">
      <h2>Продукты</h2>
      <div class="task-list" id="kbList"></div>
      <label class="page-sub" for="showArchived">
        <input type="checkbox" id="showArchived"> показывать архивные
      </label>
      <div class="btn-row hidden" id="createRow">
        <button class="btn btn--accent" id="newBtn">Новая карточка</button>
      </div>
      <div class="status" id="listStatus"></div>
    </section>

    <section class="panel hidden" id="rolesPanel">
      <h2>Доступы</h2>
      <p class="page-sub">Кто может править карточки. Пользователь появляется в списке после первого входа.</p>
      <div class="task-list" id="rolesList"></div>
      <div class="status" id="rolesStatus"></div>
    </section>

    </div><!-- /.workspace__controls -->
    <div class="workspace__output">

    <div class="empty" id="emptyState">
      <h2>Как читать эту страницу</h2>
      <p>Каждая карточка — три блока фактов о продукте: что это, что умеет и кому нужно. Пайплайн берёт из них границу достоверности: обещать сверх карточки нельзя.</p>
      <ul>
        <li><b>Блок 1</b> — что это и какую задачу решает.</li>
        <li><b>Блок 2</b> — возможности, сценарии, доказательства.</li>
        <li><b>Блок 3</b> — аудитории и что каждой важно; отсюда растёт персона.</li>
      </ul>
      <p>Выберите продукт слева, чтобы посмотреть текст и историю правок.</p>
    </div>

    <section class="panel hidden" id="cardPanel">
      <h2 id="cardTitle">Карточка</h2>
      <p class="page-sub" id="cardMeta"></p>
      <label for="cardName">Название</label>
      <input type="text" id="cardName" disabled>
      <label for="cardAliases">Синонимы <span class="page-sub">(по одному в строке — по ним пайплайн узнаёт продукт в брифе)</span></label>
      <textarea id="cardAliases" disabled></textarea>
      <label for="cardTagline">Тэглайн</label>
      <textarea id="cardTagline" disabled></textarea>
      <label for="cardBlock1">Блок 1. Что это</label>
      <textarea id="cardBlock1" class="kb-block" disabled></textarea>
      <label for="cardBlock2">Блок 2. Возможности и сценарии</label>
      <textarea id="cardBlock2" class="kb-block" disabled></textarea>
      <label for="cardBlock3">Блок 3. Аудитории</label>
      <textarea id="cardBlock3" class="kb-block" disabled></textarea>
      <div class="btn-row hidden" id="editRow">
        <button class="btn btn--accent" id="saveBtn">Сохранить как новую версию</button>
        <button class="btn btn--danger" id="archiveBtn">В архив</button>
      </div>
      <div class="status" id="cardStatus"></div>
      <details class="cand-why hidden" id="historyBox">
        <summary>История версий</summary>
        <div id="cardHistory"></div>
      </details>
    </section>

    </div><!-- /.workspace__output -->
    </div><!-- /.workspace -->
  </main>

  <script>window.APP_PREFIX = "{{ prefix }}";</script>
  <script src="{{ prefix }}/static/library.js?v=20260808v1"></script>
</body>
</html>
```

- [ ] **Step 5: Ссылка в топнаве двух других страниц**

В `app/templates/creatives.html` и `app/templates/webinar.html`, в блок `<nav class="topnav">` последней строкой:

```html
      <a href="/creatives/library" class="topnav__link">Библиотека</a>
```

Там же поднять cache-buster у `app.css` и у своего `.js` до `?v=20260808v1` (если предыдущие задачи этого ещё не сделали).

- [ ] **Step 6: `app/static/library.js`**

```javascript
/* Библиотека знаний: чтение всем, правка — kb_editor/admin, роли — admin. */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  let me = { role: "user", can_edit_kb: false };
  let items = [];
  let current = null; // slug

  const FIELDS = ["cardName", "cardAliases", "cardTagline", "cardBlock1", "cardBlock2", "cardBlock3"];

  async function jget(url) {
    const r = await fetch(`${P}${url}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function jsend(url, method, body) {
    return fetch(`${P}${url}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function errText(status) {
    if (status === 403) return "нет прав на правку";
    if (status === 404) return "карточка не найдена";
    if (status === 409) return "такой slug уже есть";
    if (status === 422) return "проверьте поля — что-то не прошло валидацию";
    return `ошибка ${status}`;
  }

  // ── список продуктов ─────────────────────────────────
  async function loadList() {
    const q = $("showArchived").checked ? "?include_archived=true" : "";
    try {
      items = await jget(`/api/kb/products${q}`);
    } catch (e) {
      $("listStatus").innerHTML = `<span class="err">Не удалось загрузить каталог.</span>`;
      return;
    }
    $("kbList").innerHTML = items
      .map(
        (p) =>
          `<button class="task-item" data-slug="${esc(p.slug)}">` +
          `<b>${esc(p.name)}</b> <span class="page-sub">v${p.version}` +
          `${p.archived ? " · архив" : ""}</span></button>`
      )
      .join("");
    $("kbList").querySelectorAll("[data-slug]").forEach((b) => {
      b.addEventListener("click", () => openCard(b.dataset.slug));
    });
  }

  // ── карточка ─────────────────────────────────────────
  function openCard(slug) {
    const p = items.find((x) => x.slug === slug);
    if (!p) return;
    current = slug;
    $("cardTitle").textContent = p.name;
    $("cardMeta").textContent =
      `${p.slug} · версия ${p.version}` +
      (p.updated_by ? ` · правил ${p.updated_by}` : "") +
      (p.archived ? " · в архиве" : "");
    $("cardName").value = p.name;
    $("cardAliases").value = (p.aliases || []).join("\n");
    $("cardTagline").value = p.tagline || "";
    $("cardBlock1").value = p.block1 || "";
    $("cardBlock2").value = p.block2 || "";
    $("cardBlock3").value = p.block3 || "";
    $("cardStatus").textContent = "";
    $("cardPanel").classList.remove("hidden");
    $("emptyState").classList.add("hidden");
    // История доступна только редакторам — читателю не показываем и блок,
    // иначе он раскрывает пустой details и видит ошибку доступа.
    if (me.can_edit_kb) loadHistory(slug);
  }

  async function loadHistory(slug) {
    try {
      const rows = await jget(`/api/kb/products/${slug}/history`);
      $("cardHistory").innerHTML = rows
        .map(
          (v) =>
            `<div class="page-sub">v${v.version} · ${esc(v.updated_at || "")}` +
            ` · ${esc(v.updated_by || "seed")}${v.archived ? " · архив" : ""}</div>`
        )
        .join("");
    } catch (e) {
      $("cardHistory").innerHTML = `<span class="err">История недоступна.</span>`;
    }
  }

  function payload() {
    return {
      name: $("cardName").value.trim(),
      aliases: $("cardAliases").value.split("\n").map((s) => s.trim()).filter(Boolean),
      tagline: $("cardTagline").value.trim(),
      block1: $("cardBlock1").value,
      block2: $("cardBlock2").value,
      block3: $("cardBlock3").value,
    };
  }

  $("saveBtn").addEventListener("click", async () => {
    if (!current) return;
    $("cardStatus").textContent = "Сохраняю…";
    const r = await jsend(`/api/kb/products/${current}`, "PUT", payload());
    if (!r.ok) {
      $("cardStatus").innerHTML = `<span class="err">Не сохранилось: ${esc(errText(r.status))}</span>`;
      return;
    }
    const p = await r.json();
    // Правка уже уехала в граф — говорим об этом прямо, это не косметика.
    $("cardStatus").textContent = `Сохранено, версия ${p.version}. Следующий запуск возьмёт её.`;
    await loadList();
    openCard(current);
  });

  $("archiveBtn").addEventListener("click", async () => {
    if (!current) return;
    const r = await jsend(`/api/kb/products/${current}`, "PUT", { archived: true });
    if (!r.ok) {
      $("cardStatus").innerHTML = `<span class="err">Не вышло: ${esc(errText(r.status))}</span>`;
      return;
    }
    $("cardStatus").textContent = "Карточка в архиве. Пайплайн её больше не увидит.";
    await loadList();
  });

  $("newBtn").addEventListener("click", async () => {
    const slug = (prompt("slug новой карточки (латиница, дефисы)") || "").trim();
    if (!slug) return;
    const name = (prompt("Название продукта") || "").trim();
    if (!name) return;
    const r = await jsend("/api/kb/products", "POST", { slug, name });
    if (!r.ok) {
      $("listStatus").innerHTML = `<span class="err">Не создалось: ${esc(errText(r.status))}</span>`;
      return;
    }
    $("listStatus").textContent = "Карточка создана — заполните блоки.";
    await loadList();
    openCard(slug);
  });

  $("showArchived").addEventListener("change", loadList);

  // ── роли (только admin) ──────────────────────────────
  async function loadRoles() {
    let rows;
    try {
      rows = await jget("/api/admin/roles");
    } catch (e) {
      return;
    }
    $("rolesList").innerHTML = rows
      .map(
        (r) =>
          `<div class="task-item"><b>${esc(r.email)}</b> ` +
          `<span class="page-sub">${esc(r.role)}</span> ` +
          `<label class="page-sub"><input type="checkbox" data-email="${esc(r.email)}"` +
          `${r.kb_editor ? " checked" : ""}${r.role === "admin" ? " disabled" : ""}>` +
          ` правит библиотеку</label></div>`
      )
      .join("");
    $("rolesList").querySelectorAll("[data-email]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const r = await jsend("/api/admin/roles", "PUT", {
          email: cb.dataset.email, role: "user", kb_editor: cb.checked,
        });
        $("rolesStatus").textContent = r.ok
          ? "Сохранено."
          : `Не сохранилось: ${errText(r.status)}`;
      });
    });
  }

  // ── старт ────────────────────────────────────────────
  (async function init() {
    try {
      me = await jget("/api/me");
    } catch (e) {
      $("listStatus").innerHTML = `<span class="err">Не удалось определить пользователя.</span>`;
    }
    if (me.can_edit_kb) {
      FIELDS.forEach((id) => { $(id).disabled = false; });
      $("editRow").classList.remove("hidden");
      $("createRow").classList.remove("hidden");
      $("historyBox").classList.remove("hidden");
    }
    if (me.role === "admin") {
      $("rolesPanel").classList.remove("hidden");
      loadRoles();
    }
    loadList();
  })();
})();
```

- [ ] **Step 7: Запустить весь набор**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py tests/unit/test_roles.py -q`
Expected: PASS

- [ ] **Step 8: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check app/api/routes_pages.py tests/unit/test_kb_routes.py
git add app/api/routes_pages.py app/templates/library.html app/templates/creatives.html app/templates/webinar.html app/static/library.js tests/unit/test_kb_routes.py
git commit -m "App3: страница библиотеки знаний — чтение всем, правка по роли, история версий"
```

---

### Task 13: Опыт в промптах — второй слой библиотеки

`kb_runs` из Task 9 пока просто копится. Здесь он начинает работать: последние отмеченные исходы по тому же продукту уходят в промпт копирайтера («вот что уже заходило и что забраковали») и в промпт метафоры. Инжект — тем же приёмом, что и каталог: app-слой кладёт снапшот в `graph.knowledge`, граф `app` не импортирует.

Важное отличие от соседних блоков (`notes_block`, `must_honour_block`): опыт **не** отдаёт заглушку при пустом слое. спека говорит прямо: «Нет отмеченных записей → секции не подмешиваются, промпт как сейчас». Поэтому секция про опыт живёт отдельным `## Experience addendum` в файле промпта и дописывается к user-сообщению только когда блок непустой — при пустой библиотеке промпт остаётся байт-в-байт тем же, что сегодня. Такой приём в репозитории уже применялся (см. changelog `creative_ads_explorer.md`: A/B-anti-bias и revise addenda).

В промпт идёт только `outcome="shipped"`, максимум 5 свежих записей по тому же продукту. `rejected` копится в `kb_runs` и виден человеку на странице библиотеки, но в промпт не попадает: забракованная формулировка, поданная как «пример из опыта», — подсказка повторить неудачу.

**Files:**
- Modify: `graph/knowledge.py` (в конец — `ExperienceNote`, `set_experience`, `experience_for`)
- Modify: `graph/nodes/context.py`
- Modify: `graph/nodes/generate_message_candidates.py:76-92,104-133`
- Modify: `graph/nodes/generate_image_prompt.py:149-158,189-202,224-255`
- Modify: `prompts/creative_ads_explorer.md`, `prompts/generate_image_prompt.md`
- Modify: `app/kb/experience.py` (Task 9), `app/main.py` (lifespan), `app/api/routes_tasks.py` (роут `set_outcome` из Task 9)
- Modify: `app/api/routes_kb.py`, `app/api/schemas.py`, `app/templates/library.html`, `app/static/library.js` (лента опыта, Step 15)
- Test: `tests/unit/test_experience_block.py` (создать), `tests/unit/test_kb_experience.py`, `tests/unit/test_generate_message_candidates.py`, `tests/unit/test_generate_image_prompt.py`, `tests/unit/test_kb_routes.py`

- [ ] **Step 1: Тест на слой опыта в графе**

Создать `tests/unit/test_experience_block.py`:

```python
"""Слой «опыт»: инжект в graph.knowledge и блок для промптов."""

from __future__ import annotations

from graph import knowledge
from graph.knowledge import ExperienceNote
from graph.nodes.context import experience_block


def _note(**kw) -> ExperienceNote:
    base = dict(
        slug="managed-rag", outcome="shipped", slogan="GPU без очереди",
        anchor="боль: очередь на GPU", desired_outcome="обучение стартует за минуты",
        metaphor="a bridge across a canyon", persona_segment="ML-инженеры",
        comment="",
    )
    base.update(kw)
    return ExperienceNote(**base)


def test_empty_layer_gives_empty_block():
    """Нет отметок — секции в промпте не будет вообще (решение спеки:
    промпт остаётся ровно таким, каким он был до слоя опыта)."""
    knowledge.set_experience(())
    assert experience_block({"kb_match": {"slug": "managed-rag"}}) == ""


def test_notes_are_filtered_by_slug():
    try:
        knowledge.set_experience((_note(), _note(slug="other", slogan="Чужое")))
        block = experience_block({"kb_match": {"slug": "managed-rag"}})
        assert "GPU без очереди" in block
        assert "Чужое" not in block
    finally:
        knowledge.set_experience(())


def test_rejected_is_stored_but_never_reaches_prompts():
    """rejected копится в kb_runs (человеку он виден на странице библиотеки),
    но в промпт идёт только принятое: забракованный текст как «пример» —
    это подсказка повторить неудачу."""
    try:
        knowledge.set_experience((
            _note(outcome="rejected", slogan="Слишком общо", comment="ни о чём"),
        ))
        assert experience_block({"kb_match": {"slug": "managed-rag"}}) == ""
    finally:
        knowledge.set_experience(())


def test_block_keeps_five_freshest():
    try:
        knowledge.set_experience(
            tuple(_note(slogan=f"Слоган {i}") for i in range(8))
        )
        block = experience_block({"kb_match": {"slug": "managed-rag"}})
        assert block.count("\n") == 4  # пять строк
        assert "Слоган 0" in block and "Слоган 5" not in block
    finally:
        knowledge.set_experience(())


def test_no_kb_match_means_no_experience():
    try:
        knowledge.set_experience((_note(),))
        # Опыт по другому продукту хуже, чем никакого: он уводит копирайтера
        # в чужие формулировки. Нет привязки к карточке — нет и блока.
        assert experience_block({}) == ""
    finally:
        knowledge.set_experience(())


def test_metaphor_kind_uses_metaphor_text():
    try:
        knowledge.set_experience((_note(),))
        block = experience_block(
            {"kb_match": {"slug": "managed-rag"}}, kind="metaphor"
        )
        assert "a bridge across a canyon" in block
        assert "GPU без очереди" not in block
    finally:
        knowledge.set_experience(())
```

- [ ] **Step 2: Запустить — упадёт**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_experience_block.py -q`
Expected: FAIL — `ImportError: cannot import name 'ExperienceNote' from 'graph.knowledge'`

- [ ] **Step 3: Слой опыта в `graph/knowledge.py`**

В конец файла:

```python
@dataclass(frozen=True)
class ExperienceNote:
    """Один отмеченный человеком исход запуска — слой «опыт» библиотеки.

    Факты (ProductDoc) говорят, ЧТО правда про продукт; опыт говорит, что из
    этой правды уже заходило команде, а что забраковали. Второе без первого
    бессмысленно, поэтому заметка всегда привязана к slug карточки."""

    slug: str
    outcome: str  # shipped | rejected
    slogan: str = ""
    anchor: str = ""
    desired_outcome: str = ""
    metaphor: str = ""
    persona_segment: str = ""
    comment: str = ""


# App-layer injection point (симметрично _catalog_override): app кладёт сюда
# снапшот kb_runs после каждой отметки исхода. Пустой кортеж = опыта нет.
_experience: tuple[ExperienceNote, ...] = ()


def set_experience(notes: tuple[ExperienceNote, ...]) -> None:
    global _experience
    _experience = tuple(notes)


def experience_for(slug: str | None, *, limit: int = 5) -> tuple[ExperienceNote, ...]:
    """Последние ПРИНЯТЫЕ заметки по этому продукту (новые первыми).

    Без slug возвращаем пусто: опыт по чужому продукту уводит копирайтера в
    формулировки, которые к текущему брифу отношения не имеют. Забракованное
    не отдаём вовсе — оно хранится для человека, а модели «вот так не надо»
    работает как подсказка повторить неудачу."""
    if not slug:
        return ()
    return tuple(
        n for n in _experience if n.slug == slug and n.outcome == "shipped"
    )[:limit]
```

- [ ] **Step 4: `experience_block` в `graph/nodes/context.py`**

Добавить импорт `from graph import knowledge` и в конец файла:

```python
def experience_block(state: GraphState, *, kind: str = "text", limit: int = 5) -> str:
    """Слой «опыт»: что по этому продукту уже ушло в работу.

    kind="text" — русские слоганы с якорями (копирайтер);
    kind="metaphor" — английские метафоры (промпт картинки — он на английском).
    Пусто, когда отметок нет: секция тогда не подмешивается вовсе и промпт
    остаётся ровно таким, каким был до слоя опыта (решение спеки). Именно
    поэтому здесь нет fallback-строки, в отличие от notes_block — там пустая
    секция ломала бы структуру user-сообщения, а тут секции просто не будет."""
    slug = (state.get("kb_match") or {}).get("slug")
    notes = knowledge.experience_for(slug, limit=limit)
    if kind == "metaphor":
        return "\n".join(f"- {n.metaphor}" for n in notes if n.metaphor)
    return "\n".join(
        f"- «{n.slogan}» — {n.anchor or 'якорь не записан'}"
        + (f"; комментарий: {n.comment}" if n.comment else "")
        for n in notes
        if n.slogan
    )
```

- [ ] **Step 5: Запустить — зелёное**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_experience_block.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Addendum-секция в промпте копирайтера**

`prompts/creative_ads_explorer.md` — НЕ трогаем `## User message template` (иначе при пустой библиотеке в промпте повиснет пустой заголовок). Вместо этого добавляем отдельную секцию **после** `## User message template` и перед `## Model-specific notes`:

````
## Experience addendum

Дописывается к user-сообщению ТОЛЬКО когда по этому продукту есть отмеченные
исходы (kb_runs, outcome=shipped). Пустая библиотека — секции нет вовсе.

```
ОПЫТ ПРЕДЫДУЩИХ ЗАПУСКОВ ПО ЭТОМУ ПРОДУКТУ (что команда уже взяла в работу):
{{experience_block}}

Это не образец для копирования: не повторяй эти слоганы дословно и не пересказывай их синонимами. Раздел говорит, куда команда уже смотрела, — заходи с других якорей.
Ответ по-прежнему — ТОЛЬКО валидный JSON по схеме выше.
```
````

Поднять frontmatter `version: 0.8.0` → `version: 0.9.0` и дописать первой строкой в `## Changelog`:

```
- v0.9.0 (2026-08-08) — слой «опыт»: новая секция «## Experience addendum»
  (последние 5 принятых исходов по этому же продукту — слоган, якорь,
  комментарий человека). Источник — таблица kb_runs, инжект через
  graph.knowledge.set_experience. Отдельной секцией, а не в user-шаблоне:
  при пустой библиотеке промпт обязан остаться прежним (решение спеки
  2026-08-07). Забракованные исходы (rejected) в промпт не идут.
```

- [ ] **Step 7: Прокинуть addendum в узел копирайтера**

В `graph/nodes/generate_message_candidates.py`:

1. К импорту из `graph.nodes.context` добавить `experience_block`.
2. В `generate_message_candidates` (файл, строки 72-91) после `user_tpl = ...` и перед `anchors = _anchor_slices(persona)`:

```python
    experience = experience_block(state)
    experience_tpl = (
        extract_section(skill.body, "## Experience addendum") if experience else ""
    )
```

3. В вызов `_run_batch` внутри `asyncio.gather` добавить аргумент после `anchors=slice_`:

```python
                anchors=slice_,
                experience=experience,
                experience_tpl=experience_tpl,
                session_id=session_id,
```

4. В сигнатуру `_run_batch` (строки 104-114), в keyword-only часть после `anchors`:

```python
    anchors: list[str],
    experience: str,
    experience_tpl: str,
    session_id: str | None,
```

5. В теле `_run_batch`, сразу после `user_msg = render(...)` и перед `result = await run_agent(`:

```python
    if experience and experience_tpl:
        user_msg += "\n\n" + render(experience_tpl, experience_block=experience)
```

`render` подставляет пустую строку в неизвестные `{{...}}`, поэтому лишних ключей в основном `render(user_tpl, ...)` заводить не нужно — `{{experience_block}}` там просто не встречается.

6. Дописать тест в `tests/unit/test_generate_message_candidates.py` (фикстура `calls` уже пишет kwargs каждого `render` в список — addendum рендерится отдельным вызовом, значит виден там же):

```python
async def test_experience_addendum_appears_only_when_layer_has_notes(calls):
    from graph import knowledge
    from graph.knowledge import ExperienceNote

    state = _state()
    state["kb_match"] = {"slug": "managed-rag", "name": "RAG", "version": 1}

    await mod.generate_message_candidates(state)
    assert not [kw for kw in calls if "experience_block" in kw], (
        "пустая библиотека не должна менять промпт"
    )

    calls.clear()
    try:
        knowledge.set_experience(
            (
                ExperienceNote(
                    slug="managed-rag",
                    outcome="shipped",
                    slogan="GPU без очереди",
                    anchor="боль: очередь на GPU",
                ),
            )
        )
        await mod.generate_message_candidates(state)
    finally:
        knowledge.set_experience(())

    rendered = [kw["experience_block"] for kw in calls if "experience_block" in kw]
    assert len(rendered) == 2, "addendum дописывается к каждому из двух заходов"
    assert "GPU без очереди" in rendered[0]
```

- [ ] **Step 8: Addendum в промпте метафоры**

`prompts/generate_image_prompt.md` — так же, отдельной секцией после `## User message template` (промпт английский, addendum тоже английский):

````
## Experience addendum

Appended to the user message ONLY when this product has shipped runs recorded
(kb_runs, outcome=shipped). Empty library — the section is not appended at all.

```
METAPHORS ALREADY SHIPPED FOR THIS PRODUCT:
{{experience_block}}

Do not reuse these metaphors or trivial variations of them. They show the visual territory the team has already covered — go somewhere else.
```
````

Поднять `version` на следующую минорную и дописать первой строкой в `## Changelog` (по образцу Step 6, но про метафоры).

В `graph/nodes/generate_image_prompt.py`:

1. Строка 40 сейчас `from graph.nodes.context import get_product` → заменить на `from graph.nodes.context import experience_block, get_product`.
2. В `generate_image_prompt`, после `system_msg, user_tpl = _load_sections()` (строка 149):

```python
    experience = experience_block(state, kind="metaphor")
```

3. Вызов `_build_one` внутри `gather` (строки 153-155):

```python
            _build_one(
                system_msg, user_tpl, brief, persona, product, cand, style,
                session_id, experience=experience,
            )
```

4. В `_regenerate_winner`, после `system_msg, user_tpl = _load_sections()` (строка 189):

```python
    experience = experience_block(state, kind="metaphor")
```

и в вызов `_build_one` (строки 191-202), рядом с `feedback_comment=comment`:

```python
        prev_metaphor=prev_meta.get("metaphor", ""),
        feedback_comment=comment,
        experience=experience,
```

5. В сигнатуру `_build_one` (строки 233-235):

```python
    prev_metaphor: str = "",
    feedback_comment: str = "",
    experience: str = "",
```

6. В теле `_build_one`, сразу после `user_msg = _render(...)` и ПЕРЕД блоком `if feedback_comment:` (комментарий маркетолога — самое свежее указание, он должен остаться последним в сообщении):

```python
    if experience:
        addendum = _extract_section(
            load_skill(_SKILL_NAME).body, "## Experience addendum"
        )
        user_msg += "\n\n" + _render(addendum, experience_block=experience)
```

`load_skill` кэширован через `lru_cache` (`graph/prompts.py:68`), так что чтение файла здесь не повторяется на каждый из 12 параллельных вызовов.

Ключ `"experience_block"` в основной `_render(...)`-словарь НЕ добавляем: в `## User message template` этого плейсхолдера нет.

7. Дописать тест в `tests/unit/test_generate_image_prompt.py` (фикстура `_stub_agent` не подменяет `load_skill`, поэтому здесь проверяется настоящий файл промпта с новой секцией):

```python
@pytest.mark.asyncio
async def test_experience_addendum_reaches_llm_only_when_layer_has_notes(_stub_agent):
    from graph import knowledge
    from graph.knowledge import ExperienceNote

    state = _good_state()
    state["kb_match"] = {"slug": "managed-rag", "name": "RAG", "version": 1}

    calls = _stub_agent(_METAPHOR)
    await mod.generate_image_prompt(state)  # type: ignore[arg-type]
    assert "ALREADY SHIPPED" not in calls[0]["messages"][1]["content"]

    calls.clear()
    try:
        knowledge.set_experience(
            (
                ExperienceNote(
                    slug="managed-rag",
                    outcome="shipped",
                    metaphor="a bridge across a canyon",
                ),
            )
        )
        await mod.generate_image_prompt(state)  # type: ignore[arg-type]
    finally:
        knowledge.set_experience(())

    user_msg = calls[0]["messages"][1]["content"]
    assert "ALREADY SHIPPED" in user_msg
    assert "a bridge across a canyon" in user_msg
```

- [ ] **Step 9: Прогнать узловые тесты**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_generate_message_candidates.py tests/unit/test_generate_image_prompt.py tests/unit/test_experience_block.py -q`
Expected: PASS без правок в существующих тестах. Это и есть проверка решения «пустой слой — промпт как сейчас»: тесты узлов не настраивают `set_experience`, значит addendum не дописывается и user-сообщение байт-в-байт прежнее. Если что-то из них всё же упало — значит addendum подмешался при пустом слое, и чинить надо `experience_block`/условие в Step 7-8, а не тест.

- [ ] **Step 10: Тест на чтение опыта из БД**

Дописать в `tests/unit/test_kb_experience.py`:

```python
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
    from graph import knowledge

    from app.kb.experience import refresh_experience

    await record_outcome(
        Session, session_id="r1", outcome="shipped", comment="", recipe=_RECIPE
    )
    try:
        n = await refresh_experience(Session)
        assert n == 1
        assert knowledge.experience_for("managed-rag")[0].slogan == "GPU без очереди"
    finally:
        knowledge.set_experience(())
```

- [ ] **Step 11: Запустить — упадёт**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_experience.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_experience'`

- [ ] **Step 12: Чтение и инжект в `app/kb/experience.py`**

Дописать в конец файла (`select` и `models` уже импортированы из Task 9):

```python
from graph.knowledge import ExperienceNote

# Сколько последних исходов держим в снапшоте. Больше не нужно: в промпт
# уходит максимум 5 по одному продукту, а таблица растёт бесконечно.
_SNAPSHOT_LIMIT = 200


async def load_experience(sessionmaker) -> tuple[ExperienceNote, ...]:
    """Отмеченные исходы, новые первыми (порядок важен: experience_for режет
    хвост limit'ом и должен оставлять самое свежее)."""
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(models.KbRun)
                .order_by(models.KbRun.created_at.desc(), models.KbRun.id.desc())
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
```

и добавить в импорты модуля:

```python
from graph import knowledge
```

- [ ] **Step 13: Запустить — зелёное**

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_experience.py -q`
Expected: PASS

- [ ] **Step 14: Подключить refresh — на старте и после каждой отметки**

В `app/main.py`, в блоке инициализации библиотеки знаний (рядом с `refresh_catalog`):

```python
        try:
            from app.kb.experience import refresh_experience
            from app.kb.store import refresh_catalog, seed_from_files

            await seed_from_files(Session)
            await refresh_catalog(Session)
            await refresh_experience(Session)
        except Exception as exc:  # noqa: BLE001
            log.error("kb_catalog_init_failed: %s", exc)
```

В `app/api/routes_tasks.py`, в роуте `set_outcome` (Task 9) — после успешной записи:

```python
    if recorded:
        # Опыт должен быть виден следующему запуску сразу, как и правка карточки.
        from app.kb.experience import refresh_experience

        await refresh_experience(Session)
```

- [ ] **Step 15: Отмеченный опыт видно на странице библиотеки**

Спека: читателю видны не только карточки, но и отмеченный опыт — иначе слой, который влияет на генерацию, остаётся невидимым.

Тест — дописать в `tests/unit/test_kb_routes.py`:

```python
def test_experience_feed_is_readable_by_everyone(tmp_path, monkeypatch):
    with TestClient(_admin_app(tmp_path, monkeypatch)) as c:
        r = c.get("/api/kb/experience", headers=_HDR)
        assert r.status_code == 200 and r.json() == []
        assert c.get("/api/kb/experience").status_code == 401
```

Схема в `app/api/schemas.py`:

```python
class ExperienceOut(BaseModel):
    slug: str
    outcome: str
    slogan: str = ""
    anchor: str = ""
    persona_segment: str = ""
    comment: str = ""
    created_at: str | None = None
```

Роут в `app/api/routes_kb.py`:

```python
@router.get("/experience", response_model=list[ExperienceOut])
async def list_experience(request: Request, limit: int = 50):
    """Отмеченные исходы — то же, что видит копирайтер, только для человека."""
    await get_current_user(request)
    Session = request.app.state.sessionmaker
    async with Session() as s:
        rows = (
            await s.execute(
                select(models.KbRun)
                .order_by(models.KbRun.created_at.desc(), models.KbRun.id.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
    return [
        ExperienceOut(
            slug=r.slug or "",
            outcome=r.outcome,
            slogan=r.slogan or "",
            anchor=r.anchor or "",
            persona_segment=r.persona_segment or "",
            comment=r.comment or "",
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]
```

(добавить `from sqlalchemy import select` и `ExperienceOut` в импорты модуля).

Панель в `app/templates/library.html` — после `#cardPanel`:

```html
    <section class="panel" id="experiencePanel">
      <h2>Отмеченный опыт</h2>
      <p class="page-sub">Что команда взяла в работу, а что забраковала. Эти отметки уходят в промпт копирайтера по тому же продукту.</p>
      <div class="task-list" id="experienceList"></div>
    </section>
```

и в `app/static/library.js` — загрузка при старте (вызвать `loadExperience()` в `init` после `loadList()`):

```javascript
  const OUTCOME_RU = { shipped: "пошло в работу", rejected: "забраковано" };

  async function loadExperience() {
    let rows;
    try {
      rows = await jget("/api/kb/experience");
    } catch (e) {
      return;
    }
    $("experienceList").innerHTML = rows.length
      ? rows
          .map(
            (r) =>
              `<div class="task-item"><b>${esc(OUTCOME_RU[r.outcome] || r.outcome)}</b>` +
              ` · ${esc(r.slug || "без продукта")} · «${esc(r.slogan)}»` +
              (r.comment ? `<div class="page-sub">${esc(r.comment)}</div>` : "") +
              `</div>`
          )
          .join("")
      : `<p class="page-sub">Пока никто не отмечал исходы. Отметка ставится на экране результата.</p>`;
  }
```

И поднять cache-buster в `library.html`: `library.js?v=20260808v1` → `?v=20260808v2` (правило из шапки плана — без бампа браузер отдаст старый файл без `loadExperience`).

Run: `.venv311/Scripts/python.exe -m pytest tests/unit/test_kb_routes.py -q`
Expected: PASS

- [ ] **Step 16: Полный прогон**

Run: `.venv311/Scripts/python.exe -m pytest -q`
Expected: PASS (весь набор)

- [ ] **Step 17: Коммит**

```bash
.venv311/Scripts/python.exe -m ruff check graph/knowledge.py graph/nodes/context.py graph/nodes/generate_message_candidates.py graph/nodes/generate_image_prompt.py app/kb/experience.py app/main.py app/api/routes_tasks.py app/api/routes_kb.py app/api/schemas.py tests/unit/test_experience_block.py tests/unit/test_kb_experience.py tests/unit/test_generate_message_candidates.py tests/unit/test_generate_image_prompt.py tests/unit/test_kb_routes.py
git add graph/knowledge.py graph/nodes/context.py graph/nodes/generate_message_candidates.py graph/nodes/generate_image_prompt.py prompts/creative_ads_explorer.md prompts/generate_image_prompt.md app/kb/experience.py app/main.py app/api/routes_tasks.py app/api/routes_kb.py app/api/schemas.py app/templates/library.html app/static/library.js tests/unit/test_experience_block.py tests/unit/test_kb_experience.py tests/unit/test_generate_message_candidates.py tests/unit/test_generate_image_prompt.py tests/unit/test_kb_routes.py
git commit -m "App3: слой опыта в промптах — отмеченные исходы по продукту идут копирайтеру и метафоре"
```

---

## Что этот план НЕ делает

- Не трогает топологию графа и `GRAPH_VERSION` (остаётся 2) — все три остановки те же, что после Плана 1.
- Не вводит `doc_type` в карточки знаний: решение отложено (коммит 2e177e6) — библиотека остаётся однородной, только текстовые карточки продуктов.
- Не добавляет поиск/фильтры по библиотеке: каталог на десяток продуктов, список помещается на экран.
- Не переносит правку карточек в git/файлы: источник истины — БД, `config/knowledge/*.md` остаётся сидом первого старта.
- Не отдаёт забракованные исходы (`rejected`) в промпты: они копятся в `kb_runs` и видны человеку на странице библиотеки, но модели «вот так не надо» работает как подсказка повторить неудачу. Решение спеки 2026-08-07; пересмотреть можно, когда наберётся материал.
- Не даёт откат к старой версии одной кнопкой: история видна, откат = скопировать текст и сохранить как новую версию. Отдельная кнопка — после того, как история кому-то реально понадобится.

