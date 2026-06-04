"""Generic text-shape hooks: emoji, length, diversity, slogan exclamation."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from .base import Violation, iter_items

# Covers the major emoji blocks + dingbats/misc-symbols. Not exhaustive but
# catches everything an LLM would actually produce.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U0001F1E0-\U0001F1FF"
    "\u2600-\u27BF"
    "]+",
    flags=re.UNICODE,
)


def _item_id(item: Any) -> str:
    return str(getattr(item, "id", "") or "")


def ban_emoji(result: BaseModel, spec: dict) -> list[Violation]:
    fields: list[str] = list(spec.get("fields") or [])
    sev: str = spec.get("on_violation", "fail")
    out: list[Violation] = []
    for item in iter_items(result):
        for f in fields:
            val = getattr(item, f, None)
            if val is None:
                continue
            if _EMOJI_RE.search(str(val)):
                tag = f" (id={_item_id(item)})" if _item_id(item) else ""
                out.append(
                    Violation(
                        hook="ban_emoji",
                        field=f,
                        message=f"emoji в поле {f}{tag}: {val!r}",
                        severity=sev,
                    )
                )
    return out


def text_len(result: BaseModel, spec: dict) -> list[Violation]:
    field: str = spec["field"]
    sev: str = spec.get("on_violation", "fail")
    max_len = spec.get("max")
    min_len = spec.get("min")
    max_words = spec.get("max_words")
    min_words = spec.get("min_words")
    out: list[Violation] = []
    for item in iter_items(result):
        val = getattr(item, field, None)
        if val is None:
            continue
        s = str(val)
        tag = f" (id={_item_id(item)})" if _item_id(item) else ""
        if max_len is not None and len(s) > max_len:
            out.append(
                Violation(
                    "text_len",
                    field,
                    f"{field}{tag}: len={len(s)} > max={max_len}: {s!r}",
                    sev,
                )
            )
        if min_len is not None and len(s) < min_len:
            out.append(
                Violation(
                    "text_len",
                    field,
                    f"{field}{tag}: len={len(s)} < min={min_len}: {s!r}",
                    sev,
                )
            )
        if max_words is not None or min_words is not None:
            n = len(s.split())
            if max_words is not None and n > max_words:
                out.append(
                    Violation(
                        "text_len",
                        field,
                        f"{field}{tag}: words={n} > max_words={max_words}: {s!r}",
                        sev,
                    )
                )
            if min_words is not None and n < min_words:
                out.append(
                    Violation(
                        "text_len",
                        field,
                        f"{field}{tag}: words={n} < min_words={min_words}: {s!r}",
                        sev,
                    )
                )
    return out


def hook_diversity(result: BaseModel, spec: dict) -> list[Violation]:
    """Reject duplicate values of `field` across items in a wrapper-list result."""
    field: str = spec["field"]
    sev: str = spec.get("on_violation", "warn")
    items = list(iter_items(result))
    values = [getattr(item, field, None) for item in items]
    seen: set[Any] = set()
    dupes: list[Any] = []
    for v in values:
        if v in seen:
            dupes.append(v)
        else:
            seen.add(v)
    if dupes:
        return [
            Violation(
                hook="hook_diversity",
                field=field,
                message=f"duplicate {field} values: {dupes}",
                severity=sev,
            )
        ]
    return []


def no_exclamation_in_slogan(result: BaseModel, spec: dict) -> list[Violation]:
    """Calm tone: `!` not allowed in slogan (CTA is permitted)."""
    sev: str = spec.get("on_violation", "warn")
    out: list[Violation] = []
    for item in iter_items(result):
        slogan = getattr(item, "slogan", None)
        if slogan is None:
            continue
        if "!" in str(slogan):
            tag = f" (id={_item_id(item)})" if _item_id(item) else ""
            out.append(
                Violation(
                    hook="no_exclamation_in_slogan",
                    field="slogan",
                    message=f"`!` not allowed in slogan{tag}: {slogan!r}",
                    severity=sev,
                )
            )
    return out
