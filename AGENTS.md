# AGENTS.md — Resize_bot agent design

Источник правды по агентам пайплайна: что у нас за агенты, какие у них контракты, какие hook'и стоят до и после LLM-вызова, где править поведение.

Парный артефакт для бренда — `docs/brand_voice_notes.md` (выжимка из брендбука).
Парный артефакт по шаблону Figma — `docs/figma_template_spec.md`.
Открытые вопросы — `docs/open_questions.md`.

---

## 1. Модель «агент» в нашем графе

Агент = одна LangGraph-нода, которая:

1. Принимает `GraphState` (TypedDict).
2. Прогоняет **pre-hook**-цепочку над релевантной частью state.
3. Делает LLM-вызов через `llm/cloudru.py:structured(...)` со своим skill-файлом из `prompts/`.
4. Прогоняет **post-hook**-цепочку над сырым LLM-выходом → собирает `list[Violation]`.
5. Если violations → один retry с подмешанным фидбэком в user-message (механизм `retry_with_feedback` уже есть в `cloudru.structured`).
6. Возвращает patch к state в виде dict (Pydantic `.model_dump()` — не сами объекты, иначе ломается Redis-чекпойнтер).

```
state ──pre-hooks──► LLM(skill, model_cfg) ──post-hooks──► retry? ──► state'
                            ▲
                            └── prompts/<agent>.md (v0.x.x semver)
```

Все три части (pre, LLM, post) живут в одной ноде `graph/nodes/<agent>.py`, но реализованы как **три явные секции**, не как простыня.

---

## 2. Каталог агентов (M2 — текущие, M3 — план)

Карточка каждого агента — `agents/<id>.yaml`. Реестр загружается при старте, нода тянет конфиг из карточки. Skill-prompt лежит отдельно в `prompts/<id>.md` и версионируется semver'ом в YAML frontmatter.

### M2 — actual

| id | skill | model | thinking | retry | post-hooks (планируем) |
|---|---|---|---|---|---|
| `parse_brief` | `prompts/parse_brief.md` v0.1.0 | DeepSeek-V4-Pro | n/a | 1 | pydantic; channel/goal in controlled vocab; product не пустой |
| `derive_persona` | `prompts/derive_persona.md` v0.1.0 | GLM-5.1 | off | 1 | pydantic; `1 ≤ len ≤ 3`; персоны не дубликаты |
| `generate_message_candidates` | `prompts/creative_ads_explorer.md` v0.2.0 | GLM-5.1 | off | 1 | pydantic; `3 ≤ len ≤ 5`; уникальные `hook_angle`; **brand-guards** (см. §4); длины slogan/body/cta; no emoji |
| `evaluate_as_persona_loop` | `prompts/persona_eval.md` v0.2.0 | GLM-5.1, Semaphore(3) | off | 1 на верификат | pydantic; scores в `[0,1]`; нет critic-фраз |
| `hitl_text_approve` | — (interrupt) | — | — | — | decision ∈ approve/regenerate/refine/cancel |

### M3.0 / M3.1 — actual (smoke + Phygital adapter; Figma MCP остаётся stub)

| id | skill | model | статус | назначение |
|---|---|---|---|---|
| `route_image_style` | `prompts/route_image_style.md` v0.1.0 | GLM-5.1 off | **REAL LLM** | классифицирует winner → photo / render / isometric. Возвращает `ImageStyleChoice {style, rationale}`. Фоллбек на `photo` при невалидном style. |
| `generate_image` | `phygital_vendor/workflows/brand_text2img.py` (vendored из Phygital-bot) | Gemini Pro 3.1 + Nano Banana v3_1 (через Phygital+) | **REAL (M3.1)** | звёт `run_brand_text2img(client, prompt=..., variant=image_style)`. Скачивает S3 URL в `/data/images/`. Prompt — только тема (product+audience_raw+tone_hints+refine), **без slogan/CTA/body** — это hero-визуал, текст накладывает макетный этап. Fallback на M3.0 PIL stub когда `PHYGITAL_ENABLED=false`, сессия не загружена или Phygital упал. |
| `hitl_image_approve` | — (interrupt) | — | **REAL** | OK / перегенерить / доработать комментарием / отменить. `regenerate`/`refine` бампают `image_revise_round`. Картинка идёт в TG как `send_photo`. |
| `fill_templates_per_format` | — (local PIL stub) | PIL composite | **STUB (M3.2)** | парсит `<channel>_<W>x<H>` slugs из `brief.formats`, делает hero+text composite в `/data/renders/`. Figma MCP — M3.2. |
| `render_all` | — (deterministic) | `zipfile.ZIP_DEFLATED` | **REAL** | пакует все PNG'и в один ZIP в `/data/zips/`, отдаёт через `send_document`. |

### M3 — planned next

| id | назначение | блокер |
|---|---|---|
| `fill_templates_per_format` (real) | Figma Make MCP `use_figma` — мап `{{slogan}}/{{body}}/{{cta}}` и `{{hero_image}}` в master frames | требует public URL для `hero_image` → infra/tunnel.py |
| `render_all` (real) | Figma REST `/v1/images/...` для финальных экспортов под фактические master-frames | требует Figma file_id и токен |

