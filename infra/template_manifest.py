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
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


class PerLineHighlight(BaseModel):
    """Rectangle drawn behind each text line, width follows the line itself.

    Optionally an underline stroke is drawn under each line (the photo banner
    slogan has a green plate + a white underline). ``alpha`` lets the plate be
    semi-transparent so the photo shows through (alpha-composited, not a flat
    fill)."""

    color: str = Field(description="Fill color, e.g. '#222222'")
    padding_x: int = 0
    padding_y: int = 0
    radius: int = 0
    alpha: int = Field(default=255, ge=0, le=255)
    underline_color: str | None = None
    underline_height: int = Field(default=0, ge=0)
    underline_gap: int = Field(
        default=0,
        description="Vertical gap between the glyph baseline box and the "
        "underline stroke.",
    )


class BoxBackground(BaseModel):
    """One rectangle filling the whole text-slot rect (CTA plate / badge).

    ``color`` is the fill; set it to None for an outline-only badge (the
    webinar "Вебинар" pill is a 2px border with no fill). ``border_color`` +
    ``border_width`` draw a stroke on top of (or instead of) the fill.
    """

    color: str | None = None
    radius: int = 0
    border_color: str | None = None
    border_width: int = 0

    @model_validator(mode="after")
    def _fill_or_border(self) -> "BoxBackground":
        if self.color is None and not (self.border_color and self.border_width):
            raise ValueError("BoxBackground needs a fill color or a border")
        return self


class ImageLayer(BaseModel):
    """Static PNG placed at (x, y) with explicit (width, height)."""

    type: Literal["image"]
    name: str | None = Field(
        default=None,
        description="Optional layer id used by scenario variant overrides.",
    )
    path: str = Field(description="Path relative to project root")
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    z: int = 0


