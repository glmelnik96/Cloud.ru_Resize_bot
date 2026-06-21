# App3 — локальный ручной смоук перед редеплоем

**Дата:** 2026-06-21
**Статус:** утверждён, к реализации

## Цель
Дать возможность поднять App3 локально и **вручную** пройти весь флоу `/new`
(бриф → текст-HITL → hero-HITL → ZIP) на **реальном** рантайме перед пушем/редеплоем
на VM, не завися от платформенного шлюза и от App1 (hero подкладывается вручную
кнопкой загрузки).

## Решения (из брейншторма)
- **Форма:** интерактивный ручной смоук в браузере (не авто-pytest-гейт).
- **Реализм:** полностью реальный — Cloud.ru FM (GLM-5.1 + DeepSeek-V4-Pro),
  реальный PIL-композер и ZIP.
- **Рантайм:** локальный venv на **Python 3.11** (`interrupt()` в async-узлах
  требует 3.11). Это тот же путь, что прод-юнит `deploy/app3.service`
  (`pip install -e '.[web]'` → `uvicorn app.main:app`), только локально.
- **Аутентификация без шлюза:** dev-флаг в приложении (`APP3_DEV_USER`),
  по умолчанию выключен → прод не меняется.
- **Hero:** кнопка «Загрузить картинку» в UI (`HERO_GEN_BACKEND=none` → genBtn скрыт).

## Модели (для allowlist/ключа)
Флоу `/new` ходит только на `foundation-models.api.cloud.ru/v1`:
- `parse_brief` → DeepSeek-V4-Pro
- `derive_persona`, `generate_message_candidates`, `evaluate_as_persona_loop`,
  `route_image_style`, `generate_image_prompt` → GLM-5.1
- Kimi-K2.6 (vision) — НЕ используется. App3 image-генерацию сам не делает.

## Изменения в коде (оба прод-безопасны по умолчанию)

### 1. Dev-аутентификация
- `app/config.py`: новая настройка `dev_user: str = Field("", alias="APP3_DEV_USER")`.
- `app/main.py` `_resolve_settings`: прокинуть `dev_user` в cfg (для test-override
  и чтения через `app.state.settings`).
- `app/auth/deps.py` `get_current_user`: если заголовка `X-User-Id` нет **и**
  `dev_user` непустой → подставить фиктивного юзера (gateway_user_id `"dev"`,
  email = `dev_user`) + `log.warning("dev_auth_bypass", ...)`. Если `dev_user`
  пуст → как сейчас: нет заголовка → 401.

### 2. Конфигурируемый префикс страницы
- `app/config.py`: `prefix: str = Field("/creatives", alias="APP3_PREFIX")`.
- `app/main.py` `_resolve_settings`: прокинуть `prefix` в cfg.
- `app/api/routes_pages.py`: брать префикс из `app.state.settings` (дефолт
  `/creatives`) вместо зашитой константы. Локально `APP3_PREFIX=""` →
  ассеты `/static/...`, API `/api/...`, `window.APP_PREFIX=""`.

Без этого локально (без шлюза, снимающего префикс) шаблон просит
`/creatives/static/...` → 404, и JS бьёт API в `/creatives/api/...` → 404.

## Запуск
Скрипт `scripts/run_local.sh` (bash):
- проверяет `.venv311` (если нет — печатает инструкцию создания);
- экспортирует `APP3_DEV_USER`, `APP3_PREFIX=""`, `HERO_GEN_BACKEND=none`;
- прокидывает `CLOUDRU_API_KEY` из окружения (в репо не коммитим);
- стартует `uvicorn app.main:app --host 127.0.0.1 --port 8013 --reload` из `.venv311`.

Ручной чек-лист (в шапке скрипта/доке): открыть `/`, ввести бриф, дождаться
текст-HITL → принять, на hero-HITL загрузить картинку, скачать ZIP, проверить
форматы.

## Тесты (TDD)
- dev-auth: `APP3_DEV_USER` задан + нет заголовка → `GET /` = 200; не задан +
  нет заголовка → 401 (регресс-защита).
- prefix: `APP3_PREFIX=""` → в HTML `/static/app.css` и `window.APP_PREFIX = ""`;
  дефолт → `/creatives/...` (существующий `test_app3_pages` не ломается).

## Вне scope (YAGNI)
Папка-дропзона для hero; Docker под App3; изменения шлюза/App1; авто-e2e с реальным
LLM в CI.
