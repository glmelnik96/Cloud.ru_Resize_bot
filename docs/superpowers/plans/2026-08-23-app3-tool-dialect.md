# App3 под диалект инструмента v11 — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перестроить три страницы App3 так, чтобы работа была одной строкой с раскрытием, а не полем одинаковых плиток, и чтобы регистр набора зависел от того, что строка делает.

**Architecture:** Два этажа, повёрнутые на 90°: камень (`.rail`) слева, чернила справа до края окна. Перекрас — только переопределением токенов `--lp-*` на `.rail`, ни одного продублированного правила. Лента становится списком строк; остановки пайплайна физически переезжают из лайтбокса в ящик под строкой (узлы **перемещаются** `appendChild`, а не пересобираются — обработчики висят на самих узлах и переезд переживают). Лайтбокс остаётся один: просмотр баннера в полный рост.

**Tech Stack:** FastAPI + Jinja2, ванильный CSS/JS без сборки, pytest через `starlette.testclient` (тесты читают отданные сервером CSS/JS/HTML как текст — это принятый в репозитории приём, см. `tests/unit/test_app3_pages.py`).

**Спека:** `docs/superpowers/specs/2026-08-23-app3-tool-dialect-design.md`

---

## Карта файлов

| Файл | Ответственность после правок |
|---|---|
| `app/static/app.css` | Один лист на три страницы. Токены → каркас → компоненты → узкий экран. Добавляется блок `.rail` (токены камня) и блок «Работа = строка»; удаляется `.lp-grid`, `.feed__grid`, плиточный `.work__body`. |
| `app/templates/creatives.html` | Разметка страницы креативов. Уходит `.lp-grid`, инициализатор `lpGrid`, три панели остановок переезжают из `#lightbox` в `#stops`. |
| `app/templates/webinar.html` | То же по каркасу. Кадрирование остаётся лайтбоксом. |
| `app/templates/library.html` | То же по каркасу. Карточка и опыт разъезжаются по пустоте, а не по общей раме. |
| `app/static/creatives.js` | Лента строк, ящик под строкой, перенос панелей остановок, срок хранения. |
| `app/static/webinar.js` | Лента строк по тому же приёму. |
| `app/static/library.js` | Правок логики нет; правится только там, где имена классов ленты. |
| `app/static/grid.js` | **Остаётся в репозитории нетронутым**, но ни один шаблон его не подключает. |
| `tests/unit/test_app3_pages.py` | Все новые проверки идут сюда — файл уже держит ровно этот жанр тестов. |

---

## Task 1: Токены — сведённые полюса и камень

**Files:**
- Modify: `app/static/app.css:85-107` (блок `body.v11`), `app/static/app.css:196` (начало `body.is-tool`)
- Test: `tests/unit/test_app3_pages.py`

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/unit/test_app3_pages.py`:

```python
def test_css_repaints_the_stone_floor_by_tokens_only(tmp_path, monkeypatch):
    """Светлый этаж перекрашивается переопределением переменных на .rail.
    Ни одного правила вида `.rail .что-то { color: ... }` быть не должно:
    дубль цвета — это второе место, где живёт правда о палитре."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        assert "--lp-ink: #D9DEDB" in css      # камень, не белый
        assert "--lp-muted: #565E5B" in css    # 4.6:1 к камню — AA
        assert "--lp-hi: #1B211F" in css       # инверсия на светлом идёт в темноту
        assert "--lp-key: #35A171" in css      # ступень вниз: #3FB67C на камне светится
        assert "--sl-surface: #CBD1CE" in css  # ящик утоплен, а не приподнят
        # сведённые полюса: расхождение с App2 в одну ступень хуже двух серых
        assert "--lp-line-2: #2C3532" in css
        assert "--lp-line-2: #333D3A" not in css
        assert "scrollbar-gutter: stable" in css


def test_css_has_no_duplicated_colour_rules_under_rail(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        for sel in (".rail .t-btn", ".rail .field-label", ".rail input", ".rail button"):
            assert sel not in css
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/unit/test_app3_pages.py -k "stone_floor or duplicated_colour" -v`
Expected: FAIL — `assert '--lp-ink: #D9DEDB' in css`.

- [ ] **Step 3: Свести полюса**

В `app/static/app.css` заменить строку `--lp-line-2: #333D3A;       /* хайрлайны */` вместе с комментарием над ней (строки 91-96) на:

```css
  /* Хайрлайн сведён с App2 (§1 диалекта). Канонный #1E2523 на чернилах не
     читался, поэтому оба приложения подняли линию до различимой — но подняли
     на разную высоту. Расхождение в один шаг между двумя разделами одного
     портала хуже, чем разница между двумя оттенками серого, поэтому берём
     общее значение. Это по-прежнему волосок 1px: меняется только светлота. */
  --lp-line-2: #2C3532;       /* хайрлайны */
```

Там же, в блоке `body.v11`, заменить строки с `--lp-ink`, `--lp-text`, `--lp-soft`, `--lp-hi` на сведённые значения:

```css
  --lp-ink: #141817;          /* чернила */
  --lp-text: #E3E8E6;         /* свет */
  --lp-soft: #B3BCB8;         /* содержательный текст */
  --lp-hi: #AAB4B0;           /* инверсия под курсором: серая, не белая */
```

И добавить в тот же блок, после `--lp-key-ink`:

```css
  --sl-surface: #181D1B;      /* утопленная поверхность: ящик под строкой */
  --sl-gut: max(24px, calc((100% - 1176px) / 2));
```

- [ ] **Step 4: Добавить токены камня и жёлоб полосы прокрутки**

Сразу после закрывающей скобки блока `body.v11` вставить:

```css
/* Раскрытие строки делает страницу длиннее окна, полоса прокрутки появляется —
   и без зарезервированного жёлоба вся раскладка прыгает вбок на её ширину. */
html { scrollbar-gutter: stable; }
```

- [ ] **Step 5: Запустить тесты**

Run: `python -m pytest tests/unit/test_app3_pages.py -v`
Expected: тест `test_css_has_no_duplicated_colour_rules_under_rail` PASS, `test_css_repaints_the_stone_floor_by_tokens_only` всё ещё FAIL (блока `.rail` пока нет) — это Task 2. Тест `test_static_css_served` может упасть на `--lp-ink: #0A0C0B`; исправить его ожидание на `--lp-ink: #141817` в той же правке, комментарий в докстроке теста дополнить фразой «полюса сведены с App2».

- [ ] **Step 6: Коммит**

```bash
git add app/static/app.css tests/unit/test_app3_pages.py
git commit -m "App3: полюса сведены с App2, жёлоб прокрутки зарезервирован"
```

---

## Task 2: Каркас — два этажа, снятие рамы и чертежа

**Files:**
- Modify: `app/static/app.css:194-220` (`body.is-tool`, `.tool__grid`, `.rail`), `app/static/app.css:164-180` (`.lp-grid` — удаляется), `app/static/app.css:256-257` (`.feed`)
- Modify: `app/templates/creatives.html:8-9,104,250-260`; `app/templates/webinar.html`; `app/templates/library.html`
- Test: `tests/unit/test_app3_pages.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_no_template_wires_the_live_grid(tmp_path, monkeypatch):
    """Чертёж снят: цвет штриха захардкожен под тёмный фон, на камне линий не
    видно, а под лентой работ фон не пустой — сетка спорит со строками.
    Сам grid.js остаётся в репозитории: это общий модуль портала."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        for path in ("/", "/webinar", "/library"):
            html = c.get(path, headers=_HDR).text
            assert "grid.js" not in html, path
            assert "lp-grid" not in html, path
            assert "lpGrid(" not in html, path
        assert "lp-grid" not in c.get("/static/app.css").text


