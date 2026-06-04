# Open questions

Архитектурные вопросы, по которым **нет решения**, но есть варианты. Перед стартом этапа M3 в коде — нужно закрыть критичные.

---

## OQ-01: Image hosting для Figma Make `createImageAsync` — БЛОКЕР M3

**Контекст.** В M3 hero-картинка генерируется через Phygital → возвращается как файл / URL → должна быть загружена в Figma как Image fill для слоя `{{hero_image}}`. Figma Make API `figma.createImageAsync(url)` требует **публично доступного URL**, который Figma-серверы сходят и скачают.

**Ограничения от пользователя:**
- Нет bucket Cloud.ru Evolution Object Storage, нет возможности завести.
- Идеально — **локальное** решение без подключения внешних сервисов.

**Варианты (с трейд-оффами):**

### A. Локальный HTTP-сервер бота + Cloudflare Tunnel / ngrok
- Бот поднимает aiohttp на `:8080/images/<uuid>.png`, картинки в `var/images/`.
- Cloudflare Tunnel (`cloudflared`) даёт публичный `*.trycloudflare.com` URL, проксирует на localhost.
- ⚠️ Это всё ещё внешний сервис, хоть и бесплатный и без аккаунта.
- ➕ Картинки физически лежат локально, можно ротировать.
- ➖ Стабильность tunnel-сессии; URL меняется при рестарте.

### B. Загружать картинку в Telegram, использовать TG-CDN URL как hosting
- Бот шлёт картинку юзеру `sendPhoto` → получает `file_id` → через `getFile` получает `file_path` → собирает `https://api.telegram.org/file/bot<TOKEN>/<file_path>`.
- ➕ Никаких новых сервисов. Уже всё нужное есть.
- ➖ URL содержит bot-token (если кто-то перехватит — компрометация бота). Для прода НЕ ОК. Для smoke — приемлемо.
- ➖ Срок жизни file_path не гарантирован Telegram'ом.

### C. Чисто локальный pipeline без Figma Make (Plan B архитектура)
- Не использовать Figma Make вообще. Скачать мастер-фрейм через Figma REST `/v1/files/.../images?ids=...&format=png&use_absolute_bounds=true` как **шаблон-PNG**. Сделать `{{hero_image}}` прозрачной дырой / chroma-key. Текст наложить **локально через PIL/Skia**, hero — встроить локально, всё локально.
- ➕ Полностью локально, никаких внешних URL.
- ➖ Теряем «WYSIWYG из Figma» — текст рисуется PIL'ом, шрифты и кернинг могут разойтись с тем, что видит дизайнер в Figma.
- ➖ Нужно поддерживать локальный рендер-движок параллельно с Figma'ой.
- ➖ Каждый формат — отдельный шаблон-PNG с заранее заданными координатами текста (либо JSON-манифест с боксами).

### D. Гибрид: Figma REST для рендера + локальный PIL только для подмены hero
- Использовать Figma как редактор и WYSIWYG (тексты заполняем через Make MCP).
- Hero-изображение **не** грузим в Figma — оставляем placeholder, рендерим фрейм через REST → получаем PNG → **локально PIL'ом** накладываем hero в координаты слоя `{{hero_image}}` (координаты тянем из `/v1/files/.../nodes`).
- ➕ Никакого внешнего hosting'а для изображения.
- ➕ Тексты остаются в Figma — кернинг/типографика правильные.
- ➖ Нужна точная calibration координат `{{hero_image}}` (Figma даёт через nodes API).
- ➖ Если в шаблоне hero «уходит за фрейм» с маской — PIL-наложение это не воспроизведёт, нужна аккуратность с дизайном.

### E. Локальный HTTP-сервер бота + статический публичный домен (минимум: VPS / домашний роутер)
- То же что A, но без tunnel: реальный домен с TLS, например `images.resize-bot.example.com`.
- ➖ Требует внешнего хостинга / домена. Противоречит «без сервисов».

