"""Environment-driven config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _parse_user_ids(raw: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()
    return frozenset(int(x.strip()) for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    whitelist_user_ids: frozenset[int]
    admin_user_id: int | None

    cloudru_api_key: str
    cloudru_base_url: str

    redis_url: str

    figma_file_key: str
    figma_mcp_url: str
    figma_api_token: str

    phygital_base_url: str

    log_level: str
    traces_dir: str
    playwright_user_data: str


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
        figma_file_key=os.environ.get("FIGMA_FILE_KEY", ""),
        figma_mcp_url=os.environ.get("FIGMA_MCP_URL", ""),
        figma_api_token=os.environ.get("FIGMA_API_TOKEN", ""),
        phygital_base_url=os.environ.get(
            "PHYGITAL_BASE_URL", "https://app-server-azure.phygital.plus"
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        traces_dir=os.environ.get("TRACES_DIR", "/data/traces"),
        playwright_user_data=os.environ.get("PLAYWRIGHT_USER_DATA", "/data/user_data"),
    )
