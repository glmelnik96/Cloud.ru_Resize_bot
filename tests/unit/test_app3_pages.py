"""App3 — HTML page + static mount (canon v2 header)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from starlette.testclient import TestClient  # noqa: E402

import app.services.creatives as creatives_mod  # noqa: E402
from app.main import create_app  # noqa: E402

_HDR = {"X-User-Id": "3", "X-User-Email": "gleb@cloud.ru"}


def _app(tmp_path, monkeypatch, **extra):
    async def fake_init_graph(checkpoint_db):
        return object(), None

    monkeypatch.setattr(creatives_mod, "init_graph", fake_init_graph)
    return create_app({"db_url": f"sqlite+aiosqlite:///{tmp_path / 'p.db'}", **extra})


def test_index_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/").status_code == 401


def test_index_renders_canon_header(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        # canon nav: 3 sections, is-active on Креативы, brand split span
        assert 'href="/images"' in html
        assert 'href="/slides"' in html
        assert 'href="/creatives" class="topnav__link is-active"' in html
        assert "Cloud.ru <span>Design</span>" in html
        # user email + logout to gateway
        assert "gleb@cloud.ru" in html
        assert 'action="/logout"' in html
        # assets go through the gateway prefix
        assert "/creatives/static/app.css" in html
        assert "/creatives/static/creatives.js" in html


def test_static_css_served(tmp_path, monkeypatch):
    """Отдаётся лист канона v11: чернила, единственный акцент и модуль 24.
    Ключ проверяем по --lp-key, а не по --accent: --accent остался ради
    совместимости, а палитру портала задаёт именно --lp-key.
    Чернила сведены с App2: полюса у двух разделов одного портала обязаны
    совпадать, иначе переход между ними читается сменой площадки."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/static/app.css")
        assert r.status_code == 200
        css = r.text
        assert "--lp-key: #3FB67C" in css  # единственный акцент палитры
        assert "--lp-ink: #141817" in css  # чернила
        assert "--lp-cell: 24px" in css  # модуль, на котором стоит вся геометрия


