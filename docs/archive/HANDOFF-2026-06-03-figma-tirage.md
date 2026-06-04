# HANDOFF — Figma Layout Multiplication

**Project:** Автоматическое тиражирование макетов в Figma через MCP с
data-merge паттерном (мастер-фрейм + CSV → N инстансов).
**Owner:** Глеб Мельников · Cloud.ru in-house motion/video design team
**Status:** Ready for implementation
**Last updated:** 2026-06-03

---

## 0. TL;DR for the implementing agent

Прочитай этот файл целиком до начала работы. Затем выполни блок
**§9 Agent Bootstrap Commands** — это даст контекст исходного набора скиллов,
проверит доступ к Cloud.ru API и Figma MCP. После этого реализуй проект по
плану из **§5**, начиная с этапа 0 (POC).

Перед запуском Phase 1 — обязательно уточнить с владельцем актуальный паттерн
тиражирования (см. §1). MVP делаем под data merge, но архитектура должна быть
расширяемой на variant generation и asset multiplication.

---

## 1. Mission

Автоматизировать **тиражирование макетов в Figma**: на входе один или
несколько мастер-фреймов + источник данных (CSV/JSON/Google Sheets) — на выходе
N инстансов с подменёнными переменными, готовых к публикации/экспорту.

**Три паттерна, MVP под первый:**

1. **Data merge** (MVP) — мастер + CSV → N инстансов с подменёнными переменными. Кейсы: сертификаты по списку имён, карточки товаров по прайсу, локализация под N языков.
2. **Variant generation** (Phase 4) — один макет → N стилевых вариаций по правилам бренда.
3. **Asset multiplication** (Phase 4) — один шаблон → N форматов под разные площадки (16:9, 9:16, 1:1, og:image) с recompose под safe-area.

Источник методологии — vendor-neutral Agent Skills, реимплементация
OpenAI Codex плагинов Creative Production и Product Design в виде SKILL.md.
Релевантные для тиражирования: `creative-shot`, `creative-ads-explorer`,
`creative-offer`, `creative-positioning`, `creative-polish`.

---

## 2. Source materials

| Что | Где | Зачем |
|---|---|---|
| Codex-skills-alternative | https://github.com/DKeken/codex-skills-alternative | 19 vendor-neutral SKILL.md, методология тиражирования |
| Cloud.ru FM — главная | https://cloud.ru/docs/foundation-models/ug/index | Общая документация |
| Cloud.ru FM — API ref | https://cloud.ru/docs/foundation-models/ug/topics/api-ref | OpenAI-совместимый API |
| Cloud.ru FM — модели | https://cloud.ru/docs/foundation-models/ug/topics/overview__available__models | Актуальный каталог |
| Cloud.ru FM — Quickstart | https://cloud.ru/docs/foundation-models/ug/topics/quickstart | Аутентификация |
| Cloud.ru FM — Function calling | https://cloud.ru/docs/foundation-models/ug/topics/api-ref__function-calling | Tools API для MCP-обвязки |
| Cloud.ru FM — Structured output | https://cloud.ru/docs/foundation-models/ug/topics/api-ref__structured-output | JSON Schema responses |
| Figma MCP server | https://mcp.figma.com/mcp | Официальный MCP endpoint |
| Figma MCP docs | https://help.figma.com/hc/en-us/articles/32132100833559 | Capabilities и authentication |
| Figma REST API | https://www.figma.com/developers/api | Fallback для операций вне MCP |
| Figma Variables API | https://help.figma.com/hc/en-us/articles/15145852043927 | Concept reference для data binding |
| LangGraph docs | https://langchain-ai.github.io/langgraph/ | StateGraph, Send API, checkpointing |
| LangGraph + Redis | https://github.com/redis-developer/langgraph-redis | `RedisSaver` для больших тиражей |
| MCP spec | https://modelcontextprotocol.io/specification | Базовая теория |

---

## 3. Tech stack

**Required:**

- Python 3.11+
- `openai>=1.40` (с `base_url` = Cloud.ru)
- `langgraph>=0.2` + `langgraph-checkpoint-redis>=0.1`
- `mcp>=1.0` (Python SDK для MCP-клиента)
- `pandas>=2.0` (CSV / Excel input)
- `pydantic>=2.6`
- `structlog>=24.1`
- `httpx>=0.27` (Figma REST fallback)

**Optional:**

