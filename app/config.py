"""App3 configuration via pydantic-settings.

Env-key names mirror App1 where they overlap (DB_URL, RESULTS_DIR,
MAX_CONCURRENCY, ...) so the platform's deploy conventions line up. App3-only
keys: REDIS_URL (langgraph checkpointer), PHYGITAL_SESSION_FILE (web hero
generation), plus the Cloud.ru FM keys the graph reads from the environment.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- gateway sub-app contract ---
    # Loopback bind port (informational; uvicorn is started with --port).
    port: int = Field(8013, alias="PORT")
    # Own DB — isolated per App1 pattern; Task/User lifecycle (NOT graph state).
    db_url: str = Field("sqlite+aiosqlite:///./data/app3.db", alias="DB_URL")
    results_dir: Path = Field(ROOT / "data" / "results", alias="RESULTS_DIR")

    # --- scratch dirs (graph render outputs land here, then move to results) ---
    heroes_dir: Path = Field(ROOT / "data" / "heroes", alias="HEROES_DIR")
    renders_dir: Path = Field(ROOT / "data" / "renders", alias="RENDERS_DIR")
    zips_dir: Path = Field(ROOT / "data" / "zips", alias="ZIPS_DIR")
    tmp_root: Path = Field(ROOT / "data" / "tmp", alias="TMP_ROOT")

    # --- queue limits (App1) ---
    max_concurrency: int = Field(5, alias="MAX_CONCURRENCY")
    max_per_user_inflight: int = Field(2, alias="MAX_PER_USER_INFLIGHT")
    user_queue_limit: int = Field(5, alias="USER_QUEUE_LIMIT")

    # --- langgraph checkpointer (App3-only: durable HITL park/resume) ---
    redis_url: str = Field("redis://127.0.0.1:6379/0", alias="REDIS_URL")

    # --- web Phygital hero generation (channel switch, phase 5) ---
    phygital_session_file: Path = Field(
        ROOT / "storage" / "session.json", alias="PHYGITAL_SESSION_FILE"
    )

    # --- Cloud.ru FM (the graph's llm/cloudru.py reads these from env) ---
    cloudru_api_key: str = Field("", alias="CLOUDRU_API_KEY")
    cloudru_base_url: str = Field(
        "https://foundation-models.api.cloud.ru/v1", alias="CLOUDRU_BASE_URL"
    )

    log_level: str = Field("INFO", alias="LOG_LEVEL")


settings = Settings()
