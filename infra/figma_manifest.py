"""Figma template manifest: JSON-on-disk → typed pydantic models.

The manifest maps a Resize_bot format slug (e.g. ``vk_post_1080x1080``) to the
Figma frame that should be rendered and the slot node-ids inside it. Designers
keep their layer names free; the slug-to-id mapping lives here in git.

Loaded once at bot boot (and re-loaded per node call — file is small, IO is
cheap). pydantic catches the obvious typos at load time.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class FigmaSlots(BaseModel):
    """Per-template slot node-ids inside the master frame."""

    slogan_text_id: str
    hero_image_id: str
    cta_text_id: str | None = None


class FigmaTemplate(BaseModel):
    """One Figma master frame + slot mapping for a single output format."""

    frame_id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    slots: FigmaSlots


class FigmaManifest(BaseModel):
    """Top-level: one Figma file, N format-slug → template mappings."""

    file_key: str
    templates: dict[str, FigmaTemplate]


def load_manifest(path: Path) -> FigmaManifest:
    """Read + validate the manifest JSON. Raises FileNotFoundError,
    json.JSONDecodeError, or pydantic.ValidationError."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return FigmaManifest.model_validate(raw)