- `python-telegram-bot>=20.7` — если нужен TG-интерфейс (переиспользовать стек проекта 1)
- `fastapi>=0.110` — если нужна web-форма для команды
- `playwright>=1.40` — для финального скриншот-смоук-теста
- `google-api-python-client` — если источник Google Sheets, а не CSV

---

## 4. Architecture

```
[Input]                                [Master in Figma]
CSV/JSON/Sheets                        figma.com/file/<key>/<frame_id>
       │                                       │
       └──────────────┬────────────────────────┘
                      ▼
            [orchestrator.py — LangGraph StateGraph]
                      │
                      ▼
   ┌─────────────────────────────────────────────┐
   │ Node: validate_inputs                       │
   │   - frame exists, has variables             │
   │   - columns match variable schema           │
   ├─────────────────────────────────────────────┤
   │ Node: read_master  (one-shot)               │
   │   GLM-5.1 → Figma MCP:                      │
   │     get_design_context(frame_id)            │
   │     get_variable_defs(frame_id)             │
   │     search_design_system (if components)    │
   ├─────────────────────────────────────────────┤
   │ Node: build_mapping  (one-shot)             │
   │   GLM-5.1: data_column → frame_target       │
   │   (variable / text_node / instance_swap)    │
   ├─────────────────────────────────────────────┤
   │ Node: process_row  ← Send API, parallel=5   │
   │   For each CSV row:                         │
   │     GLM-5.1 → Figma MCP:                    │
   │       use_figma(duplicate_frame)            │
   │       for each (col, target) in mapping:    │
   │         set text / set var / swap variant   │
   │       upload_assets if URL in row           │
   ├─────────────────────────────────────────────┤
   │ Node: verify_row  ← Send API, parallel=3    │
   │   Figma MCP: get_screenshot(new_frame_id)   │
   │   Kimi K2.6 (vision): verdict JSON          │
   │     - overflow / empty / broken / OK        │
   ├─────────────────────────────────────────────┤
   │ Node: collect_results                       │
   │   Successful + Failed CSVs                  │
   │   Optional: export PNG/PDF per frame        │
   └─────────────────────────────────────────────┘
                      │
                      ▼
            /sessions/<uuid>/result.json
            (links to Figma page + per-row status)
```

**LangGraph State (Pydantic):**

```python
class TirageState(BaseModel):
    session_id: str
    master_url: str
    file_key: str
    master_frame_id: str
    data_source: Path                          # CSV/JSON/Sheets-cached
    rows: list[dict] = []
    master_structure: dict | None = None
    variable_defs: list[dict] = []
    mapping: dict[str, str] = {}              # column -> target_path
    results: list[RowResult] = []              # success + failed
    failed_rows_csv: Path | None = None
    cost_tokens: dict[str, int] = {}
```

**Cloud.ru models по nodes:**

| Node | Model | Why |
|---|---|---|
| `validate_inputs` | `deepseek-ai/DeepSeek-V4-Flash` | Дешёвая валидация схемы |
| `read_master`, `build_mapping`, `process_row` | `zai-org/GLM-5.1` | Native MCP, function calling, агентные задачи |
| `verify_row` | `moonshotai/Kimi-K2.6` | Vision: screenshot → verdict |

---

## 5. Implementation plan

**Phase 0 — POC без LangGraph (1-2 days).**
Один мастер-фрейм с 2-3 переменными (имя, должность, фото), CSV из 5 строк,
ручной запуск Python-скрипта. GLM-5.1 дергает Figma MCP. Sequential
`for row in df`, никаких чекпоинтов. Цель — убедиться, что stack
end-to-end работает: Cloud.ru → MCP → Figma → результат на canvas.
DoD: 5 инстансов созданы в Figma, каждый содержит правильно подменённые
значения. Никаких ошибок MCP authentication.

**Phase 1 — LangGraph orchestrator + extended patterns (3-4 days).**
- Перевести pipeline в `StateGraph` с nodes из §4
- Поддержка component variants (instance swap)
- Поддержка сложных компонентов с nested overrides
- Поддержка локальных картинок через `upload_assets`
- `Send` API для параллелизма `process_row` (limit=5 из-за rate limit Figma)
- `RedisSaver` для чекпоинтов
DoD: тираж 50 строк проходит за <5 минут; убийство процесса на 30-й строке
с последующим рестартом → продолжает с 31-й.