class HeroLayer(BaseModel):
    """User-uploaded PNG drawn into a rect with fit policy."""

    type: Literal["hero"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fit: Literal["cover", "contain"] = "cover"
    z: int = 0


class HeroCutoutLayer(BaseModel):
    """Alpha-cutout hero (background removed) composited into a rect.

    M4: webinar mockups place the cutout (speaker / 3D object) on the
    green panel. The cutout is scaled into the rect and anchored, then
    alpha-composited so transparency survives.

    ``fit`` controls scaling:
      - "contain": scale to fit entirely inside the rect (no clipping,
        may leave empty margins) — good for portraits that must stay whole.
      - "cover": scale to fill the rect (clips overflow against the rect
        edges) — fills the panel like the Figma mockups, no green voids.
    """

    type: Literal["hero_cutout"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fit: Literal["cover", "contain"] = "contain"
    anchor_h: Literal["left", "center", "right"] = "center"
    anchor_v: Literal["top", "middle", "bottom"] = "bottom"
    allow_upscale: bool = Field(
        default=True,
        description="If False, a cutout smaller than the rect is not "
        "enlarged (avoids blurring small portraits).",
    )
    z: int = 0


class PatternDotsLayer(BaseModel):
    """Tiled dot grid drawn over a rect (the render banner's subtle texture on
    the dark body). Dots are ``dot_size`` px squares spaced ``spacing_x`` /
    ``spacing_y`` apart, in ``color`` at ``alpha`` opacity."""

    type: Literal["pattern_dots"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    color: str = "#3D3D3D"
    dot_size: int = Field(default=2, gt=0)
    spacing_x: int = Field(default=11, gt=0)
    spacing_y: int = Field(default=10, gt=0)
    alpha: int = Field(default=255, ge=0, le=255)
    z: int = 0


class FrameLayer(BaseModel):
    """Solid-colour border drawn around a rect (interior untouched).

    The Cloud.ru creatives 300x600 render banner frames the dark body with a
    10px green border. ``thickness`` is the border width in px; the interior
    (rect inset by thickness) is left transparent so the body / hero show
    through.
    """

    type: Literal["frame"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    thickness: int = Field(gt=0)
    color: str = Field(description="Border fill, e.g. '#26D07C'")
    z: int = 0


class GradientLayer(BaseModel):
    """Linear alpha gradient of a single colour, composited as a legibility
    scrim over the hero (e.g. transparent at top -> dark at bottom so white
    headline text stays readable on a full-bleed photo).
    """

    type: Literal["gradient"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    color: str = "#000000"
    from_alpha: int = Field(default=0, ge=0, le=255)
    to_alpha: int = Field(default=255, ge=0, le=255)
    direction: Literal["vertical", "horizontal"] = "vertical"
    z: int = 0


class TextLayer(BaseModel):
    """Auto-shrinking text in a rect, optional highlight backgrounds.

    Either ``slot`` (runtime text from the texts dict) or
    ``fixed_content`` (label baked into the template, e.g. per-format
    button captions in M4 webinar banners) must be set.
    """

    type: Literal["text"]
    name: str | None = None
    slot: str | None = Field(
        default=None,
        description="Slot key looked up in the runtime texts dict "
        "(slogan/cta/age_rating in M3, title/date/speaker_* in M4).",
    )
    fixed_content: str | None = Field(
        default=None,
        description="Baked-in text; takes precedence over slot.",
    )
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

    @model_validator(mode="after")
    def _slot_or_fixed(self) -> "TextLayer":
        if self.slot is None and self.fixed_content is None:
            raise ValueError("text layer needs either slot or fixed_content")
        return self


Layer = Annotated[
    ImageLayer
    | HeroLayer
    | HeroCutoutLayer
    | PatternDotsLayer
    | FrameLayer
    | GradientLayer
    | TextLayer,
    Field(discriminator="type"),
]


class TemplateSpec(BaseModel):
    """One output format: canvas size + ordered layers."""

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    background_color: str = "#FFFFFF"
    layers: list[Layer]


class HeroPolicy(BaseModel):
    """How a scenario obtains its hero image."""

    source: Literal["generate", "upload", "both"] = "both"
    remove_bg: bool = Field(
        default=False,
        description="Run the hero through Phygital Remove Background "
        "(b2b removebg / rmbg=1) before composing.",
    )


class VariantSpec(BaseModel):
    """Named color/composition tweak inside a scenario.

    ``overrides`` maps a layer ``name`` to a partial layer dict that is
    deep-merged into every layer with that name across the scenario's
    formats (e.g. swap accent strip color or pattern asset path).
    """

    id: str
    title: str | None = None
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ScenarioSpec(BaseModel):
    """One /banner scenario: formats + wizard slots + hero policy + variants."""

    title: str
    formats: list[str] = Field(min_length=1)
    slots: list[str] = Field(
        default_factory=list,
        description="Slot keys the wizard must collect (title, date, ...).",
    )
    hero: HeroPolicy | None = Field(
        default=None,
        description="None means the scenario composes without a hero "
        "(e.g. text-only TG cover).",
    )
    variants: list[VariantSpec] = Field(default_factory=list)


class TemplateManifest(BaseModel):
    """Top-level manifest: version + slug -> template (+ M4 scenarios)."""

    version: str
    comment: str | None = None
    templates: dict[str, TemplateSpec]
    scenarios: dict[str, ScenarioSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _scenario_formats_exist(self) -> "TemplateManifest":
        for sid, sc in self.scenarios.items():
            unknown = [f for f in sc.formats if f not in self.templates]
            if unknown:
                raise ValueError(
                    f"scenario {sid!r} references unknown formats: {unknown}"
                )
        return self


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_variant(spec: TemplateSpec, variant: VariantSpec) -> TemplateSpec:
    """Return a new TemplateSpec with the variant's overrides merged into
    every layer whose ``name`` appears in ``variant.overrides``. Layers
    without a matching name are kept as-is. The result is re-validated, so
    a bad override fails loudly instead of producing a broken render."""
    if not variant.overrides:
        return spec
    raw = spec.model_dump()
    for layer in raw["layers"]:
        patch = variant.overrides.get(layer.get("name") or "")
        if patch:
            layer.update(_deep_merge(layer, patch))
    return TemplateSpec.model_validate(raw)


def load_manifest(path: Path) -> TemplateManifest:
    """Read + validate the manifest JSON. Raises FileNotFoundError,
    json.JSONDecodeError, or pydantic.ValidationError."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TemplateManifest.model_validate(raw)
