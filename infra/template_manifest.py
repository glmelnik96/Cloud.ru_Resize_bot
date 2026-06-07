"""Template manifest: JSON-on-disk -> typed pydantic models.

A template describes how to compose one output PNG from:
- a static brand-area-line PNG layer (designer-exported),
- a hero-image slot (filled at runtime with the user-uploaded PNG),
- N text slots (slogan, CTA, age rating) with optional per-line highlight
  or solid background.

Replaces the M3.2 figma_manifest.py (Figma node-ids). Now slot binding is
done by pixel geometry + font specs; runtime backend is PIL, not Figma MCP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class PerLineHighlight(BaseModel):
    """Rectangle drawn behind each text line, width follows the line itself."""

    color: str = Field(description="Fill color, e.g. '#222222'")
    padding_x: int = 0
    padding_y: int = 0
    radius: int = 0


class BoxBackground(BaseModel):
    """One rectangle filling the whole text-slot rect (CTA plate)."""

    color: str
    radius: int = 0


class ImageLayer(BaseModel):
    """Static PNG placed at (x, y) with explicit (width, height)."""

    type: Literal["image"]
    path: str = Field(description="Path relative to project root")
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    z: int = 0


class HeroLayer(BaseModel):
    """User-uploaded PNG drawn into a rect with fit policy."""

    type: Literal["hero"]
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fit: Literal["cover", "contain"] = "cover"
    z: int = 0


class TextLayer(BaseModel):
    """Auto-shrinking text in a rect, optional highlight backgrounds."""

    type: Literal["text"]
    slot: Literal["slogan", "cta", "age_rating"]
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    font_family: str
    font_weight: str = "Regular"
    font_size_max: int = Field(gt=0)
    font_size_min: int | None = Field(
        default=None,
        description="If None, no auto-shrink (single size).",
    )
    line_height: float = 1.0
    color: str
    align_h: Literal["left", "center", "right"] = "left"
    align_v: Literal["top", "middle", "bottom"] = "top"
    max_lines: int = Field(default=3, gt=0)
    per_line_highlight: PerLineHighlight | None = None
    background: BoxBackground | None = None
    padding_x: int = Field(
        default=0,
        description="Inner horizontal padding inside the layer rect — text is "
        "wrapped and aligned within (x+padding_x .. x+width-padding_x).",
    )
    padding_y: int = Field(
        default=0,
        description="Inner vertical padding inside the layer rect — text block "
        "is placed within (y+padding_y .. y+height-padding_y).",
    )
    z: int = 0


Layer = Annotated[ImageLayer | HeroLayer | TextLayer, Field(discriminator="type")]


class TemplateSpec(BaseModel):
    """One output format: canvas size + ordered layers."""

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    background_color: str = "#FFFFFF"
    layers: list[Layer]


class TemplateManifest(BaseModel):
    """Top-level manifest: version + slug -> template."""

    version: str
    comment: str | None = None
    templates: dict[str, TemplateSpec]


def load_manifest(path: Path) -> TemplateManifest:
    """Read + validate the manifest JSON. Raises FileNotFoundError,
    json.JSONDecodeError, or pydantic.ValidationError."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TemplateManifest.model_validate(raw)