**Phase 2 — Vision verification (2 days).**
Node `verify_row` после `process_row`: screenshot из Figma MCP → Kimi K2.6 →
verdict JSON `{status: OK|OVERFLOW|EMPTY|BROKEN_IMAGE, notes: ...}`.
Failed rows пишутся в отдельный CSV для ручной доводки. Метрика recall:
≥80% реальных поломок ловится автоматически.
DoD: на 50 строках с заранее введёнными поломками (3 overflow, 2 empty,
1 broken image) verifier ловит ≥5 из 6.

**Phase 3 — UX wrapper (2-3 days).**
На выбор владельца (уточнить):
- **CLI** — `python tirage.py --master <url> --data <csv> --out <figma-page>`. Самый простой.
- **TG-бот** — переиспользовать стек проекта 1. Команда `/tirage`, файл CSV или ссылка на Google Sheets.
- **Web-форма** — FastAPI + минимальный HTML, для команды Cloud.ru.
DoD: один из трёх вариантов работает end-to-end, есть README с инструкцией.

**Phase 4 — Extending to patterns #2 and #3 (5-7 days).**
- **Variant generation:** node `generate_variants` берёт один мастер + правила бренда (палитры, типографики) → создаёт N стилевых вариантов
- **Asset multiplication:** node `multiplex_aspects` берёт мастер 16:9 → recompose в 9:16, 1:1, og:image с учётом safe-area через MCP `use_figma`
DoD: на одном мастере отрабатывают все три режима под флагом `--pattern`.

---

## 6. Known risks & caveats

| Risk | Mitigation |
|---|---|
| Figma MCP capabilities ещё развиваются | Перед Phase 1 — fetch актуальную доку, проверить наличие всех нужных операций |
| Variables vs Component Properties vs Instance Overrides — три механизма | На POC стандартизировать один. По умолчанию — Variables (новейший, гибкий) |
| Rate limits Figma API | `Send` API parallel=5; backoff на 429; для тиражей >500 строк — batching |
| Сложные градиенты, эффекты, прототипы interactivity | Не покрываются MCP, остаются на ручную доводку. Документировать в README |
| MCP-клиент крутится на стороне worker'а | Перехват tool_call от LLM → запрос к Figma MCP → результат обратно в messages-блок |
| Figma Personal Access Token | Хранить только в `.env`, не коммитить, ротация раз в квартал |
| Стоимость Kimi vision verifier | На 1000 строк ~ удорожание в 3-5×. Сделать flag `--no-verify` для быстрого прогона |
| Промпты под Claude/ChatGPT в исходном SKILL.md | Адаптировать под GLM-5.1 стиль (нумерованные шаги, structured output) |

---

## 7. Acceptance criteria

- [ ] CSV из 50 строк + master frame URL → 50 инстансов в Figma за <5 минут
- [ ] Vision-verifier ловит ≥80% поломок (recall) на тестовом наборе с известными ошибками
- [ ] Resume-from-middle: kill worker на 30-й строке → рестарт → продолжает с 31-й
- [ ] Отчёт в виде JSON: успешные / failed / стоимость в рублях / время каждого этапа
- [ ] UX-обёртка из Phase 3 работает: либо CLI, либо TG, либо web — отдаёт ссылку на Figma-страницу с результатом
- [ ] README с инструкцией: получить Figma PAT, подготовить мастер-фрейм, формат CSV
- [ ] Phase 4 (variants + multiplication) под флагами командной строки, без переписывания core'а

---

## 8. Repository layout (target)

```
figma-layout-multiplication/
├── HANDOFF.md                       ← этот файл
├── README.md
├── pyproject.toml
├── docker-compose.yml               ← redis + worker
├── .env.example
├── src/
│   ├── __init__.py
│   ├── settings.py
│   ├── cloudru_client.py            ← общий с проектом #1, можно вынести в shared lib
│   ├── figma_mcp_client.py          ← Python MCP-клиент для Figma
│   ├── orchestrator.py              ← LangGraph StateGraph
│   ├── state.py                     ← Pydantic TirageState
│   ├── nodes/
│   │   ├── validate.py
│   │   ├── read_master.py
│   │   ├── build_mapping.py
│   │   ├── process_row.py
│   │   ├── verify_row.py
│   │   └── collect.py
│   ├── patterns/
│   │   ├── data_merge.py            ← Phase 0-3
│   │   ├── variant_gen.py           ← Phase 4
│   │   └── multiplex.py             ← Phase 4
│   ├── cli.py                       ← argparse entry point
│   └── bot/                         ← опционально, если выбран TG
├── agents/                           ← .md промпты под GLM/Kimi
│   ├── 01-read-master.md
│   ├── 02-build-mapping.md
│   ├── 03-process-row.md
│   └── 04-verify-row.md
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
        ├── master_frames/           ← примеры мастеров с известными переменными
        └── test_data/               ← CSV с known-good и known-broken строками
```

