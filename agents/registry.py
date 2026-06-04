"""YAML card loader for agents.

Card lives at `agents/<id>.yaml`. See AGENTS.md §3 for schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_AGENTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class AgentCard:
    id: str
    skill: str
    model: str
    model_config: dict[str, Any]
    schema: str
    retry_with_feedback: int
    pre_hooks: list[dict[str, Any]]
    post_hooks: list[dict[str, Any]]
    extra: dict[str, Any]


@lru_cache(maxsize=32)
def load_agent(agent_id: str) -> AgentCard:
    path = _AGENTS_DIR / f"{agent_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    known = {
        "id",
        "skill",
        "model",
        "model_config",
        "schema",
        "retry_with_feedback",
        "pre_hooks",
        "post_hooks",
    }
    return AgentCard(
        id=data["id"],
        skill=data["skill"],
        model=data["model"],
        model_config=dict(data.get("model_config") or {}),
        schema=data.get("schema", ""),
        retry_with_feedback=int(data.get("retry_with_feedback", 1)),
        pre_hooks=list(data.get("pre_hooks") or []),
        post_hooks=list(data.get("post_hooks") or []),
        extra={k: v for k, v in data.items() if k not in known},
    )
