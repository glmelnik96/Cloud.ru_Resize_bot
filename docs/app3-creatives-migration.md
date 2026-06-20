# App3 / creatives — миграция Resize_bot в веб-подсайт шлюза

> Статус: **в работе** · контракт-источник: `Brand_Image_site/docs/superpowers/specs/2026-06-20-subapp-integration-contract.md` · эталон: App1 (`/images`).

Resize_bot (Telegram-бот) переезжает в FastAPI веб-подсайт **App3** (префикс `/creatives`,
порт `127.0.0.1:8013`) за платформенным шлюзом. Интерактивный HITL пайплайна `/new`
сохраняется. Ядро (`graph/`, `infra/`, `llm/`, `config/`, `agents/`) переиспользуется как
есть; заменяется только слой ввода-вывода (`bot/` → `app/`).

## Ключевое решение
Длинная интерактивная задача `/new` режется на **сегменты** между паузами HITL. Семафор
параллелизма держится только на время вычислений сегмента и освобождается, как только граф
паркуется на `interrupt()`. Склейка — через durable-чекпойнт.
- **SQLite** (`CHECKPOINT_DB`, отдельный файл) — langgraph-чекпойнты (graph state, park/resume), ключ = `task_uid`. Redis НЕ используется (решение платформы 2026-06-21).
- **SQLite** (`data/app3.db`, по App1) — только Task/User lifecycle.

## Машина состояний задачи
```
queued → running → awaiting_text → (approve) → running
       → awaiting_image → (upload | generate) → running → done
       regenerate/refine → назад к кандидатам; cancel/timeout → cancelled
```

## API-контракт (для чата-шлюза: страница creatives.html)
Роуты живут от корня (шлюз срезает префикс `/creatives`).

| Метод | Путь | Тело | Назначение |
|---|---|---|---|
| GET | `/api/me` | — | текущий юзер из `X-User-Id`/`X-User-Email` |
| POST | `/api/tasks` | `{product, goal, audience}` | создать задачу → `{task_uid}` |
| GET | `/api/tasks` | — | список задач юзера |
| GET | `/api/tasks/{uid}` | — | статус/результат задачи |
| GET | `/api/tasks/{uid}/pending` | — | перечитать payload паузы (рехидрация после реконнекта) |
| GET | `/api/tasks/{uid}/events` | — | SSE-прогресс |
| POST | `/api/tasks/{uid}/decision/text` | `{action: approve\|regenerate\|refine\|cancel, comment?}` | резюм паузы #1 |
| POST | `/api/tasks/{uid}/decision/image` | multipart (файл) или `{action: generate\|cancel}` | резюм паузы #2 |
| GET | `/results/{uid}/...` | — | статика: PNG-форматы + ZIP |

### SSE-события
Базовые из App1 (`queued`, `start`, `step`, `done`, `error`, `_eof`) + App3:
- `awaiting_input` `{phase: text_approve|image_upload, candidate?|image_prompt?, can_generate, upload_window_sec}`
- `resumed` `{phase}`
- `cancelled` `{reason: user|timeout}`

## Egress-хосты (для allowlist платформы)
- `foundation-models.api.cloud.ru` — Cloud.ru FM (LLM)
- Phygital — НЕ напрямую: hero-генерация делегируется в App1 (loopback `127.0.0.1:8011/internal/hero`), своих Phygital-egress-хостов у App3 нет.
- Redis — НЕ используется (чекпойнтер на SQLite).

## Фазы
1. **Скелет** — `app/` пакет, auth по заголовку, БД, `/api/me`, `/results`. ✅
2. **Порт графа** в orchestrator (`app/services/creatives.py`), `POST /api/tasks` → `awaiting_text`. ✅
3. **Текстовый HITL** по SSE + REST (approve/regenerate/refine/cancel + `/pending`). ✅
4. **Картиночный HITL**: загрузка из браузера → ZIP в `results/<uid>/`. ✅
5. **Веб-генерация hero** — pluggable `HeroGenerator` (Null по умолчанию; `App1HeroGenerator` — HTTP-адаптер к `/internal/hero` App1, активируется `HERO_GEN_BACKEND=app1`). ✅
6. **Resume-after-restart** (reconcile), 24h-таймаут картинки, retention, `app3.service`, `.env`. ✅

Весь код App3 написан и покрыт unit-тестами (181 passed) на фейковых графе/генераторе — без живого Cloud.ru FM/Phygital.

## Деплой
- `deploy/app3.service` — systemd-юнит (порт 8013, single-worker, `WorkingDirectory=/opt/app3`, без Redis).
- `deploy/.env.app3.example` — шаблон env (скопировать в `.env` на ВМ).
- Зависимости веб-слоя: `pip install` группы `[web]` (fastapi/uvicorn/sqlalchemy/aiosqlite/sse-starlette/python-multipart) поверх ядра.

## Осталось (требует участия/координации, НЕ автономно)
1. **Эндпоинт App1 `/internal/hero`.** App1 заводит служебный loopback-эндпоинт и отписывает в COORDINATION; после этого App3 ставит `HERO_GEN_BACKEND=app1`. До этого — режим ручной загрузки hero (`NullHeroGenerator`, generate→501).
2. **Шлюз** (чат-платформы): добавить `/creatives → 127.0.0.1:8013` в `gateway/proxy.py`, ссылку в навигацию, завести `app3.service`.
3. **Страница `creatives.html`** — владелец чат-шлюз; App3 отдаёт только API-контракт (см. выше).
4. **Egress-allowlist** — передать список хостов платформе (см. секцию Egress).
5. **Боевой прогон** на ВМ с реальным Cloud.ru FM + App1 hero.

## Что переиспользуется / выбрасывается
- **as-is:** `graph/`, `infra/`, `llm/`, `config/templates.json`, `agents/` (нужна параметризация хардкода `/data/...`).
- **порт без TG:** `bot/graph_runner.py` → `app/services/creatives.py`.
- **из App1 verbatim:** `auth/deps.py`, `db/`, `routes_stream.py`, `TaskManager`, `retention.py`, EventBus.
- **выбрасывается:** весь `bot/*` (PTB, wizard, b2b-канал генерации).

## Риски
- Чекпойнтер на SQLite (`AsyncSqliteSaver`) — без Redis (как App1/App2). Пины: `langgraph-checkpoint-sqlite<3`, `aiosqlite<0.21` (в 0.21 убрали `Connection.is_alive`, который зовёт saver.setup()).
- EventBus in-memory → App3 запускать **single-worker**; истина при реконнекте — `/pending` + `GET /tasks/{uid}`.
- per-segment семафор меняет учёт App1 → гейт «открытых сессий/юзера» на счётчике в БД.
- краш mid-segment → v1: `running` фейлим на старте, `awaiting_*` оставляем resumable.

## Вне scope v1
Спекулятивный prefetch hero; `/banner` (архив); A/B-вариант (recipe сохраняем); multi-worker;
сама `creatives.html` (владелец — чат-шлюз).
