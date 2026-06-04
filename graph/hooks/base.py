"""Hook primitives shared across the library."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pydantic import BaseModel


@dataclass(frozen=True)
class Violation:
    hook: str
    field: str | None
    message: str
    severity: str  # "fail" | "warn"


def iter_items(result: BaseModel) -> Iterable[BaseModel]:
    """Iterate over wrapper-list items if result wraps a list, else yield self.

    Convention: a wrapper has a single list-valued attribute (`candidates`,
    `personas`, ...). Otherwise we treat `result` as the single item.
    """
    for attr in ("candidates", "personas"):
        val = getattr(result, attr, None)
        if isinstance(val, list):
            return val
    return [result]
