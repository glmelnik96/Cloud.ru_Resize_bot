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
      - "stretch": scale the bbox-cropped cutout to the rect exactly
        (no crop, no margins, aspect NOT preserved) — M4 visual metaphors
        are placed whole with a mild non-uniform squish in the mockups.
      - "crop": replicate a Figma image-fill CROP transform: the full file
        frame (no alpha-bbox pre-crop) is scaled uniformly so its width is
        ``crop_scale`` × rect width, placed at (``crop_left``, ``crop_top``)
        × rect size, and clipped to the rect. ``flip_h`` mirrors the result
        AFTER cropping (Figma flips the placed rect, not the file).
    """

    type: Literal["hero_cutout"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fit: Literal["cover", "contain", "stretch", "crop"] = "contain"
    crop_scale: float = Field(
        default=1.0,
        gt=0,
        description="fit='crop' only: rendered image width / rect width "
        "(Figma fill transform w%).",
    )
    crop_scale_h: float | None = Field(
        default=None,
        gt=0,
        description="fit='crop' only: rendered image height / rect height "
        "(Figma fill transform h%). None -> uniform scale from crop_scale "
        "and the file aspect; set explicitly when the Figma crop stretches.",
    )
    crop_left: float = Field(
        default=0.0,
        description="fit='crop' only: image left offset as a fraction of "
        "rect width (Figma fill transform left%, usually negative).",
    )
    crop_top: float = Field(
        default=0.0,
        description="fit='crop' only: image top offset as a fraction of "
        "rect height (Figma fill transform top%, usually negative).",
    )
    anchor_h: Literal["left", "center", "right"] = "center"
    anchor_v: Literal["top", "middle", "bottom"] = "bottom"
    allow_upscale: bool = Field(
        default=True,
        description="If False, a cutout smaller than the rect is not "
        "enlarged (avoids blurring small portraits).",
    )
    flip_h: bool = Field(
        default=False,
        description="Mirror the cutout horizontally before fit/anchor "
        "(Figma mockups that place the portrait fill with -scale-x-100).",
    )
    z: int = 0


class RectLayer(BaseModel):
    """Bare filled (optionally rounded, optionally semi-transparent) rectangle.

    The webinar banners rest on the Cloud.ru "stepped" green silhouette — a main
    panel plus offset accent tabs — none of which carries text of its own, so a
    TextLayer (which bails on empty text) can't draw it. This is brand furniture:
    it belongs to the ``hero`` layer group (drawn behind the message/text).
    """

    type: Literal["rect"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    color: str = Field(description="Fill color, e.g. '#26D07C'")
    radius: int = Field(default=0, ge=0)
    alpha: int = Field(default=255, ge=0, le=255)
    stroke_color: str | None = Field(
        default=None, description="Outline color; None draws no border"
    )
    stroke_width: int = Field(default=0, ge=0)
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


class TextureLayer(BaseModel):
    """Tiled Cloud.ru "arrow-grid" motif over a rect (the darker line texture on
    the green webinar panels). One cell (period ``cell`` px) draws two strokes,
    lifted verbatim from the Figma pattern (node 3520:12943, viewBox 50.9068):

      - a diagonal line  (0.162, 39.158) -> (33.121, 5.710)
      - an L-bracket     (39.159, 39.159) -> (39.159, 0) -> (0, 0)

    The cell is tiled to fill (width, height) at native size and clipped to the
    rect, so non-square regions keep square cells (unlike a scaled ImageLayer).
    Brand furniture -> ``hero`` layer group.
    """

    type: Literal["texture"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    cell: float = Field(
        default=66.1788,
        gt=0,
        description="Tile period in px (Figma patternTransform scale).",
    )
    color: str = "#159A57"
    alpha: int = Field(default=255, ge=0, le=255)
    stroke: float | None = Field(
        default=None,
        description="Stroke width in px. None -> derived from cell "
        "(1.5 units of the 50.9068 viewBox).",
    )
    offset_x: float = 0.0
    offset_y: float = 0.0
    z: int = 0


class VLinesLayer(BaseModel):
    """A row of evenly-spaced thin vertical lines across a band (the Cloud.ru
    top brand bar's grey "tick" ruler between the logo and the tagline, Figma
    node 3517:12138). Lines are ``line_width`` px wide, ``color``, repeated every
    ``period`` px starting at the band's left edge, each spanning the band height
    and clipped to the band rect. Static brand furniture -> ``brand`` group."""

    type: Literal["vlines"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    color: str = "#646464"
    line_width: float = Field(default=1.286, gt=0)
    period: float = Field(default=9.7554, gt=0)
    alpha: int = Field(default=255, ge=0, le=255)
    z: int = 0


class InlineTextRun(BaseModel):
    """One text segment in an InlineRowLayer. ``slot`` pulls runtime text;
    ``fixed_content`` bakes a literal (e.g. the "в" joiner). ``color`` overrides
    the row default for this run (two-tone headers). ``gap_before`` overrides
    the row ``gap`` for the space preceding THIS run (Figma headers pad the
    inline arrows wider than the word gaps)."""

    kind: Literal["text"] = "text"
    slot: str | None = None
    fixed_content: str | None = None
    color: str | None = None
    gap_before: int | None = Field(default=None, ge=0)


class InlineImageRun(BaseModel):
    """One inline PNG (e.g. the ↗ arrow) placed on the row baseline."""

    kind: Literal["image"] = "image"
    path: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    gap_before: int | None = Field(default=None, ge=0)


InlineRun = Annotated[InlineTextRun | InlineImageRun, Field(discriminator="kind")]


class InlineRowLayer(BaseModel):
    """A single-baseline horizontal flow of text + image runs.

    The second 1080x1080 header is ``Вебинар ↗ 30 июля в 11:00 ↗`` — mixed
    colours, two inline arrows, and date+time joined by "в" on one line. The
    plain TextLayer can't flow images or per-run colours mid-line, so this lays
    runs out left-to-right (``gap`` px between them), measures the total, and
    aligns the block within the rect. Text runs share the row font; each run may
    override colour. Runs with an empty slot are skipped (the row still flows)."""

    type: Literal["inline_row"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    font_family: str
    font_weight: str = "Regular"
    font_size: int = Field(gt=0)
    color: str = "#FFFFFF"
    align_h: Literal["left", "center", "right"] = "left"
    align_v: Literal["top", "middle", "bottom"] = "middle"
    gap: int = Field(default=8, ge=0)
    runs: list[InlineRun] = Field(min_length=1)
    z: int = 0


class FrameTab(BaseModel):
    """A solid-colour rectangle painted in the frame colour, on top of the thin
    border. Used to thicken specific corners so the frame reads 'broken'/stepped
    (the Cloud.ru render banner has a thick top-left and bottom-right tab)."""

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class FrameLayer(BaseModel):
    """Solid-colour border drawn around a rect (interior untouched).

    The Cloud.ru creatives 300x600 render banner frames the dark body with a
    10px green border. ``thickness`` is the border width in px; the interior
    (rect inset by thickness) is left transparent so the body / hero show
    through. ``tabs`` are extra filled rectangles in the frame colour that
    thicken chosen corners — the reference frame is not an even rectangle but
    has a heavy top-left and bottom-right tab ('broken'/stepped look).
    """

    type: Literal["frame"]
    name: str | None = None
    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    thickness: int = Field(gt=0)
    color: str = Field(description="Border fill, e.g. '#26D07C'")
    tabs: list[FrameTab] = Field(default_factory=list)
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
    para_spacing: float | None = Field(
        default=None,
        description="Extra vertical gap in px inserted where the source text "
        "contains a blank line (\\n\\n paragraph break), replacing the blank "
        "line instead of spending a full line_height pitch on it. Matches "
        "Figma paragraph-spacing (e.g. VK_AD title mb-[10px]). None keeps "
        "the legacy behaviour (blank line = full pitch).",
    )
    letter_spacing: float = Field(
        default=0.0,
        description="Tracking as a fraction of the font size (em), matching "
        "Figma's percentage (e.g. Figma -4% -> -0.04). Positive spreads "
        "glyphs apart, negative pulls them together. 0.0 keeps PIL's native "
        "kerning (no per-glyph drawing).",
    )
    color: str
    accent_color: str | None = Field(
        default=None,
        description="Second colour for a two-tone title. When set and the text "
        "contains ':', glyphs up to and including the first colon use ``color`` "
        "and the remainder uses ``accent_color`` (the dark webinar formats draw "
        "the product name white + the tagline green). No colon → single colour.",
    )
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
    | RectLayer
    | TextureLayer
    | PatternDotsLayer
    | VLinesLayer
    | FrameLayer
    | GradientLayer
    | InlineRowLayer
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