---

**Решение (Глеб, 2026-06-04): вариант A — локальный HTTP-сервер бота + Cloudflare Tunnel.**

Что это значит для M3:
- В bot-контейнере поднимается aiohttp на `:8080`, отдаёт `/images/<uuid>.png` из `var/images/`.
- Sidecar-контейнер `cloudflared` (или embedded subprocess) держит quick-tunnel и публикует `https://<random>.trycloudflare.com`.
- При старте бот логирует базовый URL туннеля; для `figma.createImageAsync(url)` собираем `https://<tunnel>/images/<uuid>.png`.
- Картинки очищаются по TTL 24ч (cron в боте).
- URL пересоздаётся при рестарте — это нормально, в моменте Figma скачивает в течение секунд.

Минусы, которые принимаем:
- Cloudflare Tunnel — внешний сервис (квази-нейтральный, без аккаунта, без рекламных персональных данных).
- При падении tunnel'а нужна повторная инициализация (бот должен переподнимать sidecar).

Следующие шаги в коде (когда дойдём до M3):
1. `infra/tunnel.py` — обёртка над `cloudflared` (download binary on first start, supervised subprocess).
2. `infra/http_server.py` — aiohttp + статика.
3. `graph/nodes/generate_image.py` — сохраняет PNG в `var/images/`, формирует URL вида `{tunnel_url}/images/{uuid}.png`, передаёт в `fill_templates_per_format`.

---

## OQ-02: Figma Make MCP — авторизация в Docker

**Контекст.** MCP-клиент к Figma Make ходит через streamable_http с OAuth-сессией. Первый headed-handshake невозможен внутри Linux-контейнера (Google/Figma SSO не пускает в headless Chromium).

**План.** Тот же паттерн, что для Phygital: первый recon — на хосте (headed Playwright), сохраняем `storage_state.json` на volume, контейнер маунтит его в read-only.

**Решение:** план зафиксирован, реализуем по факту в M3.

---

## OQ-03: Phygital recon

**Контекст.** Embedded Playwright в bot-контейнере. Первый логин нужен headed-сессией на хосте, потом storage_state в volume для контейнера.

**План:**
1. Скрипт `scripts/phygital_recon.py` на хосте — открывает headed Chromium, ждёт пока пользователь руками залогинится, сохраняет `storage_state.json` в `./var/playwright_user_data/`.
2. Docker-volume `./var/playwright_user_data:/app/var/playwright_user_data:ro` подключается к bot-контейнеру.
3. Re-login по 401: бот пишет в TG owner'у «нужен re-recon», пользователь перезапускает скрипт.

**Решение:** план зафиксирован, реализуем в M3 после подтверждения OQ-01.

---

## OQ-04: B-вариант в M3 — включать сразу или после smoke?

**Контекст.** A/B уже есть в текстовом пайплайне (`PriorVariant` + persona-weight shift). В M3 это значит:
- 2 hero-картинки на запуск (A photo, B render — например).
- 2 фрейма в Figma (`__default__` и `__alt__`).
- 2 рендера, 2 ZIP'а — или один ZIP с обоими.

**Решение (Глеб, 2026-06-04):**
- **M3.0 smoke** — только A-вариант. Цель: end-to-end бриф → image → Figma fill → render → ZIP в TG.
- **M3.1** — A+B параллельно: 2 hero, 2 фрейма (`__default__` + `__alt__`), оба рендерятся, оба идут в ZIP. Persona-loop уже умеет weight-shift'ить для B.

---

## OQ-05 (открытое, не критично): retry-policy hook violations

При множественных violations — даём один общий retry с агрегированным фидбэком, или поштучно? Сейчас в `cloudru.structured` — один retry на любую ошибку валидации. Достаточно?

**Решение:** оставляем как есть, пересматриваем если поймаем bad case в проде.