def test_feed_frame_is_replaced_by_the_floor_border(tmp_path, monkeypatch):
    """Граница этажей идёт от шапки до низа окна и работает разделителем.
    Рама вокруг ленты поверх неё — вторая линия, делающая ту же работу."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        assert ".feed { position: relative; isolation: isolate" not in css
        assert ".rail {" in css
        assert "--sl-gut" in css
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/unit/test_app3_pages.py -k "live_grid or feed_frame" -v`
Expected: FAIL — `assert 'grid.js' not in html`.

- [ ] **Step 3: Переписать каркас в CSS**

Заменить `.tool__grid` и `.rail` (строки 205-220) на:

```css
/* ===== Каркас: два этажа, повёрнутые на 90° =====
   У App2 этажи горизонтальные — свет сверху, чернила снизу. У нас форма брифа
   не предшествует работе, а сопровождает её: человек правит поля, глядя на
   ленту. Поэтому те же два этажа поставлены вертикально.
   Граница столбцов идёт от шапки до низа окна и заменяет собой раму ленты:
   смена поверхности — самый сильный разделитель, какой есть, и рамка поверх
   него была бы второй линией, делающей ту же работу. */
.tool__grid { flex: 1 0 auto; display: grid;
  grid-template-columns: 360px 1fr; gap: 0; align-items: stretch; }
/* Камень. 360 снаружи и 48 внутри дают колонку формы ровно 264 — ту же, что
   была: поля не перерисовываются, меняется подложка под ними.
   Полоса прибита к окну целиком (top 60 = высота шапки), а не обрублена по
   max-height: обрубок скроллился сам по себе, отдельно от страницы. */
.rail { --lp-ink: #D9DEDB;      /* камень — поверхность этажа И полей на нём */
  --lp-text: #171C1A;
  --lp-soft: #39403D;
  --lp-muted: #565E5B;          /* 4.6:1 к камню — AA */
  --lp-line: #D2D8D5;
  --lp-line-2: #B4BDB9;
  --lp-hi: #1B211F;             /* инверсия на светлом обязана идти в темноту */
  --lp-edge: rgba(38, 138, 92, .42);
  --lp-key: #35A171;            /* ступень вниз: #3FB67C на камне светится */
  --sl-surface: #CBD1CE;        /* ящик утоплен, а не приподнят */
  min-width: 0; align-self: start;
  position: sticky; top: 60px; height: calc(100vh - 60px);
  overflow: hidden auto; scrollbar-width: thin;
  padding: 48px; background: var(--lp-ink); color: var(--lp-text); }
```

Заменить `body.is-tool main` (строки 201-203) на:

```css
body.is-tool main { position: relative; flex: 1 0 auto; display: flex;
  flex-direction: column; max-width: none; margin: 0; padding: 0; }
```

Заменить блок `.feed` (строки 251-257) на:

```css
/* ===== Лента =====
   Рамы больше нет: этаж чернил сам себе рама. Поле справа держит --sl-gut —
   тот же жёлоб, что в App2, поэтому строки двух приложений стоят на одной
   вертикали при одинаковой ширине окна. */
.feed { min-width: 0; padding: 96px var(--sl-gut) 96px 48px; }
```

Удалить целиком блок «Живой чертёж» (строки 164-180).

Заменить `body.is-tool h1` на версию с полем этажа:

```css
/* Заголовок стоит на этаже чернил, а не над обоими: он называет то, что
   человек смотрит, а не то, чем управляет. 44/48 — строка ровно два кирпича. */
body.is-tool h1 { margin: 0 0 48px; font-size: 44px; line-height: 48px;
  letter-spacing: -0.03em; font-weight: 700; color: var(--lp-text); }
```

В медиазапросе `@media (max-width: 860px)` заменить правила каркаса:

```css
  .tool__grid { grid-template-columns: 1fr; }
  /* В одну колонку липнуть нечему: камень стоит НАД лентой, и прилипший он
     закрыл бы собой работы, ради которых прокрутка и затевалась. */
  .rail { position: static; height: auto; overflow: visible; padding: 24px; }
  .feed { padding: 48px 24px; }
```

- [ ] **Step 4: Снять чертёж из трёх шаблонов**

В `app/templates/creatives.html`:
- удалить строки 8-9 (комментарий `{# Живая сетка ... #}` и `<script src="{{ prefix }}/static/grid.js...">`);
- удалить строку 104 (`<div class="lp-grid lp-grid--page">...</div>`);
- удалить блок `<script>` со `window.lpGrid({...})` (строки 250-260).

Те же три правки — в `app/templates/webinar.html` и `app/templates/library.html` (искать `grid.js`, `lp-grid`, `lpGrid`).

- [ ] **Step 5: Перенести h1 внутрь этажа чернил**

В каждом из трёх шаблонов заголовок сейчас стоит перед `<div class="tool__grid">`. Перенести его первой строкой внутрь `<section class="feed">`, сразу после открывающего тега. В `creatives.html` это:

```html
      <section class="feed" id="feed">
        <h1>Генерация креативов</h1>
        <header class="feed__head">
```

В `webinar.html` — `<h1>Вебинарные ресайзы</h1>`, в `library.html` — `<h1>Библиотека знаний</h1>` (взять существующий текст заголовка, не выдумывать новый).

- [ ] **Step 6: Запустить тесты**

Run: `python -m pytest tests/unit/test_app3_pages.py -v`
Expected: `test_no_template_wires_the_live_grid`, `test_feed_frame_is_replaced_by_the_floor_border`, `test_css_repaints_the_stone_floor_by_tokens_only` — PASS.

- [ ] **Step 7: Коммит**

```bash
git add app/static/app.css app/templates/creatives.html app/templates/webinar.html app/templates/library.html tests/unit/test_app3_pages.py
git commit -m "App3: два этажа вместо рамы и чертежа — граница столбцов делит экран сама"
```

---

## Task 3: Работа = одна строка

**Files:**
- Modify: `app/static/creatives.js:36-52,96-197,224-234`
- Modify: `app/static/app.css` (блок «Плитка», строки 306-351 — заменяется), `.feed__grid` (291-292 — удаляется)
- Modify: `app/templates/creatives.html:120-122`
- Test: `tests/unit/test_app3_pages.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_a_finished_run_is_one_row_not_twelve_tiles(tmp_path, monkeypatch):
    """Двенадцать баннеров — это один предмет «работа», а не двенадцать.
    Взрыв прогона в сетку одинаковых квадратов и был той самой кашей."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "imgs.forEach" not in js      # сетка плиток
        assert "feedGrid" not in js          # рама сетки
        assert "workRow" in js               # строка работы
        assert "feedList" in js
        html = c.get("/", headers=_HDR).text
        assert 'id="feedList"' in html
        assert 'id="feedGrid"' not in html


