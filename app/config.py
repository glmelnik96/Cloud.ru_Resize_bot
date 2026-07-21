"""App3 configuration via pydantic-settings.

Env-key names mirror App1 where they overlap (DB_URL, RESULTS_DIR,
MAX_CONCURRENCY, ...) so the platform's deploy conventions line up. App3-only
keys: CHECKPOINT_DB (SQLite langgraph checkpointer), HERO_GEN_BACKEND /
APP1_HERO_URL (hero generation delegated to App1), plus the Cloud.ru FM keys
the graph reads from the environment.
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
    # Section prefix the gateway mounts this sub-app under, used for the page's
    # asset/API URLs. Default matches prod; set "" for a local run with no
    # gateway (assets at /static, API at /api).
    prefix: str = Field("/creatives", alias="APP3_PREFIX")
    # Local-smoke dev auth: when set AND no gateway X-User-Id header is present,
    # treat the request as this user (email). Empty (default) → prod behavior:
    # missing header → 401. The gateway always injects the header in prod.
    dev_user: str = Field("", alias="APP3_DEV_USER")
    # Own DB — isolated per App1 pattern; Task/User lifecycle (NOT graph state).
    db_url: str = Field("sqlite+aiosqlite:///./data/app3.db", alias="DB_URL")
    results_dir: Path = Field(ROOT / "data" / "results", alias="RESULTS_DIR")

    # --- scratch dirs (graph render outputs land here, then move to results) ---
    heroes_dir: Path = Field(ROOT / "data" / "heroes", alias="HEROES_DIR")
    renders_dir: Path = Field(ROOT / "data" / "renders", alias="RENDERS_DIR")
    zips_dir: Path = Field(ROOT / "data" / "zips", alias="ZIPS_DIR")
    tmp_root: Path = Field(ROOT / "data" / "tmp", alias="TMP_ROOT")

    # --- queue limits (App1) ---
    # All gt=0: a zero/negative cap is never meaningful and breaks the runtime
    # (Semaphore(0) deadlocks compute, a 0 queue rejects everything) — reject at
    # settings load instead of failing obscurely later.
    max_concurrency: int = Field(5, alias="MAX_CONCURRENCY", gt=0)
    max_per_user_inflight: int = Field(2, alias="MAX_PER_USER_INFLIGHT", gt=0)
    user_queue_limit: int = Field(5, alias="USER_QUEUE_LIMIT", gt=0)

    # --- HITL image-upload window (graph cancels the task after this) ---
    image_timeout_sec: int = Field(24 * 3600, alias="IMAGE_TIMEOUT_SEC", gt=0)
    # --- results retention TTL ---
    # gt=0: a negative TTL makes the purge cutoff land in the future and wipes
    # every result immediately.
    retention_ttl_sec: int = Field(24 * 3600, alias="RETENTION_TTL_SEC", gt=0)

    # --- upload guard: hard cap on a single hero upload (bytes) ---
    # Bounds per-request memory and blocks oversized/non-image payloads at the
    # multipart boundary before they reach disk or PIL. 20MB covers real photos.
    # gt=0: a 0 cap rejects every upload as "too large".
    max_upload_bytes: int = Field(20 * 1024 * 1024, alias="MAX_UPLOAD_BYTES", gt=0)

    # --- langgraph checkpointer (App3-only: durable HITL park/resume) ---
    # SQLite-backed (no Redis on the VM — platform decision 2026-06-21).
    # Separate DB file from db_url so checkpoint + app schemas stay independent.
    checkpoint_db: str = Field("./data/app3_checkpoints.db", alias="CHECKPOINT_DB")

    # --- hero generation (delegated to App1, platform decision 2026-06-21) ---
    # "none" → manual upload only (default until App1's /internal/hero is live);
    # "app1" → POST the prompt to App1's internal loopback endpoint.
    hero_gen_backend: str = Field("none", alias="HERO_GEN_BACKEND")
    app1_hero_url: str = Field(
        "http://127.0.0.1:8011/internal/hero", alias="APP1_HERO_URL"
    )

    # --- Cloud.ru FM (the graph's llm/cloudru.py reads these from env) ---
    cloudru_api_key: str = Field("", alias="CLOUDRU_API_KEY")
    cloudru_base_url: str = Field(
        "https://foundation-models.api.cloud.ru/v1", alias="CLOUDRU_BASE_URL"
    )

    log_level: str = Field("INFO", alias="LOG_LEVEL")


settings = Settings()
