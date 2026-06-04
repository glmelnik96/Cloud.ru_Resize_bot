"""Cloud.ru 2.0 brand-voice regex stop-words.

Source of truth: AGENTS.md §4.1. Changes go through that file first, then code.

Groups:
- A — visual pomp not native to Cloud.ru (cinematic, neon, hologram, ...)
- B — corporate clichés / buzzwords in Russian (революционный, синергия, ...)
  - exception: `экосистем*` is warn-only (often used legitimately)
- C — Nothing-distinction (transparent housing, glyph, ...)
- D — outdated design references (vintage, retro, Braun-style, ...)
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from .base import Violation, iter_items

_GROUP_A = [
    re.compile(
        r"\b(epic|cinematic|award.?winning|masterpiece|hyperdetail\w*|ultra.?realistic)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(neon|glassmorphism|cyberpunk|vaporwave|synthwave|hologram\w*)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(fractal\w*|neural.?mesh|brain.?network|swirling.?data)\b",
        re.IGNORECASE,
    ),
]

_GROUP_B_FAIL = [
    re.compile(
        r"\b(революцион\w+|инновацион\w+|прорывн\w+|потрясающ\w+|невероятн\w+)\b",
        re.IGNORECASE,
    ),
    re.compile(r"передов\w+\s+технолог\w+", re.IGNORECASE),
    re.compile(r"облачн\w+\s+решен\w+\s+нового\s+поколен\w+", re.IGNORECASE),
    re.compile(
        r"\b(цифров\w+\s+трансформац\w+|синерги\w+|комплиментарн\w+)\b",
        re.IGNORECASE,
    ),
]
_GROUP_B_WARN = [
    re.compile(r"\bэкосистем\w+\b", re.IGNORECASE),
]

_GROUP_C = [
    re.compile(
        r"\b(transparent\s+housing|glyph.?interface|dot.?matrix|LED.?matrix)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bNothing\s+(Phone|Ear|Buds)\b", re.IGNORECASE),
]

_GROUP_D = [
    re.compile(r"\b(vintage|retro|1960s|mid.?century|Braun.?style)\b", re.IGNORECASE),
]

_FAIL_GROUPS: dict[str, list[re.Pattern[str]]] = {
    "A": _GROUP_A,
    "B": _GROUP_B_FAIL,
    "C": _GROUP_C,
    "D": _GROUP_D,
}


def brand_stopwords(result: BaseModel, spec: dict) -> list[Violation]:
    groups: list[str] = list(spec.get("groups") or ["A", "B", "C", "D"])
    fields: list[str] = list(spec.get("fields") or [])
    fail_sev: str = spec.get("on_violation", "fail")

    fail_patterns: list[re.Pattern[str]] = []
    for g in groups:
        fail_patterns.extend(_FAIL_GROUPS.get(g, []))

    warn_patterns = _GROUP_B_WARN if "B" in groups else []

    out: list[Violation] = []
    for item in iter_items(result):
        for f in fields:
            val = getattr(item, f, None)
            if val is None:
                continue
            text = str(val)
            for pat in fail_patterns:
                m = pat.search(text)
                if m:
                    out.append(
                        Violation(
                            hook="brand_stopwords",
                            field=f,
                            message=f"stop-word '{m.group(0)}' in {f}: {text!r}",
                            severity=fail_sev,
                        )
                    )
            for pat in warn_patterns:
                m = pat.search(text)
                if m:
                    out.append(
                        Violation(
                            hook="brand_stopwords",
                            field=f,
                            message=f"caution '{m.group(0)}' in {f} (requires concretization)",
                            severity="warn",
                        )
                    )
    return out