def test_row_tells_how_long_the_run_is_kept(tmp_path, monkeypatch):
    """Дата создания человеку не нужна — ему нужен остаток срока. Отсчёт от
    created_at (по нему чистит ретенция), окно с сервера, а не из константы."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "keepText" in js
        assert "retentionHours" in js        # окно приезжает атрибутом, не хардкодом
        assert '+ "Z"' in js                 # наивный UTC: без суффикса дата врёт
        html = c.get("/", headers=_HDR).text
        assert 'data-retention-hours="24"' in html
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/unit/test_app3_pages.py -k "one_row or how_long" -v`
Expected: FAIL — `assert 'imgs.forEach' not in js`.

- [ ] **Step 3: Заменить раму сетки списком в шаблоне**

В `app/templates/creatives.html` заменить строки 120-122 на:

```html
        {# Список работ. Срок хранения приезжает атрибутом, а не только фразой
           в сноске: строке нужен остаток в часах, и разбирать его из текста
           было бы разбором собственной вёрстки. #}
        <div class="feed__list" id="feedList" data-retention-hours="{{ retention_hours }}"></div>
        <p class="feed__empty" id="feedEmpty">Работ пока нет</p>
        <p class="feed__foot" id="feedFoot" hidden>Хранятся {{ retention_hours }} ч</p>
```

- [ ] **Step 4: Переписать ленту в creatives.js**

Заменить строки 46-48 (`const feedGrid = $("feedGrid");` и соседние) на:

```js
  const feedList = $("feedList");
  const feedCount = $("feedCount");
  const feedEmpty = $("feedEmpty");
  const feedFoot = $("feedFoot");
  // Окно хранения приходит с сервера через шаблон: константа в JS разъехалась
  // бы с RETENTION_TTL_SEC при первой же правке конфига.
  const RETENTION_H = Number((feedList && feedList.dataset.retentionHours) || 24);
```

Заменить `stateTile`, `imgTile` и `renderFeed` (строки 109-197) на:

```js
  // Остаток срока хранения. Дата создания человеку не говорит ничего: он
  // спрашивает «успею ли скачать», а не «когда это было».
  // ⚠️ Наивный UTC: сервер отдаёт created_at без суффикса, и new Date() читает
  // такую строку как местное время — в Москве это ошибка на три часа.
  function keepText(t) {
    if (!t.created_at || ACTIVE.indexOf(t.status) >= 0) return "";
    const raw = String(t.created_at);
    const iso = /(Z|[+-]\d\d:?\d\d)$/.test(raw) ? raw : raw + "Z";
    const left = RETENTION_H - (Date.now() - new Date(iso).getTime()) / 3600000;
    if (!isFinite(left) || left <= 0) return "";
    return "хранится ещё " + Math.max(1, Math.round(left)) + " ч";
  }

  const rowClass = (t) =>
    "work work--" + t.status + (AWAITING.indexOf(t.status) >= 0 ? " work--await" : "");

  // Работа — одна строка на 48, независимо от того, сколько у неё баннеров.
  // Каркас строится один раз: события шага приходят часто, и пересборка гасила
  // бы кнопку прямо под курсором.
  function workRow(t) {
    const wrap = el("article", rowClass(t));
    const head = el("div", "work__head");
    const hit = el("button", "work__hit");
    hit.type = "button";
    hit.setAttribute("aria-expanded", "false");
    hit.appendChild(el("span", "work__name", t.prompt || "(без названия)"));
    const line = el("span", "work__state", stateText(t));
    hit.appendChild(line);
    const keep = el("span", "work__keep", keepText(t));
    hit.appendChild(keep);
    hit.addEventListener("click", () => toggleRow(t.task_uid));
    head.appendChild(hit);
    const acts = el("div", "work__acts");
    head.appendChild(acts);
    const bar = el("div", "work__bar is-idle");
    bar.appendChild(document.createElement("i"));
    bar.hidden = !(t.status === "queued" || t.status === "running");
    head.appendChild(bar);
    const panel = el("div", "work__panel");
    wrap.appendChild(head);
    wrap.appendChild(panel);
    stateEls.set(t.task_uid, { wrap, line, keep, acts, panel, actsKey: "" });
    fillActs(t);
    return wrap;
  }

  function renderFeed() {
    feedList.innerHTML = "";
    stateEls.clear();
    views = [];
    for (const t of tasks) {
      feedList.appendChild(workRow(t));
      // Просмотр листает по всем баннерам ленты, поэтому плоский список
      // собирается независимо от того, раскрыта строка или нет.
      const imgs = Array.isArray(t.images) ? t.images : [];
      const cards = Array.isArray(t.cards) ? t.cards : [];
      imgs.forEach((u, i) => views.push({
        uid: t.task_uid, idx: i, url: u,
        caption: (cards[i] && cards[i].slogan) || "Баннер " + (i + 1),
      }));
    }
    feedCount.textContent = tasks.length;
    feedEmpty.hidden = tasks.length > 0;
    feedFoot.hidden = !tasks.length;
    paintRoute();
  }
```

Заменить `fillActs` (строки 129-144) на:

```js
  function fillActs(t) {
    const p = stateEls.get(t.task_uid);
    if (!p) return;
    p.actsKey = actsKeyOf(t);
    p.acts.innerHTML = "";
    if (AWAITING.indexOf(t.status) >= 0) {
      p.acts.appendChild(tBtn("Открыть решение", () => attach(t.task_uid)));
    } else if (t.status === "done" && t.result_url) {
      // Готовый прогон без картинок — файлы подчистила ретенция, архив ещё жив.
      const a = el("a", "t-btn", "Скачать ZIP");
      a.href = P + t.result_url;
      a.setAttribute("download", "");
      p.acts.appendChild(a);
    } else if (t.status === "queued" || t.status === "running") {
      p.acts.appendChild(tBtn("Отменить", () => cancelTask(t.task_uid)));
    }
  }
```

Заменить `syncState` (строки 224-234) на:

```js
  function syncState(uid) {
    const t = byUid(uid);
    if (!t) return;
    const p = stateEls.get(uid);
    if (!p) { renderFeed(); return; }
    p.wrap.className = rowClass(t);
    p.line.textContent = stateText(t);
    p.keep.textContent = keepText(t);
    p.bar.hidden = !(t.status === "queued" || t.status === "running");
    paintRoute();
    if (p.actsKey !== actsKeyOf(t)) fillActs(t);
  }
```

Временная заглушка (её тело придёт в Task 4) — вставить рядом с `workRow`:

```js
  // Раскрытие строки. Тело — Task 4; здесь только переключатель, чтобы
  // обработчик клика не ссылался на несуществующую функцию.
  function toggleRow(uid) {
    const p = stateEls.get(uid);
    if (!p) return;
    const open = p.wrap.classList.toggle("is-open");
    p.wrap.querySelector(".work__hit").setAttribute("aria-expanded", String(open));
  }
```

Если функции `cancelTask` в файле нет — проверить `grep -n "cancelTask\|/cancel" app/static/creatives.js` и подставить существующее имя; если отмены нет вовсе, убрать эту ветку из `fillActs` (лишнюю кнопку не изобретать).

- [ ] **Step 5: Заменить плитку строкой в CSS**

Удалить `.feed__grid` (строки 291-292). Заменить блок «Плитка» (строки 306-351) на:

```css
/* ===== Работа — одна строка =====
   Строка на 48, инверсия под курсором, ящик под ней. Двенадцать баннеров это
   один предмет, а не двенадцать: сетка одинаковых квадратов и была той самой
   кашей, ради которой всё затевалось.
   Раскрытие на grid-template-rows: единственный способ анимировать высоту, не
   зная её заранее. display: none тут не годится — он обрывает переход. */
.feed__list { display: flex; flex-direction: column; }
.work { display: grid; grid-template-rows: auto 0fr;
  transition: grid-template-rows .42s var(--lp-ease); }
.work.is-open { grid-template-rows: auto 1fr; }
.work__head { position: relative; display: flex; align-items: center; gap: 24px;
  min-height: 48px; box-shadow: inset 0 -1px 0 var(--lp-line-2); }
.work__hit { flex: 1 1 auto; min-width: 0; display: flex; align-items: baseline;
  gap: 24px; height: 48px; padding: 0 12px; cursor: pointer;
  border: 0; border-radius: 0; background: none; text-align: left;
  font-family: var(--font-text); color: var(--lp-text);
  transition: background .25s var(--lp-ease), color .25s var(--lp-ease); }
.work__hit:hover { background: var(--lp-hi); color: var(--lp-ink); }
.work__name { flex: 1 1 auto; min-width: 0; font-family: var(--font-display);
  font-size: 16px; line-height: 24px; letter-spacing: -0.01em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* Состояние и срок — метки: короткие, называют, а не рассказывают. */
.work__state, .work__keep { flex: none; font-family: var(--font-mono);
  font-weight: 700; font-size: 11px; line-height: 24px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--lp-muted); }
.work__keep { min-width: 132px; text-align: right; }
.work--await .work__state { color: var(--lp-key); }
.work--failed .work__state { color: var(--lp-text); }
.work__acts { flex: none; display: flex; gap: 12px; padding-right: 12px; }
/* Единственное движение на странице: оно означает работу, а не украшает её. */
.work__bar { position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
  background: transparent; overflow: hidden; }
.work__bar i { display: block; height: 100%; width: 0; background: var(--lp-key);
  transition: width .35s var(--lp-ease); }
.work__bar.is-idle i { width: 35%; animation: work-slide 1.1s ease-in-out infinite; }
@keyframes work-slide { 0% { margin-left: -35%; } 100% { margin-left: 100%; } }
/* Ящик утоплен, а не приподнят: он часть строки, а не окно поверх неё.
   min-height: 0 обязателен — иначе содержимое не даёт строке сжаться в 0fr. */
.work__panel { min-height: 0; overflow: hidden; background: var(--sl-surface); }
.work__panel > * { width: min(576px, 100%); padding: 24px 12px; }
```

Правило `.work__mark` оставить: метка исхода переезжает на строку в Task 4.

- [ ] **Step 6: Прогнать тесты и синтаксис**

Run: `node --check app/static/creatives.js && python -m pytest tests/unit/test_app3_pages.py -v`
Expected: новые тесты PASS. Тест `test_creatives_js_renders_banner_grid` упадёт на `feedGrid` — это ожидаемо: переписать его под строку, заменив утверждения на `assert "workRow" in js`, `assert "views.push" in js`, `assert "lightbox" in js`, и поправить докстроку: «Готовый прогон — одна строка; баннеры листает лайтбокс». То же с `test_creatives_js_loads_recent_tasks` (`feedGrid` → `feedList`) и `test_index_has_recent_tasks_panel` (`id="feedGrid"` → `id="feedList"`).

- [ ] **Step 7: Коммит**

```bash
git add app/static/creatives.js app/static/app.css app/templates/creatives.html tests/unit/test_app3_pages.py
git commit -m "App3: прогон — одна строка, а не двенадцать плиток; срок хранения читается в часах"
```

---

## Task 4: Ящик под строкой — остановки переезжают из лайтбокса

**Files:**
- Modify: `app/templates/creatives.html:138-248` (лайтбокс), `app/static/creatives.js:378-454,511-543,588-615`
- Modify: `app/static/app.css` (`.cand-grid`)
- Test: `tests/unit/test_app3_pages.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_stops_live_in_the_row_not_in_a_modal(tmp_path, monkeypatch):
    """Остановка — состояние работы, а не окно поверх неё. Панели лежат в
    разметке отдельным контейнером и ПЕРЕЕЗЖАЮТ в ящик строки: пересборка
    оторвала бы обработчики, навешанные при загрузке."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert 'id="stops"' in html
        # три панели ушли из лайтбокса, но остались в документе
        lightbox = html.split('id="lightbox"', 1)[1]
        for pid in ("personaPanel", "textPanel", "imagePanel"):
            assert f'id="{pid}"' in html, pid
            assert f'id="{pid}"' not in lightbox, pid
        js = c.get("/static/creatives.js").text
        assert "openStop" in js
        assert "lightbox__panel--form" not in js   # режимов остался один


