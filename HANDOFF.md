# Resize_bot — HANDOFF

> Source of truth for the project. Replaces `docs/archive/HANDOFF-2026-06-03-figma-tirage.md` (original data-merge scope that was reframed during brainstorming on 2026-06-04).

## 1. Что это

Telegram-бот для **audience-driven генерации рекламных креативов с мультиформатным выводом**. Маркетолог в TG проходит wizard (продукт → goal → ЦА → канал → форматы) → LangGraph агент на моделях Cloud.ru Foundation Models генерирует кандидатов рекламного сообщения → валидирует в persona-as-TA agentic loop → линейный HITL approve текста → генерация картинки через Phygital+ brand pipeline → HITL approve картинки → подмена placeholders `{{token}}` в N мастер-фреймах Figma → batch render через Figma REST API → ZIP → отправка в TG + кнопка `[Сделать вариант B]` для A/B-тестa.

**Объёмы:** 10/день MVP → 100/день целевой.

## 2. Стек

- Python 3.11+ (uv-managed)
- `python-telegram-bot[ext]==22.x` async
- `langgraph` + `langgraph-checkpoint-redis` (AsyncRedisSaver)
- `langchain-mcp-adapters` + `mcp` (для Figma Make MCP, streamable_http)
- `openai` (Cloud.ru FM OpenAI-compatible endpoint)
- `pydantic>=2.9`
- `structlog` (JSON logs)
- `playwright` (embedded SuperTokens browser для Phygital auth)
- `httpx[http2]` + `truststore` (для Cloud.ru MITM cert)
- `pillow` (PIL fallback для Figma image composition + format resize)
- Redis Stack 7.4
- Docker Compose

## 3. Архитектура

### LangGraph nodes (async)

1. `parse_brief` — DeepSeek-V4-Pro (long-context). Wizard text → `AdBrief` Pydantic.
2. `derive_persona` — GLM-5.1 thinking-OFF. Описание ЦА → 1-3 дискретные `Persona`.
3. `generate_message_candidates` — GLM-5.1 thinking-OFF. N=3-5 кандидатов (slogan/body/CTA). Если есть `prior_variant` (A/B вариант B) — anti-bias по hook angle.
4. `evaluate_as_persona_loop` — GLM-5.1 thinking-ON. Для каждого candidate × persona — Structured Output verdict (resonance/clarity/action_intent 0-10 + free-form). Aggregate → revise (≤2 итераций). Симуляция в роли ЦА, не критик. Для B variant — приоритет ранкинга на persona[1].
5. `hitl_text_approve` — `interrupt()`, inline `[OK, делать картинку]/[Перегенерить]/[Доработать]/[Отменить]`.
6. `generate_image` — Phygital vendored client. brand_t2i (Gemini Text node 72 + Nano Banana node 94). Вариант LLM-router (photo/render/isometric) или ручной override. Для B variant — принудительно другой вариант если позволяет brief.
7. `hitl_image_approve` — `interrupt()`, inline `[OK, делать форматы]/[Перегенерить]/[Сменить вариант]/[Отменить]`.
8. `fill_templates_per_format` — fan-out через `use_figma` MCP. Batch text updates + image fills через `figma.createImageAsync(s3_url)`. Один глобальный `{{slogan}}`, format-specific trim только при overflow.
9. `render_all` — REST `GET /v1/images/{file_key}?ids=...` (один batch на все форматы).
10. `zip_and_send` — ZIP + summary + `[Сделать вариант B (A/B)]`.

### Modal split

| Модель | Роль |
|---|---|
| `deepseek-ai/DeepSeek-V4-Pro` | parse_brief, classifier, long-context RAG |
| `zai-org/GLM-5.1` | message-gen, persona-loop (thinking-ON в critic-проходах) |
| `moonshotai/Kimi-K2.6` | visual verifier рендеров (P2, опционально) |

### Runtime