### M3.0 инфра-каркас + M3.1 Phygital adapter

- `infra/http_server.py` — stdlib `ThreadingHTTPServer` отдаёт `/data/{images,renders,zips}/` read-only. Запускается в `_post_init` (см. `bot/app.py`), порт из `HTTP_PORT` (default 8088).
- `infra/tunnel.py` — обёртка над `cloudflared`. Режимы: `disabled` (default — для M3.0 smoke не нужен), `quick` (`*.trycloudflare.com`), `named` (`TUNNEL_TOKEN`+`PUBLIC_BASE_URL`). Публичный URL кладётся в `app.bot_data["public_base_url"]`.
- `infra/phygital_client.py` — singleton-holder `PhygitalClient` (M3.1). Bootstrap'ит `phygital_vendor/` в `sys.path`, открывает HTTP-клиент при старте, закрывает при шатдауне. `get_client()` возвращает None если `PHYGITAL_ENABLED=false` или нет `session.json` — `generate_image` тогда падает на PIL-stub.
- `phygital_vendor/` — vendor-copy `{client,workflows,docs}` из `C:/Users/Глеб/Documents/Phygital-bot` (2026-06-04). Минимальный patch: `client/config.py::STORAGE_DIR` + `workflows/brand_docs.py::CACHE_FILE` читают `PHYGITAL_STORAGE_DIR` (по умолчанию `/data/phygital_storage`), чтобы сессия и кеш брендовых документов жили в docker-volume, а не в `/app`.
- Volume map в `docker-compose.yml`: `images`, `renders`, `zips`, `phygital_storage` (все named volumes, не bind).
- **Bootstrap session.json** (одноразово, после первого `up`): `docker cp C:/Users/Глеб/Documents/Phygital-bot/storage/session.json resize-bot:/data/phygital_storage/session.json`. SuperTokens refresh после этого сам поддерживает сессию.

---

## 3. Структура карточки агента (`agents/<id>.yaml`)

```yaml
id: generate_message_candidates
skill: prompts/creative_ads_explorer.md   # тот же файл, на котором живёт системный/юзер-промпт
model: zai-org/GLM-5.1
model_config:
  thinking: false        # для GLM с длинным input — обязательно (наследие Slides Bot)
  max_tokens: 4000
  temperature: 0.8
schema: graph.state.MessageCandidate   # Pydantic class, к которому Coerce
retry_with_feedback: 1
pre_hooks:
  - normalize_brief
  - ban_emoji_input
post_hooks:
  - pydantic_validate
  - len_between:    {min: 3, max: 5}
  - field_diversity: {field: hook_angle}
  - text_len:       {field: slogan, max: 60}
  - text_len:       {field: body,   max: 180}
  - text_len:       {field: cta,    max: 30}
  - ban_emoji
  - brand_stopwords_regex
  - brand_pleasewords_optional       # warn-only, не блокирует
metrics:
  - latency_ms
  - tokens_in
  - tokens_out
  - retries
  - violations_by_hook
```

Карточка — single source of truth. Нода читает её, не хардкодит цифры. Когда хочется поменять «жёсткий лимит 60 символов на слоган» — правишь YAML, не код.

---

## 4. Бренд-гварды (брейншторм по `Карта_дизайна_Cloud.ru_2.0.md`)

> ⚠️ **Caveat:** брендбук Cloud.ru 2.0 — преимущественно **визуальный**. Tone-of-voice для копирайта в нём не зафиксирован системно. Список ниже — это синтез из визуального брендбука + философии Dieter Rams / Nothing-distinction (которая там явно прописана). До утверждения с Глебом — это **proposed-уровень**, не final.

### 4.1 Что точно блокируем (hard-fail → retry с фидбэком)

**Регекс-стоп-слова (case-insensitive):**

```
# группа A — визуальный пафос, не свойственный Cloud.ru
\b(epic|cinematic|award.?winning|masterpiece|hyperdetail\w*|ultra.?realistic)\b
\b(neon|glassmorphism|cyberpunk|vaporwave|synthwave|hologram\w*)\b
\b(fractal\w*|neural.?mesh|brain.?network|swirling.?data)\b

# группа B — корпоративный канцелярит и замыленные buzzwords
\b(революцион\w+|инновацион\w+|прорывн\w+|потрясающ\w+|невероятн\w+)\b
\b(передов\w+ технолог\w+|облачн\w+ решен\w+ нового поколен\w+)\b
\b(цифров\w+ трансформац\w+|синерги\w+|комплиментарн\w+)\b
\b(экосистем\w+)\b    # допустимо только с конкретизацией — реализовать как warn, не error

# группа C — анти-Nothing (бренд явно дистанцируется)
\b(transparent housing|glyph.?interface|dot.?matrix|LED.?matrix)\b
\b(Nothing\s+(Phone|Ear|Buds))\b

# группа D — устаревшие референсы по дизайну
\b(vintage|retro|1960s|mid.?century|Braun.?style)\b
```

