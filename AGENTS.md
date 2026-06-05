# AGENTS.md — Resize_bot agent design

Источник правды по агентам пайплайна: что у нас за агенты, какие у них контракты, какие hook'и стоят до и после LLM-вызова, где править поведение.

Парный артефакт для бренда — `docs/brand_voice_notes.md` (выжимка из брендбука).
Парный артефакт по шаблону рендера — `docs/template_spec.md` (M3.3 PIL composer; M3.2 Figma-вариант — в `docs/archive/figma_template_spec-2026-06-05-m3.2-broken.md`).
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

## 2. Каталог агентов (M3.3 — current)

Карточка каждого агента — `agents/<id>.yaml`. Реестр загружается при старте, нода тянет конфиг из карточки. Skill-prompt лежит отдельно в `prompts/<id>.md` и версионируется semver'ом в YAML frontmatter.

### LLM-агенты

| id | skill | model | thinking | retry | post-hooks |
|---|---|---|---|---|---|
| `parse_brief` | `prompts/parse_brief.md` v0.1.0 | DeepSeek-V4-Pro | n/a | 1 | pydantic; channel/goal in controlled vocab; **`formats` пиннится whitelist'ом slug'ов из `config/templates.json`**; product не пустой |
| `derive_persona` | `prompts/derive_persona.md` v0.1.0 | GLM-5.1 | off | 1 | pydantic; `1 ≤ len ≤ 3`; персоны не дубликаты |
| `generate_message_candidates` | `prompts/creative_ads_explorer.md` v0.3.0 | GLM-5.1 | off | 1 | pydantic; `3 ≤ len ≤ 5`; уникальные `hook_angle`; brand-guards (§4); **soft word-bands** (slogan 3-6, cta 1) — warn-only, не блокирует |
| `evaluate_as_persona_loop` | `prompts/persona_eval.md` v0.2.0 | GLM-5.1, Semaphore(3) | off | 1 на верификат | pydantic; scores в `[0,1]`; нет critic-фраз |
| `route_image_style` | `prompts/route_image_style.md` v0.1.0 | GLM-5.1 | off | 1 | pydantic; `style ∈ {photo, render, isometric}`; фоллбек на `photo` при невалидном |
| `generate_image_prompt` | `prompts/generate_image_prompt.md` v0.1.0 | GLM-5.1 | off (temp=0.5, max_tokens=800) | 1 | warn-only validators: word count 40-90, no Cyrillic, contains "no text"/"no letters"; **outputs EN-paragraph для копипаста юзером в свой image-gen** |

### Детерминированные / HITL ноды

| id | назначение |
|---|---|
| `hitl_text_approve` | `interrupt()`. Decision ∈ approve/regenerate/refine/cancel. |
| `hitl_image_upload` | `interrupt()`. Ждёт PHOTO или Document с `image/*` MIME. Resume contract: `{action: upload, local_path}` / `{action: cancel}` / `{action: timeout}`. 24-часовой timeout (`asyncio.create_task`, хранится в `app.bot_data["_image_upload_timeouts"]`) → cancel + TG msg "Время истекло". Latest-wins: `bot/graph_runner.py:on_image_upload` флипает `session.status → running` перед resume, чтобы вторая фотка отбилась. |
| `fill_templates_per_format` | Локальный PIL композер. Для каждого slug из `brief.formats` зовёт `infra.composer.compose(template, hero=state.image, slogan=..., cta=..., age_rating=...)` → PNG в памяти. Manifest — `config/templates.json`, схема — `infra/template_manifest.py` (Pydantic discriminated union ImageLayer / HeroLayer / TextLayer). |
| `render_all` | Складывает PNG'и в `state.renders` (имя слот'а → bytes). Без удалённого rendering. |
| `zip_and_send` | `zipfile.ZIP_DEFLATED` → `/data/zips/<session_id>.zip` → `send_document`. |

### Что выкинуто в M3.3 (для архивной памяти)

- **M3.0–3.1 Phygital path** (vendored client + Playwright + SuperTokens session): хрупкая re-auth, vendor под чужой код. Выкинуто целиком вместе с `phygital_vendor/`.
- **M3.2 Figma MCP write-flow** (`use_figma` + `createImageAsync` + tunnel + presigned upload): тупик — Desktop MCP read-only, cloud MCP gated. См. `docs/archive/M3.2_BROKEN-2026-06-05.md`.
- Файлы: `infra/{figma_mcp,phygital_client,tunnel,http_server}.py`, `graph/nodes/{generate_image,hitl_image_approve}.py`, `scripts/`.
- Deps: `playwright`, `loguru`, `langchain-mcp-adapters`, `mcp`.

### Manifest contract (M3.3)

- `config/templates.json` — single source of truth для layouts. Manifest schema — `infra/template_manifest.py`.
- Slug whitelist: добавил новый slug → синхронно правишь (a) `config/templates.json`, (b) `bot/wizard.py` format whitelist, (c) `prompts/parse_brief.md` slug whitelist, (d) `tests/unit/test_composer.py::test_compose_real_template_smoke` parametrize.
- Fonts — `assets/fonts/SBSansDisplay-*.otf` (Light/Regular/Medium/Semibold/Bold).
- Brand-area strips — `assets/brand/brand_area_line_<W>x<H>_v1.png`.
- Полная спека композера — `docs/template_spec.md`.

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
  - word_band:      {field: slogan, min_words: 3, max_words: 6, severity: warn}
  - word_band:      {field: cta,    min_words: 1, max_words: 1, severity: warn}
  - text_len:       {field: body,   max: 180, severity: warn}
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

- любой emoji в любом поле → fail (правило уже в нашем глобальном CLAUDE — `feedback_no_emojis.md`).
- `!` в `slogan` → fail (calm tone). В `cta` — разрешён.

**Soft word-bands (warn-only, не fail):**

- `slogan` 3–6 слов (до 8 допустимо для мягкого target).
- `cta` ровно 1 слово.
- `body` ≤ 180 символов (черновое описание / референс, в финальный макет не идёт).

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

- `docs/template_spec.md` — спека M3.3 PIL композера (manifest, layers, fonts)
- `docs/open_questions.md` — нерешённые архитектурные вопросы
- `docs/archive/M3.2_BROKEN-2026-06-05.md` — почему Figma MCP write-flow дохлый (для археологии)
- `prompts/*.md` — собственно skill-файлы агентов
- `graph/state.py` — Pydantic-модели, общие для пайплайна
- `llm/cloudru.py` — единая точка LLM-вызовов и retry-with-feedback