---

## 9. Agent bootstrap commands

```bash
# A. Studying source skills (codex-skills-alternative)
mkdir -p /tmp/source-skills && cd /tmp/source-skills
git clone https://github.com/DKeken/codex-skills-alternative
cd codex-skills-alternative
cat README.md
cat skills/creative-shot/SKILL.md             # multi-angle / multi-variant pattern
cat skills/creative-ads-explorer/SKILL.md     # angle × format matrix — близко к тиражированию
cat skills/creative-offer/SKILL.md            # merchandising в hero визуалы
cat skills/creative-positioning/SKILL.md      # формализация бренд-правил

# B. Verifying Cloud.ru access
echo $CLOUD_RU_API_KEY
curl -s https://foundation-models.api.cloud.ru/v1/models \
     -H "Authorization: Bearer $CLOUD_RU_API_KEY" | jq

# C. Smoke-test GLM-5.1 with function calling
curl -s https://foundation-models.api.cloud.ru/v1/chat/completions \
     -H "Authorization: Bearer $CLOUD_RU_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "zai-org/GLM-5.1",
       "messages": [{"role": "user", "content": "What tools do you have?"}],
       "tools": [{
         "type": "function",
         "function": {
           "name": "get_weather",
           "description": "Get weather for a city",
           "parameters": {
             "type": "object",
             "properties": {"city": {"type": "string"}}
           }
         }
       }]
     }' | jq

# D. Verifying Figma access
echo $FIGMA_PAT                                # Personal Access Token
curl -s "https://api.figma.com/v1/me" \
     -H "X-Figma-Token: $FIGMA_PAT" | jq

# E. Studying Figma MCP capabilities
# (используй web_fetch tool агента)
# https://help.figma.com/hc/en-us/articles/32132100833559
# https://mcp.figma.com/mcp (попробовать подключиться MCP-клиентом)

# F. Reading LangGraph + Send API before Phase 1
# https://langchain-ai.github.io/langgraph/concepts/low_level/#send
# https://langchain-ai.github.io/langgraph/how-tos/map-reduce/

# G. Preparing test fixtures
# 1. Create simple master frame in Figma:
#    - Frame "MasterCard" with vars: $name (text), $title (text), $photo (image)
# 2. Get file_key and frame_id from URL
# 3. Create tests/fixtures/test_data/sample.csv with 5 rows
# 4. Save Figma file URL into .env as FIGMA_TEST_MASTER_URL
```

---

## 10. Working agreements with the implementing agent

1. **Никаких моков Figma MCP.** Если MCP-сервер недоступен — остановиться и зовать владельца. Mock-режим допустим только для unit-тестов под `tests/unit/`.
2. **Все артефакты сессии под UUID.** `/sessions/<uuid>/{input.csv,mapping.json,results.json,failed.csv,trace.jsonl,screenshots/}`.
3. **JSON-logs всегда.** `structlog` с context binding per session_id и row_index.
4. **Не реализовывать Phase 4 раньше Phase 3.** Соблазн "сразу сделать гибко" — частая ошибка, итог нерабочий core.
5. **`Send` API не использовать в Phase 0.** Sequential на POC. Параллелизм добавлять на Phase 1, когда уже понятны rate limits.
6. **Адаптировать промпты под GLM-5.1.** Тот же подход, что в проекте #1: нумерованные шаги, structured output через JSON Schema, минимум XML-тегов.
7. **Commits атомарные, по Phase.** Conventional commits: `feat(phase-1): langgraph orchestrator`, `fix(phase-0): figma mcp auth`.
8. **Не вводить новые библиотеки без явного запроса.** Стек из §3 — закрытый список.

---

## 11. Hand-off contact

Глеб Мельников — Telegram: уточнить у владельца. Часовой пояс UTC+3.
Ожидаемое время ответа: рабочие дни, ~2-4 часа.

**Перед Phase 1 уточнить:**
- Целевой паттерн тиражирования (data merge / variant gen / multiplication) — MVP
- Источник данных в проде (CSV / JSON / Google Sheets)
- UX-обёртка (CLI / TG / web)
- Целевой Figma workspace и формат мастер-фреймов
