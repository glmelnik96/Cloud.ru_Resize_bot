"""Конфиг через pydantic-settings + .env.

VENDOR PATCH (Resize_bot integration, 2026-06-04):
  - Added STORAGE_DIR resolved from PHYGITAL_STORAGE_DIR env var (falls back to
    ROOT/"storage" for standalone use). Used by workflows.brand_docs.CACHE_FILE
    and Settings.session_file so SuperTokens session + brand_docs cache live
    in a docker volume mounted to /data/phygital_storage instead of /app.
  - .env loading is disabled by default in container mode: Resize_bot owns its
    own .env at /app/.env and passes PHYGITAL_* via docker env_file. If pydantic
    reads /app/.env here it would also pick up TELEGRAM_BOT_TOKEN etc., harmless
    but noisy. We leave it pointing at ROOT/".env" (vendor dir) which won't
    exist in container → effectively env-only.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# Vendor patch: storage dir override. Used by brand_docs cache + session file.
STORAGE_DIR = Path(os.environ.get("PHYGITAL_STORAGE_DIR") or (ROOT / "storage"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    phygital_base_url: str = "https://app.phygital.plus"
    phygital_email: str = ""
    phygital_password: str = ""

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""  # CSV; парсим в свойстве
    telegram_proxy_url: str = ""  # http://host:port; пусто = без прокси
    telegram_proxy_cert: str = ""  # path к PEM CA прокси; пусто = truststore (keychain)
    bot_max_concurrency: int = 5
    # CSV uid'ов, которым доступна админская статистика по фидбэку.
    bot_owner_uids: str = ""

    # Vendor patch: was `ROOT / "storage" / "session.json"`.
    session_file: Path = STORAGE_DIR / "session.json"

    log_level: str = "INFO"

    @property
    def allowed_user_ids(self) -> set[int]:
        ids = (x.strip() for x in self.telegram_allowed_user_ids.split(","))
        return {int(x) for x in ids if x}

    @property
    def owner_user_ids(self) -> set[int]:
        ids = (x.strip() for x in self.bot_owner_uids.split(","))
        return {int(x) for x in ids if x}


settings = Settings()
