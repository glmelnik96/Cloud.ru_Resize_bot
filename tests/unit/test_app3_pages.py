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
        # canon nav: 4 sections, is-active on Промо-баннеры, brand split span
        assert 'href="/images"' in html
        assert 'href="/slides"' in html
        assert 'href="/creatives" class="topnav__link is-active"' in html
        # Разделы названы тем, что человек получает на выходе. «Слайды» и
        # «Креативы» — слова производства, а не результата: слайд это единица
        # файла, креатив — жаргон отдела. data-t обязан совпадать с надписью,
        # иначе шторка прокручивает одно, а на месте стоит другое.
        assert 'data-t="Презентации">Презентации' in html
        assert 'data-t="Промо-баннеры">Промо-баннеры' in html
        assert "Слайды" not in html
        assert "Креативы" not in html
        # Вебинарные ресайзы — не раздел платформы, а внутренность промо-баннеров,
        # ровно как библиотека. Пятым пунктом они делали шапку списком страниц
        # приложения вместо списка разделов портала. Вход остался в подвале.
        assert 'data-t="Вебинары"' not in html
        # Трансляции сняты по решению Глеба (2026-08-23): в шапке остаются три
        # раздела, которые делают материал. Ссылка на /present ушла из App3
        # целиком — если раздел живой, вход в него обязан вернуть шлюз.
        assert 'data-t="Трансляции"' not in html
        assert 'href="/present"' not in html
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
    """Законченный прогон обязан пережить перезагрузку страницы: лента — этаж
    чернил, и её наполняет JS из GET /api/tasks."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert 'id="feedList"' in html  # сюда JS кладёт строки работ
        assert 'id="feedCount"' in html  # счётчик работ в шапке этажа
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
        assert "feedList" in js  # строки работ уезжают на этаж чернил
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
    """Готовый прогон — одна строка; баннеры листает лайтбокс.
    Прогон с двенадцатью баннерами остаётся одним предметом ленты: сетка
    одинаковых квадратов и была той кашей, ради которой всё затевалось."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "images" in js  # consume the per-task image URLs
        assert "workRow" in js  # прогон — строка, а не набор плиток
        assert "views.push" in js  # лайтбокс листает по всем баннерам ленты
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
    """Страница вебинарных ресайзов несёт канон-шапку и свой скрипт кадрирования.
    Своего пункта в шапке у неё нет: ресайзы — внутренность промо-баннеров, как и
    библиотека, поэтому подсвечены «Промо-баннеры», а путь назад даёт подвал."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        r = c.get("/webinar", headers=_HDR)
        assert r.status_code == 200
        html = r.text
        # data-t обязателен на каждом пункте канон-шапки: без него шторка не
        # умеет прокручивать надпись, поэтому проверяем пункт целиком.
        assert 'class="topnav__link is-active" data-t="Промо-баннеры">Промо-баннеры' in html
        assert 'data-t="Вебинары"' not in html
        # Раз пункта в шапке нет, путь назад обязан быть на странице — иначе
        # ресайзы становятся тупиком, куда попадают и откуда не выходят.
        # Ссылка идёт через prefix: за шлюзом корень приложения — /creatives/.
        assert 'href="/creatives/">Промо-баннеры' in html
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
        assert 'class="topnav__link is-active" data-t="Промо-баннеры">Промо-баннеры' in html
        assert "topnav__link\">Библиотека" not in html
        assert 'href="/creatives/">Вернуться к промо-баннерам' in html
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
        assert "--sl-stone: #D9DEDB" in css  # камень, не белый
        assert "--lp-ink: var(--sl-stone)" in css  # рельс берёт его оттуда же, что и сетка
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
    """Граница этажей идёт от края до края окна и работает разделителем.
    Рама вокруг ленты поверх неё — вторая линия, делающая ту же работу.

    Камень прибит к окну целиком (top: 0, высота 100vh), а не отодвинут под
    шторку: шторка канона висит поверх страницы (fixed, top: 18px) и в потоке
    ничего не занимает, так что любой отступ сверху оставил бы над камнем
    полосу чернил — и колонка перестала бы читаться этажом."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        assert ".feed { position: relative; isolation: isolate" not in css
        assert "grid-template-columns: 360px 1fr" in css
        assert "position: sticky; top: 0;" in css  # камень стоит, работы едут мимо
        assert "height: 100vh; overflow: hidden auto" in css
        assert "padding: 96px var(--sl-gut)" in css


def test_a_finished_run_is_one_row_not_twelve_tiles(tmp_path, monkeypatch):
    """Двенадцать баннеров — это один предмет «работа», а не двенадцать.
    Взрыв прогона в сетку одинаковых квадратов и был той самой кашей."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        js = c.get("/static/creatives.js").text
        assert "imgs.forEach((u, i) => feedGrid" not in js  # сетка плиток
        assert "feedGrid" not in js                         # рама сетки
        assert "workRow" in js                              # строка работы
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
        assert "retentionHours" in js  # окно приезжает атрибутом, не хардкодом
        assert '+ "Z"' in js           # наивный UTC: без суффикса дата врёт
        html = c.get("/", headers=_HDR).text
        assert 'data-retention-hours="24"' in html


def test_stops_live_in_the_row_not_in_a_modal(tmp_path, monkeypatch):
    """Остановка — состояние работы, а не окно поверх неё. Панели лежат в
    разметке отдельным контейнером и ПЕРЕЕЗЖАЮТ в ящик строки: пересборка
    оторвала бы обработчики, навешанные при загрузке."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        assert 'id="stops"' in html
        # три панели ушли из лайтбокса, но остались в документе
        lightbox = html.split('id="lightbox"', 1)[1].split('id="stops"', 1)[0]
        for pid in ("personaPanel", "textPanel", "imagePanel"):
            assert f'id="{pid}"' in html, pid
            assert f'id="{pid}"' not in lightbox, pid
        js = c.get("/static/creatives.js").text
        assert "openStop" in js
        assert "lightbox__panel--form" not in js  # режимов остался один


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


def test_commands_are_set_in_the_text_register(tmp_path, monkeypatch):
    """Критерий регистра — что строка делает. Метка называет (не длиннее ~12
    знаков) и остаётся моно-капсом; команда обращается к человеку и набирается
    текстом. Капс убирает выносные — различители, по которым слово узнают
    целиком; на метке это не мешает, на фразе слово читают по буквам."""
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


def test_webinar_feed_is_a_list_of_rows(tmp_path, monkeypatch):
    """Прогон вебинарных ресайзов — тоже одна работа, а не поле плиток.
    Форматов 26, но предмет один, и в ленте он занимает одну строку."""
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


def test_hints_did_not_grow(tmp_path, monkeypatch):
    """Подсказка остаётся, только если без неё человек ошибётся. Порог держит
    экран честным: объяснять устройство раздела текстом больше нечем, устройство
    должно читаться само. Свёрнутое «Как это работает» в счёт входит — иначе
    абзацы просто переехали бы туда."""
    import re
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        html = c.get("/", headers=_HDR).text
        hints = re.findall(r'<p class="muted">(.*?)</p>', html, re.S)
        total = sum(len(re.sub(r"<[^>]+>", "", h).strip()) for h in hints)
        assert total <= 240, total


def test_stone_floor_reaches_the_bottom_of_the_page(tmp_path, monkeypatch):
    """Камень — этаж, а не колонка высотой в окно. Рельс липкий и ростом ровно
    в экран, поэтому ниже первого экрана его столбец оставался бы чернилами:
    светлое поле обрывалось бы посреди страницы. Красит столбец не рельс и даже
    не сетка, а main — единственный узел, который тянется до самого низа
    страницы, включая полосу подвала. Цвет берётся из одного места."""
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        css = c.get("/static/app.css").text
        assert "--sl-stone:" in css
        main = css.split("body.is-tool main {", 1)[1].split("}", 1)[0]
        assert "linear-gradient" in main
        assert "var(--sl-stone)" in main
        assert "360px" in main
        rail = css.split(".rail { --lp-ink:", 1)[1].split(";", 1)[0]
        assert "var(--sl-stone)" in rail
        # В одну колонку красить нечего: рельс встаёт НАД лентой, и вертикальная
        # заливка слева легла бы поперёк обоих этажей.
        narrow = css.split("@media (max-width: 860px) {", 1)[1]
        assert "background: none" in narrow.split("body.is-tool main {", 1)[1].split("}", 1)[0]
        # Подвал — служебная строка этажа чернил, и начинаться обязан там же, где
        # этаж. Отцентрованный по всей ширине, он заезжает светлыми ссылками на
        # камень: на 900px «Библиотека знаний» встаёт левее 360 и пропадает.
        foot = css.split("body.is-tool .lp-foot {", 1)[1].split("}", 1)[0]
        assert "margin-left: 360px" in foot
        assert "margin-left: 0" in narrow.split("body.is-tool .lp-foot {", 1)[1].split("}", 1)[0]
