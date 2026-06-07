"""Environment-driven config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _parse_user_ids(raw: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()
    return frozenset(int(x.strip()) for x in raw.split(",") if x.strip())


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    whitelist_user_ids: frozenset[int]
    admin_user_id: int | None

    cloudru_api_key: str
    cloudru_base_url: str

    redis_url: str

    log_level: str
    traces_dir: str

    # M3.4: bot-to-bot delegation of hero image generation to @Cloud_Phygital_bot.
    # When use_phygital_render is False, the legacy HITL upload path is used.
    # phygital_bot_username is the @-handle without the leading @.
    use_phygital_render: bool
    phygital_bot_username: str
    phygital_request_timeout_s: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    whitelist = _parse_user_ids(os.environ.get("WHITELIST_USER_IDS", ""))
    admin = next(iter(whitelist)) if whitelist else None
    return Settings(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        whitelist_user_ids=whitelist,
        admin_user_id=admin,
        cloudru_api_key=os.environ["CLOUDRU_API_KEY"],
        cloudru_base_url=os.environ.get(
            "CLOUDRU_BASE_URL", "https://foundation-models.api.cloud.ru/v1"
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        traces_dir=os.environ.get("TRACES_DIR", "/data/traces"),
        use_phygital_render=_parse_bool(os.environ.get("USE_PHYGITAL_RENDER"), default=False),
        phygital_bot_username=os.environ.get("PHYGITAL_BOT_USERNAME", "Cloud_Phygital_bot").lstrip("@"),
        phygital_request_timeout_s=int(os.environ.get("PHYGITAL_REQUEST_TIMEOUT_S", "600")),
    )
