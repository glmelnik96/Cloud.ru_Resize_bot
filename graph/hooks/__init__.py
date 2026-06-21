"""Post-hooks library for agents.

Each hook = pure function (result: BaseModel, spec: dict) -> list[Violation].
Hooks are listed in `agents/<id>.yaml` under `post_hooks:`.
See AGENTS.md §4 for brand-guard contract.
"""

from __future__ import annotations

from pydantic import BaseModel

from .base import Violation
from .brand_stopwords import brand_stopwords
from .text import (
    ban_emoji,
    hook_diversity,
    no_exclamation_in_slogan,
    text_len,
)

HOOK_REGISTRY = {
    "ban_emoji": ban_emoji,
    "text_len": text_len,
    "hook_diversity": hook_diversity,
    "no_exclamation_in_slogan": no_exclamation_in_slogan,
    "brand_stopwords": brand_stopwords,
}


def run_hooks(result: BaseModel, specs: list[dict]) -> list[Violation]:
    out: list[Violation] = []
    for spec in specs:
        hook_id = spec.get("id")
        fn = HOOK_REGISTRY.get(hook_id)
        if fn is None:
            raise KeyError(f"unknown hook id: {hook_id!r}")
        out.extend(fn(result, spec))
    return out


__all__ = ["Violation", "run_hooks", "HOOK_REGISTRY"]