def test_candidates_are_rows_not_a_second_grid_of_cards(tmp_path, monkeypatch):
    """Двенадцать предложений — второе поле одинаковых карточек. Строки."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        assert "minmax(264px, 1fr)" not in css
        assert ".cand-grid { display: flex" in css


def test_outcome_is_marked_on_the_run_not_on_each_banner(tmp_path, monkeypatch):
    """Исход у работы один, а не у каждого из двенадцати баннеров."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "outcomeGroup" in js
        assert "buildViewActions" in js
        # отметка больше не строится внутри колонки действий лайтбокса
        actions = js.split("function buildViewActions", 1)[1].split("\n  }", 1)[0]
        assert "outcomeGroup" not in actions
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/unit/test_app3_pages.py -k "stops_live or candidates_are_rows or outcome_is_marked" -v`
Expected: FAIL — `assert 'id="stops"' in html`.

- [ ] **Step 3: Вынести три панели из лайтбокса**

В `app/templates/creatives.html` вырезать три блока `<div class="lb-col hidden" id="personaPanel">`, `id="textPanel"`, `id="imagePanel"` целиком (строки 157-245) и вставить их **после** закрывающего `</div>` лайтбокса, обернув:

```html
  {# Хранилище панелей остановок. Панели лежат в разметке, а не собираются
     скриптом: на них навешаны обработчики при загрузке, и пересборка отрывала
     бы их. Открывая работу, скрипт ПЕРЕМЕЩАЕТ нужную панель в ящик строки —
     перемещение узла обработчики переживает, пересборка нет. #}
  <div class="stops hidden" id="stops">
    ... три вырезанных блока ...
  </div>
```

В самих панелях заменить кнопку закрытия `<button ... data-lb="close">✕</button>` на `<button type="button" class="t-btn" data-stop="close">Свернуть</button>` — окна больше нет, крестик закрывать нечего.

- [ ] **Step 4: Ящик открывает и закрывает панели**

В `app/static/creatives.js` заменить заглушку `toggleRow` на:

```js
  // Что лежит в ящике — зависит от состояния работы. Идущий прогон не
  // открывается: показывать нечего, живой шаг стоит на самой строке.
  const STOP_PANEL = {
    awaiting_persona: "personaPanel",
    awaiting_text: "textPanel",
    awaiting_image: "imagePanel",
  };

  function toggleRow(uid) {
    const t = byUid(uid);
    const p = stateEls.get(uid);
    if (!t || !p) return;
    if (t.status === "queued" || t.status === "running") return;
    if (t.status === "failed" || t.status === "cancelled") return;
    if (p.wrap.classList.contains("is-open")) { closeRow(uid); return; }
    openRow(uid);
  }

  function openRow(uid) {
    const t = byUid(uid);
    const p = stateEls.get(uid);
    if (!t || !p) return;
    // Открыт всегда один ящик: два раскрытых прогона рядом — это снова каша.
    stateEls.forEach((_, other) => { if (other !== uid) closeRow(other); });
    p.panel.innerHTML = "";
    const stop = STOP_PANEL[t.status];
    if (stop) p.panel.appendChild($(stop));       // перемещение, не копия
    else if (t.status === "done") fillDoneDrawer(t, p.panel);
    p.wrap.classList.add("is-open");
    p.wrap.querySelector(".work__hit").setAttribute("aria-expanded", "true");
  }

  function closeRow(uid) {
    const p = stateEls.get(uid);
    if (!p || !p.wrap.classList.contains("is-open")) return;
    // Панель возвращается в хранилище живой: узел тот же, обработчики те же.
    HITL_PANELS.forEach((id) => {
      const node = $(id);
      if (node && p.panel.contains(node)) $("stops").appendChild(node);
    });
    p.panel.innerHTML = "";
    p.wrap.classList.remove("is-open");
    p.wrap.querySelector(".work__hit").setAttribute("aria-expanded", "false");
  }

  const HITL_PANELS = ["personaPanel", "textPanel", "imagePanel"];

  // Готовая работа: ряд превью с прокруткой вбок и отметка исхода. Ряд, а не
  // сетка: двенадцать квадратов в сетке — ровно то, от чего ушли.
  function fillDoneDrawer(t, box) {
    const body = el("div", "work__done");
    const imgs = Array.isArray(t.images) ? t.images : [];
    if (imgs.length) {
      const strip = el("div", "strip");
      imgs.forEach((u, i) => {
        const b = el("button", "strip__item");
        b.type = "button";
        const img = el("img");
        img.loading = "lazy";
        img.src = P + u;
        img.alt = "Баннер " + (i + 1);
        b.appendChild(img);
        const pos = views.findIndex((v) => v.uid === t.task_uid && v.idx === i);
        b.addEventListener("click", () => openView(pos < 0 ? 0 : pos));
        strip.appendChild(b);
      });
      body.appendChild(strip);
    }
    body.appendChild(outcomeGroup(t));
    box.appendChild(body);
  }
```

- [ ] **Step 5: Остановка открывает свою строку, а не окно**

Заменить в `onAwaiting` три вызова `openLb("...Panel")` на общий `openStop(silent)`, добавив рядом:

```js
  // Остановка открывает СВОЮ строку. Раньше здесь выскакивало окно поверх
  // ленты; окно закрывали — и работа терялась из виду, хотя ждала человека.
  function openStop(silent) {
    if (silent) return;   // восстановление после перезагрузки: строка ждёт клика
    openRow(taskUid);
  }
```

и заменить `closeHitl()` на:

```js
  // Пайплайн поехал дальше — ящик закрываем, просмотр баннера не трогаем.
  function closeHitl() { if (taskUid) closeRow(taskUid); }
```

Удалить из файла `LB_MODE`, `HITL_MODES`, `LB_PARTS` и ветки `openLb` для панелей: режим остался один. `openLb` упрощается до:

```js
  function openLb() {
    show($("lbView"));
    show($("lbSide"));
    lbPanel.className = "lightbox__panel";
    lbMode = "view";
    show(lightbox);
    document.body.style.overflow = "hidden";
  }
```

`openView(pos)` вызывает `openLb()` без аргумента.

- [ ] **Step 6: Убрать отметку исхода из лайтбокса**

В `buildViewActions` удалить строку `box.appendChild(outcomeGroup(t));`. В `sendOutcome` заменить финальный `renderFeed();` на:

```js
    // Исход теперь у работы, а не у каждого баннера: перерисовывать всю ленту
    // незачем, меняется одна строка.
    syncState(t.task_uid);
```

- [ ] **Step 7: Кандидаты строками**

Заменить `.cand-grid` и `.cand` в `app/static/app.css` на:

```css
/* ===== 12 предложений: строки, а не второе поле карточек =====
   Ведущее метим планкой ключа слева — тем же приёмом, что выбранную строку. */
.cand-grid { display: flex; flex-direction: column; }
.cand { position: relative; display: flex; flex-direction: column; gap: 12px;
  padding: 12px; box-shadow: inset 0 -1px 0 var(--lp-line-2); }
```

- [ ] **Step 8: Ряд превью в CSS**

Добавить рядом с `.work__panel`:

```css
/* Ряд превью: прокрутка вбок, высота 192 = восемь модулей. Форматы баннеров
   одинаковые, поэтому ряд не рвётся — но кадрирование всё равно cover, чтобы
   ряд не зависел от того, что вернул композер. */
.work__done { display: flex; flex-direction: column; gap: 24px; }
.strip { display: flex; gap: 12px; overflow-x: auto; scrollbar-width: thin;
  padding-bottom: 12px; }
.strip__item { flex: none; width: 96px; height: 192px; padding: 0; border: 0;
  border-radius: 0; cursor: pointer; background: var(--lp-ink);
  box-shadow: inset 0 0 0 1px var(--lp-line-2); }
.strip__item img { display: block; width: 100%; height: 100%; object-fit: cover; }
```

- [ ] **Step 9: Прогнать**

Run: `node --check app/static/creatives.js && python -m pytest tests/unit/test_app3_pages.py -v`
Expected: три новых теста PASS, остальные зелёные.

- [ ] **Step 10: Коммит**

```bash
git add app/static/creatives.js app/static/app.css app/templates/creatives.html tests/unit/test_app3_pages.py
git commit -m "App3: остановка открывается в строке, а не окном поверх ленты"
```

---

## Task 5: Третий регистр

**Files:**
- Modify: `app/static/app.css` (правила `.t-btn`, `button[type=submit]`, `.chip`, `.seg__btn`, `.dropzone`, `.status`, `.feed__foot`, `.cand__flag`, `.exp-item__tag`, `.tool-details > summary`, `.lp-foot`)
- Test: `tests/unit/test_app3_pages.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_commands_are_set_in_the_text_register(tmp_path, monkeypatch):
    """Критерий регистра — что строка делает. Метка называет (≲12 знаков) и
    остаётся моно-капсом; команда обращается к человеку и набирается текстом.
    Капс убирает выносные — различители, по которым слово узнают целиком; на
    метке это не мешает, на фразе слово приходится читать по буквам."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        btn = css.split("body.is-tool .t-btn {", 1)[1].split("}", 1)[0]
        assert "text-transform: uppercase" not in btn
        assert "var(--font-text)" in btn
        assert "font-size: 12px" in btn
        submit = css.split('body.is-tool .gen-form button[type="submit"] {', 1)[1].split("}", 1)[0]
        assert "text-transform: uppercase" not in submit
        # метки регистр не меняют
        label = css.split("body.is-tool .field-label {", 1)[1].split("}", 1)[0]
        assert "text-transform: uppercase" in label
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/unit/test_app3_pages.py -k "text_register" -v`
Expected: FAIL — `assert 'text-transform: uppercase' not in btn`.

- [ ] **Step 3: Перевести команды в текстовый регистр**

В `body.is-tool .t-btn` заменить типографическую часть на:

```css
  color: var(--lp-soft); font-family: var(--font-text); font-weight: 600;
  font-size: 12px; line-height: 12px; letter-spacing: 0; text-transform: none;
```

⚠️ `<button>` не наследует гарнитуру — без явного `font-family` придёт системный шрифт формы. `font: inherit` тут не годится: от родителя приедет дисплейный полужирный.

В `body.is-tool .gen-form button[type="submit"]` заменить на:

```css
  font-family: var(--font-text); font-weight: 600; font-size: 13px;
  line-height: 24px; letter-spacing: 0; text-transform: none;
```

В `body.is-tool .chip, body.is-tool .seg__btn` — те же четыре свойства, что у `.t-btn` (12px/600, `letter-spacing: 0`, `text-transform: none`).

- [ ] **Step 4: Перевести фразы**

Заменить типографику в:
- `.dropzone` — на `font-family: var(--font-text); font-weight: 400; font-size: 13px; letter-spacing: 0; text-transform: none;`
  ⚠️ **Специфичность.** Канон пишет `body.is-tool .gen-form label` = (0,2,2); `.dropzone` (0,1,0) и `body.is-tool .dropzone` (0,2,1) проигрывают ему **молча**. Рабочий селектор — `body.v11.is-tool label.dropzone` (0,3,2). То же правило навязывает `line-height: 12px` — перебить `line-height: 24px` отдельной строкой. Проверять `getComputedStyle`, не глазами.
- `body.is-tool .status` — 13/24, текстовый, без капса;
- `.feed__foot` — 13/24, текстовый;
- `.cand__flag`, `.exp-item__tag`, `.tool-details > summary`, `.dropzone__hint`, `.thumb span`, `.scen-card__desc` — 13/24 текстовым, без `letter-spacing` и капса;
- `.lp-foot` — 13/24 текстовым.

Не трогать (остаются моно-капсом): `.field-label`, `.feed__title`, `.feed__count`, `.route__stop`, `.lb-counter`, `.lb-group__label`, `.cand__rank`, `.cand__score`, `.kv b`, `.lib-card__meta`, `.work__mark`, `.work__state`, `.work__keep`, `body.v11 .topnav__link`, `body.v11 .logout-form button` (шапка побайтово канонная — её не трогать вовсе).

- [ ] **Step 5: Прогнать и проверить живьём**

Run: `python -m pytest tests/unit/test_app3_pages.py -v`
Expected: PASS.

Затем поднять предпросмотр и убедиться, что оверрайд дропзоны действительно взял:

```js
getComputedStyle(document.querySelector(".dropzone")).textTransform  // "none"
getComputedStyle(document.querySelector(".dropzone")).lineHeight     // "24px"
```

- [ ] **Step 6: Коммит**

```bash
git add app/static/app.css tests/unit/test_app3_pages.py
git commit -m "App3: команда обращается к человеку — и набирается как обращение, а не как метка"
```

---

## Task 6: `/webinar` и `/library` тем же диалектом

**Files:**
- Modify: `app/templates/webinar.html`, `app/static/webinar.js`, `app/templates/library.html`
- Test: `tests/unit/test_app3_pages.py`

- [ ] **Step 1: Написать падающий тест**

```python
def test_webinar_feed_is_a_list_of_rows(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/webinar", headers=_HDR).text
        assert 'class="feed__list"' in html
        assert 'class="feed__grid"' not in html


def test_library_separates_the_card_from_the_experience(tmp_path, monkeypatch):
    """Черновик карточки и отмеченный опыт — два разных предмета. В одной раме
    они читались как один; разделяет их пустота и смена регистра, не рамка."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/library", headers=_HDR).text
        assert 'id="cardPanel"' in html
        assert 'class="feed__body"' not in html
        assert "feed__split" in html
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/unit/test_app3_pages.py -k "webinar_feed or library_separates" -v`
Expected: FAIL.

- [ ] **Step 3: `/webinar` — лента строками**

В `app/templates/webinar.html` заменить `<div class="feed__grid" id="feedGrid">` на `<div class="feed__list" id="feedList" data-retention-hours="{{ retention_hours }}">`.

Дальше — **сначала прочитать `app/static/webinar.js` целиком** (774 строки; `grep -n "feedGrid\|renderFeed\|imgTile" app/static/webinar.js` покажет точки входа) и только потом править. Копировать код из Task 3 вслепую нельзя: у вебинаров своя модель задачи (26 форматов одного прогона, свой набор статусов), и подставленный без чтения `workRow` сломает то, что там уже работает. Переносится **приём**, а не текст: строка на 48 с именем слева и меткой состояния справа, `grid-template-rows: auto 0fr → 1fr` на раскрытие, ряд превью 192 в ящике. Классы (`.work`, `.work__head`, `.work__hit`, `.work__panel`, `.strip`) уже описаны в CSS из Task 3 и Task 4 — новых заводить не нужно.

Здесь визуальный ряд оправдан геометрией, а не привычкой: 26 форматов — это 26 разных размеров, и человек выбирает глазами. Кадрирование остаётся лайтбоксом: холст 360 в колонку 264 не влезает.

- [ ] **Step 4: `/library` — два предмета, не один ящик**

В `app/templates/library.html` заменить `<div class="feed__body" id="feedBody">` на:

```html
        {# Карточка и опыт — разные предметы. Разделяет их пустота 48 и смена
           регистра заголовка, а не общая рама: рамка сказала бы «это одно». #}
        <div class="feed__split" id="feedBody">
```

Добавить в `app/static/app.css` рядом с `.feed__list`:

```css
.feed__split { display: flex; flex-direction: column; gap: 48px; }
```

Удалить правило `.feed__body`.

- [ ] **Step 5: Прогнать**

Run: `node --check app/static/webinar.js && node --check app/static/library.js && python -m pytest tests/unit/ -v`
Expected: PASS.

- [ ] **Step 6: Коммит**

```bash
git add app/templates/webinar.html app/templates/library.html app/static/webinar.js app/static/app.css tests/unit/test_app3_pages.py
git commit -m "App3: вебинары и библиотека говорят тем же диалектом, что креативы"
```

---

## Task 7: Сверка

**Files:** правок нет, только проверки.

- [ ] **Step 1: Весь набор тестов**

Run: `python -m pytest -q`
Expected: 611+ passed, 0 failed. Любое падение чинить в коде, а не ослаблением теста.

- [ ] **Step 2: Линтеры**

Run: `python -m ruff check app tests && node --check app/static/creatives.js && node --check app/static/webinar.js && node --check app/static/library.js`
Expected: пусто.

- [ ] **Step 3: Подсказки не выросли**

Добавить последний тест:

```python
def test_hints_did_not_grow(tmp_path, monkeypatch):
    """Подсказка остаётся, только если без неё человек ошибётся."""
    import re
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        hints = re.findall(r'<p class="muted">(.*?)</p>', html, re.S)
        total = sum(len(re.sub(r"<[^>]+>", "", h).strip()) for h in hints)
        assert total <= 240, total
```

Run: `python -m pytest tests/unit/test_app3_pages.py -k hints_did_not_grow -v`
Expected: PASS.

- [ ] **Step 4: Живые замеры**

Поднять предпросмотр и проверить числом, а не картинкой (`preview_screenshot` в этом окружении стабильно таймаутится на 30 с):

```js
// геометрия: колонка формы осталась 264
document.querySelector(".rail").getBoundingClientRect().width            // 360
document.querySelector(".gen-form").getBoundingClientRect().width        // 264
// гистограмма кеглей: 11px больше не единственный кегль страницы
[...document.querySelectorAll("body *")].reduce((m, e) => {
  const s = getComputedStyle(e).fontSize; m[s] = (m[s] || 0) + 1; return m; }, {})
```

Ожидание: 11px перестал быть модой распределения; в наборе присутствуют 44, 16, 13, 12, 11.

- [ ] **Step 5: Коммит**

```bash
git add tests/unit/test_app3_pages.py
git commit -m "App3: подсказки закреплены тестом — объяснять экран текстом больше нечем"
```

---

## Что этот план сознательно НЕ делает

- **Не переименовывает разделы** («Креативы» → «Промо-баннеры»). Шапка обязана быть побайтово одинаковой во всех приложениях; пока App1 не обновил `gateway/templates/base.html`, правка у себя развела бы два имени одного раздела.
- **Не удаляет `app/static/grid.js`.** Файл — общий модуль портала; он просто перестаёт подключаться.
- **Не трогает технический контракт**: bind 127.0.0.1, личность только из `X-User-Id`/`X-User-Email`, своя БД и очередь, SSE, регидрация активной задачи, абсолютные ссылки в шапке.
- **Не меняет серверный API.** Единственная правка на стороне сервера — вывод `retention_hours` в атрибут; сам эндпоинт и модели не трогаются.
