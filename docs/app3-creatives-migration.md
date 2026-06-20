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
- **Redis** — langgraph-чекпойнты (graph state, park/resume), ключ = `task_uid`.
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
- `app-server-azure.phygital.plus` — Phygital API (генерация hero, upload, download)
- `app.phygital.plus` — Phygital auth/refresh (SuperTokens)
- хост S3/CDN из `download_link` Phygital — уточнить по реальному ответу
- Redis — внутренняя зависимость (не egress; задекларировать как требование к ВМ)

## Фазы
1. **Скелет** — `app/` пакет, auth по заголовку, БД, `/api/me`, `/results`. ✅ ГОТОВО.
2. Порт графа в orchestrator (`app/services/creatives.py`), `POST /api/tasks` доезжает до `awaiting_text`.
3. Текстовый HITL по SSE + REST.
4. Картиночный HITL: загрузка из браузера → ZIP.
5. Веб-Phygital генерация hero (смена канала с Telegram b2b).
6. Resume-after-restart, таймаут, `app3.service`, egress.

## Что переиспользуется / выбрасывается
- **as-is:** `graph/`, `infra/`, `llm/`, `config/templates.json`, `agents/` (нужна параметризация хардкода `/data/...`).
- **порт без TG:** `bot/graph_runner.py` → `app/services/creatives.py`.
- **из App1 verbatim:** `auth/deps.py`, `db/`, `routes_stream.py`, `TaskManager`, `retention.py`, EventBus.
- **выбрасывается:** весь `bot/*` (PTB, wizard, b2b-канал генерации).

## Риски
- Redis-зависимость — отклонение от App1 (`After=redis.service`); альтернатива `AsyncSqliteSaver` (отложено).
- EventBus in-memory → App3 запускать **single-worker**; истина при реконнекте — `/pending` + `GET /tasks/{uid}`.
- per-segment семафор меняет учёт App1 → гейт «открытых сессий/юзера» на счётчике в БД.
- краш mid-segment → v1: `running` фейлим на старте, `awaiting_*` оставляем resumable.

## Вне scope v1
Спекулятивный prefetch hero; `/banner` (архив); A/B-вариант (recipe сохраняем); multi-worker;
сама `creatives.html` (владелец — чат-шлюз); миграция на `AsyncSqliteSaver`.