def test_static_font_served(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/static/fonts/sb-sans-text-400.woff2")
        assert r.status_code == 200
        assert r.content[:4] == b"wOF2"  # woff2 magic


def test_index_has_recent_tasks_panel(tmp_path, monkeypatch):
    """Законченный прогон обязан пережить перезагрузку страницы: лента —
    единственная рама на экране, и её наполняет JS из GET /api/tasks."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert 'id="feedGrid"' in html  # сюда JS кладёт плитки прогонов
        assert 'id="feedCount"' in html  # счётчик в шапке рамы
        assert 'id="feedFoot"' in html  # срок хранения — под лентой


def test_index_route_strip_replaces_pipeline_paragraph(tmp_path, monkeypatch):
    """Маршрут показывает путь задачи полосой из пяти остановок, а не абзацем.
    Полоса работает втройне — оглавление в покое, «мы здесь» в прогоне, «ждёт
    человека» на остановке, — поэтому перечисления шагов текстом в рельсе нет."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert 'id="route"' in html
        assert html.count('class="route__stop"') == 5
        # Номер отделён от подписи тегом: он несёт акцент плашкой, когда
        # остановка живая, и покраска слова целиком этого не даёт.
        for num, name in (("01", "Бриф"), ("02", "Персона"), ("03", "Тексты"),
                          ("04", "Hero"), ("05", "Баннеры")):
            assert f"<b>{num}</b>{name}" in html
        # тот самый абзац, который полоса заменила
        assert "01 · Бриф — продукт, аудитория, эмоция" not in html
        assert "Остановки 02–04 ждут человека" not in html


def test_creatives_js_paints_route_by_stage_number(tmp_path, monkeypatch):
    """Подсветку остановки ведёт число stage из SSE, а не русская подпись шага:
    подпись — текст для человека, её правка не должна гасить полосу."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "route__stop" in js
        assert "is-live" in js  # где мы сейчас
        assert "is-wait" in js  # остановка ждёт человека
        assert ".stage" in js  # число едет рядом с подписью


def test_static_css_has_route_component(tmp_path, monkeypatch):
    """Полоса маршрута — служебный регистр канона (mono-caps 11/700/.09em) и
    ни одной рамки: состояние метится волоском через inset box-shadow."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        assert ".route__stop" in css
        assert ".route__stop.is-live" in css
        assert ".route__stop.is-wait" in css


def test_creatives_js_loads_recent_tasks(tmp_path, monkeypatch):
    """The served JS fetches the task list and renders the ZIP link so a
    completed creative is re-downloadable within the 24h retention window."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "loadTasks" in js  # fetch + render recent tasks on load
        assert "feedGrid" in js  # плитки прогонов уезжают в раму ленты
        assert "result_url" in js  # done rows expose the ZIP download


def test_index_shows_retention_notice_from_config(tmp_path, monkeypatch):
    """The history panel tells the user how long runs are kept; the number is
    derived from RETENTION_TTL_SEC, not hard-coded."""
    with TestClient(_app(tmp_path, monkeypatch, retention_ttl_sec=7200)) as c:
        html = c.get("/", headers=_HDR).text
        assert "2 ч" in html  # 7200s // 3600 = 2 hours
        assert "удаляются" in html


def test_index_retention_notice_defaults_to_24h(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert "24 ч" in html


def test_creatives_js_renders_banner_grid(tmp_path, monkeypatch):
    """Finished task rows expand into a grid of their banner images with the
    original brief, per-thumb download, and a swipeable lightbox."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "images" in js  # consume the per-task image URLs
        assert "feedGrid" in js  # рама ленты, куда ложатся плитки
        assert "work__open" in js  # плитка прогона раскрывается в баннеры
        assert "brief" in js  # show the brief fields on expand
        assert "download" in js  # per-thumbnail download affordance
        assert "lightbox" in js  # gallery overlay to page through banners


def test_creatives_js_lightbox_navigation(tmp_path, monkeypatch):
    """The lightbox can page between banners (prev/next + keyboard)."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "ArrowRight" in js  # keyboard paging
        assert "ArrowLeft" in js


def test_creatives_js_rehydrates_active_task(tmp_path, monkeypatch):
    """Canon-header nav is a full reload; the JS must restore an in-flight run:
    persist the uid, look it up (localStorage / GET /api/tasks), snapshot via
    /pending, and reattach the EventSource (contract v3 §8)."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "localStorage" in js  # uid persisted across the reload
        assert "/api/tasks" in js  # discover the active task when storage is empty
        assert "/pending" in js  # snapshot the parked/running state before stream
        assert "rehydrate" in js  # run the restore on load


# ── local-smoke dev affordances (no gateway) ────────────────────────


def test_dev_user_allows_access_without_gateway_header(tmp_path, monkeypatch):
    """APP3_DEV_USER set → the page renders without a gateway X-User-Id header."""
    with TestClient(_app(tmp_path, monkeypatch, dev_user="gleb@cloud.ru")) as c:
        r = c.get("/")  # no _HDR
        assert r.status_code == 200
        assert "gleb@cloud.ru" in r.text


def test_no_dev_user_still_401_without_header(tmp_path, monkeypatch):
    """Default (no dev user) → unauthenticated request is rejected as in prod."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/").status_code == 401


def test_empty_prefix_serves_assets_and_api_at_root(tmp_path, monkeypatch):
    """APP3_PREFIX='' → assets/API resolve at root (local run without gateway)."""
    with TestClient(_app(tmp_path, monkeypatch, prefix="")) as c:
        r = c.get("/", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        assert 'href="/static/app.css?v=' in html
        assert 'src="/static/creatives.js?v=' in html
        assert 'window.APP_PREFIX = "";' in html


def test_webinar_page_renders(tmp_path, monkeypatch):
    """The webinar resizes page serves with canon nav (is-active on Вебинары)
    and loads its own fit-engine script."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/webinar", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        # data-t обязателен на каждом пункте канон-шапки: без него шторка не
        # умеет прокручивать надпись, поэтому проверяем пункт целиком.
        assert 'class="topnav__link is-active" data-t="Вебинары">Вебинары' in html
        assert "/creatives/static/webinar.js" in html
        assert 'id="fitCanvas"' in html


def test_webinar_page_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/webinar").status_code == 401


# ── библиотека знаний: страница, ссылки, ассеты ─────────────────────
# Проверки уровня страницы: рендер, топнав, отдача статики. Ролевая граница
# библиотеки живёт на сервере и покрыта поведенчески в tests/unit/test_kb_routes.


def test_library_page_requires_auth(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        assert c.get("/library").status_code == 401


def test_library_page_carries_canon_topbar(tmp_path, monkeypatch):
    """Страница отдаётся авторизованному и несёт канон-топбар: без него страница
    выпадает из единой оболочки платформы. Библиотека — не раздел платформы, а
    внутренность «Креативов», поэтому в топнаве подсвечены именно «Креативы», и
    со страницы есть путь назад: иначе библиотека — тупик без кнопки «домой»."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/library", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        assert "Библиотека знаний" in html
        assert 'class="topnav__link is-active" data-t="Креативы">Креативы' in html
        assert "topnav__link\">Библиотека" not in html
        assert "Вернуться к генерации креативов" in html
        assert 'href="/images"' in html
        assert 'href="/slides"' in html
        assert 'href="/creatives"' in html
        assert "Cloud.ru <span>Design</span>" in html
        assert 'action="/logout"' in html
        assert "gleb@cloud.ru" in html
        # Ассеты и API идут через префикс шлюза — иначе на проде 404 на всё.
        assert 'window.APP_PREFIX = "/creatives";' in html
        assert "/creatives/static/library.js" in html
        assert "/creatives/static/app.css" in html


def test_library_link_lives_in_the_brief(tmp_path, monkeypatch):
    """Страница без ссылки — страница, которой нет: адрес библиотеки никто не
    помнит наизусть. Но живёт ссылка не в топнаве платформы, а в брифе, рядом с
    выбором карточки — там единственное место, где человек про эту карточку
    думает и хочет проверить, что в ней написано.

    Ссылка идёт через префикс шлюза: топнав платформы прибит к /creatives/ и
    локально ведёт в никуда, а этот переход должен работать в обеих сборках."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert 'href="/creatives/library"' in html
        assert "Библиотека знаний" in html
        # Соседство с выбором карточки: подсказка идёт сразу за селектором.
        assert html.index('id="productSlug"') < html.index('href="/creatives/library"')
        assert html.index('href="/creatives/library"') < html.index('id="product"')
        assert 'class="topnav__link">Библиотека' not in html


def test_library_js_is_served_and_not_a_stub(tmp_path, monkeypatch):
    """Текстовый smoke по отдаваемому library.js: файл на месте и не выродился
    в заглушку — в нём остались ролевые ветки, вызовы API и защиты правки.

    Это НЕ проверка срабатывания гардов: JS здесь не исполняется, и отличить
    `if (me.can_edit_kb)` от `if (true)` тест не в состоянии. Настоящая граница
    прав — серверная, она покрыта поведенчески в test_kb_routes.py
    (test_write_requires_role). Здесь ловится только исчезновение кода."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/static/library.js")
        assert r.status_code == 200
        js = r.text
        assert "can_edit_kb" in js  # ветка правки карточек
        assert "rolesPanel" in js  # ветка админской панели доступов
        assert "/api/kb/products" in js  # чтение и запись каталога
        assert "/api/admin/roles" in js  # раздача доступов
        assert "history" in js  # история версий карточки
        assert "disabled = false" in js  # редактору поля отпираются
        assert "editRow" in js
        assert "createRow" in js
        assert "historyBox" in js
        # Архивирование выключает карточку для всех будущих запусков — молча
        # такое не делают, и вернуть карточку должно быть можно из UI.
        assert "window.confirm" in js
        assert "Вернуть из архива" in js
        # Оборванная сеть не должна оставлять «Сохраняю…» навсегда: jsend
        # ловит отказ fetch и отдаёт null, errText это называет вслух.
        assert "catch (_) { return null; }" in js
        assert "Нет соединения с сервером" in js


def test_app_css_carries_library_classes(tmp_path, monkeypatch):
    """Список продуктов, строки опыта и блоки карточки держатся на
    .scen-card/.exp-item/.kb-block — без них список рендерится системными
    кнопками, а блоки схлопываются в однострочные поля."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/static/app.css")
        assert r.status_code == 200
        css = r.text
        assert ".scen-card" in css  # строка каталога продуктов
        assert ".exp-item" in css  # строка отмеченного опыта и доступов
        assert ".kb-block" in css  # высокие поля блоков карточки


def test_css_repaints_the_stone_floor_by_tokens_only(tmp_path, monkeypatch):
    """Светлый этаж перекрашивается переопределением переменных на .rail.
    Ни одного правила вида `.rail .что-то { color: ... }` быть не должно:
    дубль цвета — это второе место, где живёт правда о палитре."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        assert "--lp-ink: #D9DEDB" in css  # камень, не белый
        assert "--lp-muted: #565E5B" in css  # 4.6:1 к камню — AA
        assert "--lp-hi: #1B211F" in css  # инверсия на светлом идёт в темноту
        assert "--lp-key: #35A171" in css  # ступень вниз: #3FB67C на камне светится
        assert "--sl-surface: #CBD1CE" in css  # ящик утоплен, а не приподнят
        # Полюса сведены с App2: расхождение в одну ступень между двумя
        # разделами одного портала хуже, чем разница двух оттенков серого.
        assert "--lp-line-2: #2C3532" in css
        assert "--lp-line-2: #333D3A" not in css
        assert "scrollbar-gutter: stable" in css


def test_css_has_no_duplicated_colour_rules_under_rail(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        for sel in (".rail .t-btn", ".rail .field-label", ".rail input", ".rail button"):
            assert sel not in css
