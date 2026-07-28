"""SKILL.md loader.

Parses YAML frontmatter + body. Body is treated as a Jinja2-like template
with simple ``{{name}}`` placeholders (no full Jinja — keeps it dependency-free
and predictable for LLM prompts).

Usage:
    skill = load_skill("understand_product")
    rendered = skill.render(raw_brief="...", today="2026-06-04")
    skill.model_config["glm-4.7"]["thinking"]  # access model config
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    body: str
    frontmatter: dict[str, Any]

    @property
    def model_config(self) -> dict[str, Any]:
        return self.frontmatter.get("model_config", {})

    @property
    def target_primary(self) -> str | None:
        target = self.frontmatter.get("target_models", {})
        return target.get("primary") if isinstance(target, dict) else None

    def render(self, **variables: Any) -> str:
        """Substitute {{var}} placeholders. Missing vars → empty string."""

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            value = _lookup(variables, key)
            if value is None:
                return ""
            return str(value)

        return _PLACEHOLDER_RE.sub(repl, self.body)


def _lookup(variables: dict[str, Any], dotted: str) -> Any:
    obj: Any = variables
    for part in dotted.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


@lru_cache(maxsize=64)
def load_skill(name: str) -> Skill:
    """Load `prompts/<name>.md` once and cache parsed result."""
    path = _PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    parsed_fm = yaml.safe_load(fm) if fm else {}
    return Skill(
        name=parsed_fm.get("name", name),
        version=str(parsed_fm.get("version", "0.0.0")),
        body=body.strip(),
        frontmatter=parsed_fm,
    )


def extract_section(body: str, heading: str) -> str:
    """Pull the fenced block under a given `## heading` from the SKILL body."""
    idx = body.find(heading)
    if idx == -1:
        raise ValueError(f"section {heading!r} not found in skill body")
    start = body.find("```", idx)
    end = body.find("```", start + 3)
    if start == -1 or end == -1:
        raise ValueError(f"fenced block missing under {heading!r}")
    block = body[start + 3 : end]
    # drop optional language tag on first line
    if "\n" in block:
        first, rest = block.split("\n", 1)
        if not first.strip() or first.strip().isalpha():
            return rest.strip()
    return block.strip()


def render(template: str, **variables: Any) -> str:
    """Substitute ``{{var}}`` placeholders in a section. Missing vars → empty."""

    def repl(match: re.Match[str]) -> str:
        return str(variables.get(match.group(1).strip(), ""))

    return _PLACEHOLDER_RE.sub(repl, template)


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    fm = text[3:end].strip()
    body = text[end + 4 :]
    return fm, body