**Структурные хард-фейлы:**

- `slogan` > 60 символов / `body` > 180 / `cta` > 30 → fail (уже в `creative_ads_explorer.md`, выносим в YAML).
- любой emoji в любом поле → fail (правило уже в нашем глобальном CLAUDE — `feedback_no_emojis.md`).
- `!` в `slogan` → fail (calm tone). В `cta` — разрешён.
- `cta` короче 2 слов или > 5 слов → fail.

### 4.2 Что warn'им, но не блочим (LLM видит и может учесть на retry)

- Отсутствие хотя бы одного «бренд-маркера»: `спокойн|инженерн|чист|честн|надёжн|функциональн|просто|минималистичн` — warning.
- Прилагательное без существительного-носителя смысла (украшение) — LLM-judge, не regex.
- Generic claim без цифры (`быстрее`, `надёжнее`, `проще` без числа) — LLM-judge, warning.

### 4.3 LLM-judge гварды (для финального verdict-этапа, не для каждого retry)

Добавляются в `prompts/persona_eval.md` v0.3.0 как доп-критерии (если решим):

- **Tone:** «спокойно, уверенно, инженерно, без пафоса?» (1 предложение оценки + 0/1).
- **Honesty:** «каждое утверждение можно подкрепить цифрой/фактом?»
- **Minimalism:** «есть ли усилители-балласт (очень/супер/потрясающе)?»
- **Nothing-distinction:** только если в тексте упомянут визуал — не путаемся ли с Nothing.

### 4.4 Статус решений (2026-06-04)

- ✅ Список стоп-слов групп A/B/C/D — **утверждён в proposed-виде** (Глеб, 2026-06-04). Доработка идёт через правки этого файла → потом код.
- 🟡 Обращение «вы»/«ты», `?` в slogan'е, лимит 50 vs 60, обязательность цифры — пока **остаются как сейчас** (warn-only). Когда поймаем bad case в проде — донастраиваем через этот файл, не лезем в код.

**Контракт:** правки бренд-гвардов = правки §4 этого файла. Код в `graph/hooks/brand_stopwords.py` — отражение этого файла, не наоборот. Bump'аем `prompts/creative_ads_explorer.md` minor при изменениях.

---

## 5. Версионирование

- **Skill-файлы** (`prompts/*.md`) — semver в YAML-frontmatter (`version: 0.2.0`).
  - MAJOR — несовместимый формат выхода (например, добавили обязательное поле).
  - MINOR — новый guard, новый секция системы, расширение controlled vocab.
  - PATCH — переформулировка, опечатки, уточнения без поведенческих изменений.
- **Карточки агентов** (`agents/*.yaml`) — semver там же; bump'ается, когда меняется LLM-config, лимиты, состав hook'ов.
- **Changelog внутри skill-файла** — мини-секция `## Changelog` в конце с датой и одним bullet'ом per bump.
- **`tests/agents/golden/`** — золотые входы → ожидаемый shape выхода; при MAJOR-bump skill'а — фиксируется новый ожидаемый shape.

---

## 6. Где править что (рабочий процесс)

| Хочу поменять | Куда иду |
|---|---|
| Системный/юзер-промпт агента, формулировки, формат выхода | `prompts/<id>.md` + bump version |
| Модель / temperature / max_tokens / thinking | `agents/<id>.yaml` |
| Новое поле в выходе агента | `graph/state.py` (Pydantic) → `prompts/<id>.md` (описание поля) → `agents/<id>.yaml` (если новый guard) |
| Новый hook (например, новый стоп-слов список) | `graph/hooks/<hook_name>.py` + регистрация в `graph/hooks/__init__.py` + ссылка в YAML |
| Глобальный hook (no_emoji) на все агенты | `agents/_defaults.yaml` (если решим завести) |
| Roadmap / новые агенты | этот файл, секция §2 |
| Бренд-гварды / стоп-слова | этот файл, §4 (как источник) → `graph/hooks/brand_stopwords.py` (как реализация) |

---

## 7. Чего сейчас нет (TODO для M2.5 рефакторинга)

- [ ] Создать `agents/` директорию с YAML-карточками для 4 текущих агентов
- [ ] Создать `graph/hooks/` модуль с базовыми проверками: `ban_emoji`, `text_len`, `len_between`, `field_diversity`, `brand_stopwords_regex`
- [ ] Вынести жёсткие лимиты из текста промпта (`creative_ads_explorer.md` §3) в карточку — чтобы менялись в одном месте
- [ ] Логирование `event="hook_violation"` со всеми полями (agent, hook, payload)
- [ ] `tests/agents/` с фикстурой `mock_cloudru_structured` + golden inputs

---

## 8. Связанные документы

- `docs/figma_template_spec.md` — что подготовить в Figma для M3 шаблона
- `docs/open_questions.md` — нерешённые архитектурные вопросы (включая image hosting для Figma)
- `prompts/*.md` — собственно skill-файлы агентов
- `graph/state.py` — Pydantic-модели, общие для пайплайна
- `llm/cloudru.py` — единая точка LLM-вызовов и retry-with-feedback
