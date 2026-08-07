"""Паспорт происхождения ZIP-архива (блок 4 синтеза с Bannerzila).

`build_provenance` снимает снапшот того, из чего собран результат:

- версии всех промптов-скиллов (prompts/*.md frontmatter);
- конфигурацию всех агентов: модель из карточки + ОБЪЯВЛЕННАЯ
  fallback-цепочка (какая модель реально ответила — в structlog,
  cloudru_call/cloudru_fallback_succeeded; в паспорт идёт статичная правда);
- версию манифеста шаблонов (config/templates.json);
- выбранные карточки с оффер-полями (anchor/desired_outcome) и lint_flags;
- метафорные обоснования (metaphor_meta из generate_image_prompt);
- источник hero (generated/uploaded) и sha256 каждого hero-ассета.

Паспорт — best-effort: вызывающий (render_all) оборачивает сбой в fail-open,
ZIP без provenance.json лучше, чем никакого ZIP.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from agents.registry import load_agent
from graph.agent_runner import _MODEL_MAP
from graph.prompts import load_skill
from infra.template_manifest import load_manifest
from llm.cloudru import _FALLBACKS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROMPTS_DIR = _REPO_ROOT / "prompts"
_AGENTS_DIR = _REPO_ROOT / "agents"
_MANIFEST_PATH = _REPO_ROOT / "config" / "templates.json"

_CANDIDATE_KEYS = ("id", "slogan", "cta", "anchor", "desired_outcome", "lint_flags")


def build_provenance(state: dict, *, rendered_files: list[dict]) -> dict:
    return {
        "provenance_version": "0.1.0",
        "session_id": state.get("session_id") or "nosession",
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "template_manifest_version": load_manifest(_MANIFEST_PATH).version,
        "prompts": _prompt_versions(),
        "agents": _agent_snapshot(),
        "candidates": [
            {k: c.get(k) for k in _CANDIDATE_KEYS}
            for c in (state.get("ranked") or [])
        ],
        "metaphor_meta": state.get("metaphor_meta") or [],
        "hero_source": _hero_source(state),
        "files": [_file_entry(e) for e in rendered_files],
    }


def _prompt_versions() -> dict[str, str]:
    return {
        p.stem: load_skill(p.stem).version
        for p in sorted(_PROMPTS_DIR.glob("*.md"))
    }


def _agent_snapshot() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(_AGENTS_DIR.glob("*.yaml")):
        card = load_agent(path.stem)
        primary = _MODEL_MAP.get(card.model)
        chain = [m.value for m in _FALLBACKS.get(primary, ())] if primary else []
        out[path.stem] = {
            "model": card.model,
            "skill": card.skill,
            "fallback_chain": chain,
        }
    return out


def _hero_source(state: dict) -> str:
    if state.get("generated_heroes"):
        return "generated"
    if state.get("image"):
        return "uploaded"
    return "none"


def _file_entry(entry: dict) -> dict:
    out: dict = {"format": entry.get("format")}
    hero_path = (entry.get("layers") or {}).get("hero")
    if hero_path and Path(hero_path).exists():
        out["hero_sha256"] = hashlib.sha256(
            Path(hero_path).read_bytes()
        ).hexdigest()
    return out
