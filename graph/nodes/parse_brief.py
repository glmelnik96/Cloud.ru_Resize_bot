"""parse_brief node — DeepSeek-V4-Pro long-context normalizer.

Input:  GraphState.raw_brief (wizard text)
Output: GraphState.brief (AdBrief)
"""

from __future__ import annotations

from datetime import date

import structlog

from graph.prompts import load_skill
from graph.state import AdBrief, GraphState
from llm.cloudru import CloudRuClient, ModelCall, ModelName

log = structlog.get_logger(__name__)

_SKILL_NAME = "parse_brief"


async def parse_brief(state: GraphState) -> dict:
    """Parse raw_brief → AdBrief via DeepSeek-V4-Pro."""
    raw = state.get("raw_brief")
    if not raw:
        raise ValueError("parse_brief: state.raw_brief is empty")

    skill = load_skill(_SKILL_NAME)
    system_msg = _extract_section(skill.body, "## System message")
    user_tpl = _extract_section(skill.body, "## User message template")

    user_msg = _render(user_tpl, raw_brief=raw, today=date.today().isoformat())

    client = CloudRuClient()
    cfg = skill.model_config.get("deepseek-v4-pro", {})
    brief = await client.call_structured(
        ModelCall(
            model=ModelName.DEEPSEEK,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            thinking=bool(cfg.get("thinking") or False),
            max_tokens=int(cfg.get("max_tokens", 2000)),
            temperature=float(cfg.get("temperature", 0.2)),
        ),
        schema=AdBrief,
    )
    log.info(
        "parse_brief_ok",
        session_id=state.get("session_id"),
        product=brief.product,
        goal=brief.goal,
        channel=brief.channel,
        n_formats=len(brief.formats),
    )
    return {"brief": brief.model_dump()}


def _extract_section(body: str, heading: str) -> str:
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


def _render(template: str, **vars: object) -> str:
    import re

    def repl(m: "re.Match[str]") -> str:
        key = m.group(1).strip()
        return str(vars.get(key, ""))

    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}", repl, template)
