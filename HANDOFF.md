# Resize_bot — HANDOFF

> Source of truth for the project. Replaces `docs/archive/HANDOFF-2026-06-03-figma-tirage.md` (original data-merge scope that was reframed during brainstorming on 2026-06-04).

## 1. Что это

Telegram-бот для **audience-driven генерации рекламных креативов с мультиформатным выводом**. Маркетолог в TG проходит 3-шаговый wizard (продукт → goal → ЦА; канал и форматы НЕ спрашиваются — канал извлекается parse_brief'ом из текста ЦА с дефолтом `tg_post`, форматы прибиты к whitelist'у manifest'а) → LangGraph агент на моделях Cloud.ru Foundation Models генерирует кандидатов рекламного сообщения → валидирует в persona-as-TA agentic loop → линейный HITL approve текста → LLM пишет EN prompt для hero-картинки → **юзер сам прогоняет prompt в своём image-генераторе (MJ / DALL-E / SDXL / Nano Banana) и присылает PNG в чат** → локальный PIL композер накладывает hero + slogan + CTA в N layered шаблонов из `config/templates.json` → ZIP → отправка в TG + кнопка `[Сделать вариант B]` для A/B-тестa.

**Объёмы:** 10/день MVP → 100/день целевой.

**Статус (2026-06-07):** **M3.4 done** — hero-картинка делегируется в @Cloud_Phygital_bot через **bot-to-bot Telegram API 10.0** (Render 3:4 @2K на Gemini+Nano Banana). Юзер видит только «Запрашиваю hero…» и получает готовый ZIP — никаких копипастов промпта в чужие генераторы. При любой ошибке/таймауте — fallback на старый HITL upload-флоу. Поведение управляется флагом `USE_PHYGITAL_RENDER`. Если он `false`, работает M3.3-флоу: юзер сам генерит hero и шлёт в чат.

## 2. Стек

- Python 3.11+ (uv-managed; локальный pytest на 3.10 — без stdlib 3.11-only)
- `python-telegram-bot[ext]==22.x` async
- `langgraph` + `langgraph-checkpoint-redis` (AsyncRedisSaver)
- `openai` (Cloud.ru FM OpenAI-compatible endpoint)
- `pydantic>=2.9` (discriminated unions для layer schema)
- `structlog` (JSON logs)
- `httpx[http2]` + `truststore` (для Cloud.ru MITM cert)
- `pillow` — основной композер (PIL canvas + layered templates, fonts SBSansDisplay)
- Redis Stack 7.4
- Docker Compose

## 3. Архитектура

### LangGraph nodes (async)

1. `parse_brief` — DeepSeek-V4-Pro (long-context). Wizard text → `AdBrief` Pydantic. `formats` пиннится whitelist'ом slug'ов из manifest'а.
2. `derive_persona` — GLM-5.1 thinking-OFF. Описание ЦА → 1-3 дискретные `Persona`.
3. `generate_message_candidates` — GLM-5.1 thinking-OFF. N=3-5 кандидатов (slogan/body/CTA). Soft word-bands (slogan 3-6, cta 1), retry-with-feedback на schema-fail. Если есть `prior_variant` — anti-bias по hook angle.
4. `evaluate_as_persona_loop` — GLM-5.1 thinking-ON. Для каждого candidate × persona — Structured Output verdict (resonance/clarity/action_intent 0-10 + free-form). Aggregate → revise (≤2 итераций). Симуляция в роли ЦА, не критик. Для B variant — приоритет ранкинга на persona[1].
5. `hitl_text_approve` — `interrupt()`, inline `[OK, делать картинку]/[Перегенерить]/[Доработать]/[Отменить]`.
6. `route_image_style` — детерминированный routing по brief (photo / render / isometric).
7. `generate_image_prompt` — GLM-5.1 thinking-OFF, temp=0.5. Outputs EN-paragraph (40-90 слов) под image_style. Soft validators: word-count band, cyrillic guard, "no text/no letters" required phrase. Юзеру отдаётся в TG как markdown-code-block для копи-паста.
8. `hitl_image_upload` — `interrupt()`. Двойная диспетчеризация в `bot/graph_runner.py:_handle_terminal_or_interrupt`:
   - **`USE_PHYGITAL_RENDER=true` (M3.4 default):** `_request_phygital_render` шлёт `@b2b render 3:4 k2 corr=<8hex>\n\n<EN prompt>` на `@{PHYGITAL_BOT_USERNAME}`. Ждёт `reply_photo` с caption `@b2b OK corr=<id>` (`PHYGITAL_REQUEST_TIMEOUT_S`, дефолт 1200с / 20 мин). На успех — сохраняет bytes в `/data/heroes/<thread>_<ts>_b2b.jpg` и резюмит граф как при ручной загрузке. На `@b2b ERROR corr=<id> reason=<code>`, таймаут, send-failure — fallback в HITL upload (юзер шлёт hero руками). Pending Future-ы лежат в `app.bot_data[B2B_PENDING_KEY]`, ключ — corr id.
   - **`USE_PHYGITAL_RENDER=false`:** старый M3.3-флоу — `_render_image_upload` показывает EN prompt в чате, юзер генерит сам, шлёт PHOTO или Document с `image/*` MIME (latest-wins, status flip → "running" перед resume). 24-часовой timeout → cancel + TG msg.
   - Resume contract одинаков для обоих путей: `{action: upload, local_path, style?, prompt?}` / `{action: cancel}` / `{action: timeout}`.
9. `fill_templates_per_format` — локальный PIL композер. Для каждого slug из `brief.formats` зовёт `infra.composer.compose(template, hero, slogan, cta, age_rating)` → PNG. Один глобальный `winner.slogan`, авто-shrink текста при overflow.
10. `render_all` — собирает PNG'и в `state.renders` (без удалённого rendering — композер уже отдал bytes).
11. `zip_and_send` — ZIP + summary + `[Сделать вариант B (A/B)]`.

### Modal split

| Модель | Роль |
|---|---|
| `deepseek-ai/DeepSeek-V4-Pro` | parse_brief, classifier, long-context RAG |
| `zai-org/GLM-5.1` | message-gen, persona-loop (thinking-ON в critic-проходах) |
| `moonshotai/Kimi-K2.6` | visual verifier рендеров (P2, опционально) |

### Runtime

- **async LangGraph + PTB** в одном Python процессе (без Celery — 10-100/день не требует worker isolation).
- **2 Docker сервиса:** `bot` + `redis` (Redis Stack 7.4).
- **Cloud.ru FM через openai SDK.** Никаких MCP-клиентов, никаких embedded браузеров — M3.2-стек выкинут.
- **structlog JSON** → stdout + RotatingFileHandler на `/data/traces/{session_id}.jsonl`. LangFuse/LangSmith добавлять по факту необходимости.
- **Whitelist** — env-var `WHITELIST_USER_IDS="123,456"`, единственный админ.
- **TTL janitor** чистит `/data/heroes`, `/data/renders`, `/data/zips` старше `ARTIFACT_TTL_HOURS` (24h по умолчанию). `/data/traces` не трогает.

### Template anatomy (PIL composer)

Полная спека — `docs/template_spec.md`. Кратко:

- `config/templates.json` — manifest со списком templates по slug'ам. Каждый template = canvas (width/height/background_color) + ordered `layers`.
- Три типа layer (Pydantic discriminated union в `infra/template_manifest.py`):
  - `image` — статический PNG (brand-area-line на верхушке каждого баннера).
  - `hero` — user-uploaded PNG, `fit: cover|contain`. Ровно один на template.
  - `text` — slot `slogan | cta | age_rating`, auto-shrink `font_size_max → font_size_min`, опциональные `per_line_highlight` (cloud.ru lemon highlight) и flat `background`.
- Render: `infra/composer.py` сортирует layers по `z`, рисует на RGBA PIL canvas, сохраняет PNG.
- Fonts — `assets/fonts/SBSansDisplay-<Weight>.otf` (Light/Regular/Medium/Semibold/Bold).
- Brand-area strips — `assets/brand/brand_area_line_<W>x<H>_v1.png`.

### HITL & Drafts

- Линейный HITL: text approve → image approve → forms. Single-session per user.
- Cancel: hard stop. Draft сохраняется **только если** маркетолог дошёл до approve текста (TTL 7д в Redis). До text-approve — полное удаление.
- Без эмодзи во всём runtime UI (см. `feedback_no_emojis` в agent memory).

### A/B механика (вариант B)

State-поле `prior_variant: {slogan, hook_angle, brand_variant, persona_priority}`. Diversity по 3 осям, минимум 2 из 3 должны отличаться:

1. **Slogan axis** — anti-bias по hook angle (эмоциональный ↔ рациональный, прямая выгода ↔ social proof).
2. **Brand visual axis** — принудительно другой brand-вариант если brief позволяет.
3. **Persona-priority axis** — ранкинг по persona[1] вместо persona[0] (если >1 persona).

Тот же LangGraph граф, conditional блоки в промптах.

## 4. Repository layout

```
Resize_bot/
├─ HANDOFF.md                      # этот файл (source of truth)
├─ AGENTS.md                       # rules для агентов (Claude/Cursor)
├─ pyproject.toml                  # uv-managed
├─ Dockerfile
├─ docker-compose.yml              # bot + redis
├─ .env.example
├─ assets/
│  ├─ brand/                       # brand_area_line_<W>x<H>_v1.png
│  └─ fonts/                       # SBSansDisplay-*.otf
├─ config/
│  └─ templates.json               # manifest (M3.3, replaces figma_templates.json)
├─ docs/
│  ├─ template_spec.md             # M3.3 PIL composer spec
│  ├─ open_questions.md
│  └─ archive/                     # HANDOFF-* + M3.2_BROKEN-* + figma_template_spec-*
├─ bot/
│  ├─ app.py                       # entry, PTB init, TTL janitor task
│  ├─ wizard.py                    # ConversationHandler + format whitelist
│  ├─ graph_runner.py              # LangGraph driver, HITL bridge, hero upload handler
│  └─ sessions.py                  # status enum + Redis adapter
├─ llm/
│  └─ cloudru.py                   # model-aware thinking-toggle, retry-with-feedback
├─ graph/
│  ├─ state.py                     # AdBrief / Persona / MessageCandidate / ImagePromptOutput
│  ├─ builder.py                   # state graph wiring
│  └─ nodes/                       # parse_brief, derive_persona, generate_message_candidates,
│                                  #   evaluate_as_persona_loop, hitl_text_approve,
│                                  #   route_image_style, generate_image_prompt,
│                                  #   hitl_image_upload, fill_templates_per_format,
│                                  #   render_all, zip_and_send
├─ infra/
│  ├─ composer.py                  # PIL render engine
│  ├─ template_manifest.py         # Pydantic schema + loader for config/templates.json
│  ├─ ttl_janitor.py               # /data/heroes,renders,zips sweeper
│  └─ admin_alert.py
├─ prompts/                        # SKILL.md (markdown frontmatter)
│  ├─ creative_ads_explorer.md
│  ├─ persona_eval.md
│  ├─ parse_brief.md
│  └─ generate_image_prompt.md     # EN hero-prompt writer (M3.3)
├─ agents/                         # *.yaml — model+skill+schema configs
└─ tests/
   ├─ unit/
   └─ integration/
```

## 5. Milestones

### M0 — bootstrap

- pyproject + docker-compose (bot + redis) + ENV-схема
- `bot/app.py` PTB skeleton с whitelist + `/start` (заглушка wizard)
- `llm/cloudru.py` model-aware client + retry-with-feedback
- `tests/integration/test_smoke.py` — ping трёх моделей
- `prompts/` skeleton под 3 SKILL.md
- HANDOFF.md (этот файл) + archive старого

**DoD:** `docker compose up` → бот отвечает `/start` whitelisted user'у; `pytest tests/integration/test_smoke.py -v` зелёный против всех трёх моделей.

### M1 — LangGraph foundation + parse_brief + wizard

- `graph/state.py` (TypedDict + `AdBrief` Pydantic + `Persona` + `MessageCandidate`)
- `graph/nodes/parse_brief.py` (DeepSeek-V4-Pro)
- `bot/wizard.py` — пошаговый wizard через ConversationHandler
- AsyncRedisSaver checkpoints
- structlog JSON + RotatingFileHandler

**DoD:** end-to-end wizard → `AdBrief` сохранён в Redis, дальше пока stub.

### M2 — message gen + persona-loop

- `graph/nodes/derive_persona.py`
- `graph/nodes/generate_message_candidates.py`
- `graph/nodes/evaluate_as_persona_loop.py` (LangGraph Send API для fan-out, max_concurrency=3)
- `prompts/creative_ads_explorer.md` + `prompts/persona_eval.md` финализированы
- HITL text approve (`interrupt()` + ConversationHandler resume)
- A/B механика (B variant prior_variant injection)

**DoD:** реальный brief → winner кандидата → TG inline approve → `prior_variant` сохранён для возможного B.

### M3 — image gen + render (история итераций)

- **M3.0–3.1 (Phygital path, выкинут):** vendored Phygital client + Playwright SuperTokens auth → brand_t2i (Gemini Text + Nano Banana). Работало end-to-end, но session expiry болезненный + цепочка хрупкая.
- **M3.2 (Figma MCP write-flow, **тупик**):** попытка fan-out форматов через `use_figma` MCP (`createImageAsync` + batch text updates). Desktop MCP read-only, cloud MCP gated to whitelisted clients — третья сторона зайти не может. Артефакты: `docs/archive/M3.2_BROKEN-2026-06-05.md`, `docs/archive/figma_template_spec-2026-06-05-m3.2-broken.md`.
- **M3.3 (current, done):** **юзер сам рисует hero** в любом image-генераторе, бот пишет EN prompt + накладывает hero+text локальным PIL композером по manifest'у.
  - `agents/generate_image_prompt.yaml` + `prompts/generate_image_prompt.md`
  - `graph/nodes/generate_image_prompt.py`, `graph/nodes/hitl_image_upload.py`
  - `infra/template_manifest.py` + `infra/composer.py` + `config/templates.json` + `assets/`
  - `bot/graph_runner.py` — handler на PHOTO/Document.IMAGE, 24h timeout, latest-wins
  - `docs/template_spec.md` — спека композера
  - удалено: `infra/{figma_mcp,phygital_client,tunnel,http_server}.py`, `graph/nodes/{generate_image,hitl_image_approve}.py`, `phygital_vendor/`, `scripts/`
  - deps выкинуты: `playwright`, `loguru`, `langchain-mcp-adapters`, `mcp`

**DoD M3.3:** реальный brief → winner → EN prompt в чат → юзер шлёт PNG → PIL композер → ZIP с N форматами → TG. 76 unit+agent тестов зелёные.

- **M3.4 (current, done 2026-06-07):** **bot-to-bot Telegram-делегация hero-картинки в @Cloud_Phygital_bot** через Bot API 10.0 (8 мая 2026). Оба бота включают «Bot-to-Bot Communication Mode» в BotFather, наш слышит ответы соседнего через carve-out в `whitelist_gate` (user.is_bot + username == `PHYGITAL_BOT_USERNAME`).
  - **Wire-протокол:** request `@b2b render 3:4 k2 corr=<8hex>\n\n<EN prompt>` → success `reply_photo` с caption `@b2b OK corr=<id>` → error `@b2b ERROR corr=<id> reason=<short_code>`. `corr` — 4-байтовый hex, обязательно эхо в ответе.
  - **Реализация на нашей стороне:** `bot/graph_runner.py` — `_request_phygital_render` (отправка запроса, ожидание Future через `asyncio.wait_for`), `on_phygital_reply` (MessageHandler на group=-2, строго ДО `whitelist_gate`), `B2BError`, `B2B_PENDING_KEY` registry. `bot/config.py` — `use_phygital_render`/`phygital_bot_username`/`phygital_request_timeout_s`. `bot/app.py` — `whitelist_gate` carve-out + глобальный `cmd_cancel` (поднят на app-level, чтобы работал из любого состояния — раньше был только fallback внутри wizard ConversationHandler).
  - **Реализация на стороне Phygital-bot:** `bot/b2b.py` (b2b_handler с regex-парсером, semaphore-ограниченный пул, `run_brand_text2img(variant="render", model_name="v3_1", ratio="3:4", resolution="k2")`, retry-loop для safety scrubbing уже встроен в workflow). Регистрация на `group=-1` с `ApplicationHandlerStop`, чтобы не утечь в menu/conv handlers. Whitelist через `B2B_BOT_WHITELIST` env-var.
  - **Aspect ratio выбор:** 3:4 @ 2K — компромисс между вертикальными баннерами (240×400 ≈ 0.6, 300×500 ≈ 0.6) и горизонтальным 300×250 (≈ 1.2). Hero рисует abstract environment, обрезка по бокам не критична. Один hero на креатив, PIL потом ресайзит в 7 форматов.
  - **Структурные логи** (structlog) с corr/thread_id/elapsed_ms: `b2b_sent` / `b2b_ok` / `b2b_error` / `b2b_timeout` / `b2b_send_failed` / `b2b_save_failed` / `b2b_reply_unknown_corr` / `b2b_skip_empty_prompt`.
  - **Тесты:** `tests/unit/test_b2b_phygital.py` — 10 тестов на regex (OK/ERROR happy + corner cases) и handler-coroutine (happy path с Future-resolution, timeout, B2BError, empty prompt). Все 60 unit-тестов зелёные.
  - **Что НЕ сделано в M3.4 и не нужно:** ни REST, ни sidecar, ни MCP — всё через TG. Не сохраняем recipe на стороне Phygital — это служебный одноразовый канал.

**DoD M3.4:** floor — `/new` с включённым `USE_PHYGITAL_RENDER=true` → wizard → text approve → бот сам отправляет запрос соседнему боту → получает hero в течение 20 мин → накладывает → ZIP. Если что-то отвалится (timeout, ERROR, send-fail) — пользователь не теряет сессию, видит «Переключаюсь на ручную загрузку» и старый EN-prompt в чате. **Подтверждено в проде 2026-06-07.**

### M4 — A/B + drafts + admin

- `[Сделать вариант B]` callback + diversity-constraint в промптах
- Drafts Redis TTL 7д, `/drafts` команда
- `/cancel`, `/resume <session_id>`
- Admin counters в Redis (B vs A wins, cancel rate, avg time-to-approve)
- Re-login по 401 (Phygital expiry)

**DoD:** B variant отличается от A минимум по 2 осям, admin может посмотреть базовую статистику.

### M5 — synthetic eval + production hardening

- `tests/prompts_smoke.py` — 10 synthetic брифов как regression gate (schema_ok ≥95%)
- LangFuse interim (опционально, если structlog грязнеет)
- Кэш rendered форматов в Redis (если та же brief — переиспользуем)
- Прод-релиз pipeline

**DoD:** synthetic regression в CI; 5-10 живых задач от маркетологов прошли без интервенции инженера.

## 6. Empirical findings (унаследованы из Cloud.ru Slides Bot)

- **Multimodal только Kimi-K2.6.** GLM/DeepSeek → `400 not a multimodal model`.
- **Thinking-toggle per model:**
  - GLM-5.1: `extra_body={"chat_template_kwargs":{"enable_thinking":False}}`
  - Kimi-K2.6 (text): `extra_body={"thinking":{"type":"disabled"}}`
  - Kimi-K2.6 vision: toggle игнорируется, `max_tokens >= 2500`
- **`response_format: json_schema` не работает.** Plain prompt + Pydantic + 1 retry-with-feedback — стандарт.
- **GLM-5.1 trap:** default thinking на input ≥10K tokens → `NoneType` (сжигает бюджет в reasoning_content). В `parse_brief` фиксировать `thinking=False`.
- **20 RPS** на один API-ключ → `max_concurrency=3` в LangGraph config.

## 7. Open risks

1. **Hero quality dependence на юзера.** Если маркетолог пришлёт мутный PNG — композер всё равно ляпнет его на canvas. Контроль качества вне бота. Митигация: EN prompt в чате готов к копи-пасту в MJ/DALL-E/SDXL.
2. **24h timeout длинный.** Если юзер бросил сессию — она висит в Redis сутки. Митигация: TTL janitor чистит `/data/heroes`; `/cancel` доступен глобально.
3. **Manifest drift.** Добавили новый slug в `bot/wizard.py` и `prompts/parse_brief.md` whitelist, забыли в `config/templates.json` → KeyError в композере. Митигация: smoke-тест `tests/unit/test_composer.py::test_compose_real_template_smoke` параметризован slug'ами.
4. **SKILL.md alignment cost.** Пилот на `creative-ads-explorer`, экстраполируем.
5. **Persona-loop стоимость.** 100/день × 16 вызовов = 1600/день. ОК до 100/день, дальше пересмотреть N×M.
6. **Fonts/brand assets отсутствуют в git.** `assets/fonts/SBSansDisplay-*.otf` и `assets/brand/brand_area_line_*.png` — лицензионные, держим вне репо. При деплое — заносить вручную в volume.

## 8. Memory & references

- Agent memory: `C:\Users\Глеб\.claude\projects\C--Users------Documents-Resize-bot\memory\`
- Vault folder note: `2nd brain\01 Projects\Resize_bot\Resize_bot.md`
- Brainstorming session: `2nd brain\01 Projects\Resize_bot\Resize_bot — старт brainstorming 2026-06-04.md`
- Sister project (sаме stack reference): `2nd brain\01 Projects\Cloud.ru Slides Bot\`
- Brand prompts source: `C:\Users\Глеб\Documents\Phygital-bot\docs\SYSTEM_PROMPT_*.md`
- Skills upstream: `https://github.com/DKeken/codex-skills-alternative`
