# Open questions

Архитектурные вопросы, по которым **нет решения**, но есть варианты.

> История M3.0–M3.2 (Figma MCP / Phygital / tunnel / image-hosting) — закрыта переходом
> на **M3.3 user-uploaded hero + PIL композер**. Старые OQ-01…OQ-04 ниже помечены
> RESOLVED/OBSOLETE и оставлены для археологии. Полный postmortem M3.2 —
> `docs/archive/M3.2_BROKEN-2026-06-05.md`.

---

## Активные

### OQ-05: Retry-policy hook violations

При множественных violations — даём один общий retry с агрегированным фидбэком, или поштучно? Сейчас в `cloudru.structured` — один retry на любую ошибку валидации.

**Решение:** оставляем как есть, пересматриваем если поймаем bad case в проде.

### OQ-06 (новое M3.3): Hero PNG валидация на входе

Юзер шлёт PNG/JPEG в `hitl_image_upload`. Сейчас бот:
- принимает PHOTO и Document с `image/*` MIME,
- скачивает в `/data/heroes/<thread_id>_<ts>.<ext>`,
- отдаёт `local_path` в composer.

Что **не** проверяется:
- aspect-ratio (если юзер прислал 9:16, а template ждёт 1:1 — `fit: cover` обрежет, но потеря может быть критичной).
- разрешение (если 256×256 → upscaling до 1920×1080 даст мыло).
- цветовой профиль (CMYK PNG падает в PIL).
- наличие текста на картинке (юзер прислал готовый макет вместо чистого hero).

**Варианты:**
- A. Не валидируем, доверяем юзеру (текущее поведение).
- B. Soft warnings в TG («картинка низкого разрешения, продолжить?»).
- C. Kimi-K2.6 vision-judge: «есть ли текст на картинке?», «совпадает ли с prompt'ом?».

**Решение:** пока A. Возвращаемся если соберём 3+ bad case.

### OQ-07 (новое M3.3): Поведение при отсутствии brand_area_line ассета

Manifest ссылается на `assets/brand/brand_area_line_<W>x<H>_v1.png`. Если файл отсутствует — composer падает с FileNotFoundError. Сейчас smoke-тест `test_compose_real_template_smoke` это ловит, но в проде юзер увидит ошибку только после prompt → upload → composer-стадии.

**Варианты:**
- A. Валидация manifest'а на старте бота (`_post_init` пробегает все referenced paths, падает если нет).
- B. Graceful degradation: пропускаем `image` layer, рисуем без brand strip.

**Решение:** склоняемся к A. Заведём при следующей сессии.

---

## Архив (RESOLVED / OBSOLETE — M3.3)

### OQ-01: Image hosting для Figma `createImageAsync` — RESOLVED (obsolete)

В M3.2 был выбран вариант A (cloudflared tunnel). Реализация (`infra/{tunnel,http_server}.py`) выкинута в M3.3 — Figma MCP write-flow оказался недоступен третьей стороне, hosting больше не нужен.

### OQ-02: Figma Make MCP — авторизация в Docker — OBSOLETE

MCP-клиент к Figma Make выкинут целиком (см. `docs/archive/M3.2_BROKEN-2026-06-05.md`).

### OQ-03: Phygital recon — OBSOLETE

Phygital vendor выкинут в M3.3. Hero рисует юзер, embedded Playwright не нужен.

### OQ-04: B-вариант — RESOLVED

A/B живёт в текстовом пайплайне через `PriorVariant`. В M3.3 B-вариант = повтор пайплайна с другим `prior_variant`: один новый prompt → юзер шлёт **новую** hero-картинку → новый ZIP. Параллельные A+B в одном запуске пока не делаем.