- **async LangGraph + PTB** в одном Python процессе (без Celery — 10-100/день не требует worker isolation).
- **2 Docker сервиса:** `bot` + `redis` (Redis Stack 7.4).
- **Playwright embedded** в main process с persistent volume `user_data`. Recon один раз при первом запуске. Re-login по 401-детекту → notify admin + headed page в фоне.
- **MCP streamable_http** для Figma Make MCP (один MCP-клиент в системе). Phygital через vendored Python client напрямую, без MCP. Cloud.ru FM через openai SDK.
- **structlog JSON** → stdout + RotatingFileHandler на `/data/traces/{session_id}.jsonl`. LangFuse/LangSmith добавлять по факту необходимости.
- **Whitelist** — env-var `WHITELIST_USER_IDS="123,456"`, единственный админ.

### Figma anatomy

- Одна Figma-страница, N фреймов с naming `format__<slug>__<WxH>`.
- Placeholders `{{token}}` в named text nodes. Детерминированные из brief (`{{product}}`, `{{cta}}`, `{{disclaimer}}`, `{{date}}`) + LLM-generated (`{{slogan}}`, `{{body}}`, `{{hashtags}}`).
- Image — Frame с name `{{hero_image}}`, scaleMode = FILL (никаких компонентов с image properties).
- Image-write: Phygital PNG → Cloud.ru Object Storage public URL (TTL 7д) → `figma.createImageAsync(url)` через `use_figma`. Fallback при недоступности MCP image-write — PIL local composition.
- Render: REST `/v1/images` batch (1 request на 8 форматов = 100 req/день при 100 task/день, 6× запас под 600/день лимит).

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
├─ pyproject.toml                  # uv-managed
├─ docker-compose.yml              # bot + redis
├─ .env.example
├─ .gitignore
├─ docs/
│  └─ archive/
│     └─ HANDOFF-2026-06-03-figma-tirage.md   # оригинальный data-merge замысел
├─ bot/                            # PTB layer
│  ├─ __init__.py
│  └─ app.py                       # entry, whitelist middleware, /start wizard
├─ llm/                            # Cloud.ru FM client
│  ├─ __init__.py
│  └─ cloudru.py                   # model-aware thinking-toggle, retry-with-feedback
├─ graph/                          # LangGraph nodes & state (создаётся в M2)
├─ figma/                          # MCP client + REST helpers (M3)
├─ phygital/                       # vendored client from Phygital-bot (M3)
├─ prompts/                        # SKILL.md
│  ├─ creative_ads_explorer.md
│  ├─ creative_positioning.md
│  └─ persona_eval.md
└─ tests/
   └─ integration/
      └─ test_smoke.py             # ping 3 моделей одним API-ключом
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

### M3 — image gen + Figma fill + render

- `phygital/` vendored client (копия из Phygital-bot) + brand_t2i workflow
- Playwright sidecar embedded в main process
- `graph/nodes/generate_image.py`
- HITL image approve
- `figma/mcp.py` подключение к Figma Make MCP (streamable_http) через `langchain-mcp-adapters`
- `graph/nodes/fill_templates_per_format.py`
- `graph/nodes/render_all.py` + REST batch `/v1/images`
- Image-write smoke-test: подтвердить или fallback на PIL

**DoD:** real brief → winner → image → 8 формат ZIP → TG.

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

1. **Figma image-write через MCP не подтверждён.** Smoke-test в M3, fallback PIL.
2. **Phygital recon свежесть.** SuperTokens expiry → 401 detect → notify + re-login.
3. **SKILL.md alignment cost.** Пилот на `creative-ads-explorer`, экстраполируем.
4. **Persona-loop стоимость.** 100/день × 16 вызовов = 1600/день. ОК до 100/день, дальше пересмотреть N×M.
5. **Figma rate-limit.** Batch `/images` спасает от 600/день при цели 100/день × 8 форматов.

## 8. Memory & references

- Agent memory: `C:\Users\Глеб\.claude\projects\C--Users------Documents-Resize-bot\memory\`
- Vault folder note: `2nd brain\01 Projects\Resize_bot\Resize_bot.md`
- Brainstorming session: `2nd brain\01 Projects\Resize_bot\Resize_bot — старт brainstorming 2026-06-04.md`
- Sister project (sаме stack reference): `2nd brain\01 Projects\Cloud.ru Slides Bot\`
- Brand prompts source: `C:\Users\Глеб\Documents\Phygital-bot\docs\SYSTEM_PROMPT_*.md`
- Skills upstream: `https://github.com/DKeken/codex-skills-alternative`
