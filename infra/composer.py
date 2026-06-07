"""PIL composer: TemplateSpec + hero PNG + text dict -> output PNG.

Replaces the M3.2 Figma MCP rendering pipeline. Composition strategy:
- create canvas at (width, height) filled with background_color,
- sort layers by z (stable),
- for each layer: image / hero / text -> draw on canvas,
- return PIL.Image.Image (caller writes to disk / sends to TG).

The composer is pure: no IO outside reading static asset files declared
in the manifest. Hero comes from caller as either a Path or bytes.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from infra.template_manifest import (
    HeroLayer,
    ImageLayer,
    TemplateSpec,
    TextLayer,
)


# Fonts live in assets/fonts/ as SBSansDisplay-<Weight>.otf
_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def _font_path(family: str, weight: str) -> Path:
    """Map (family, weight) -> font file. Raises FileNotFoundError if missing."""
    # Family is "SBSansDisplay"; weight one of Regular/Semibold/Bold/Medium/Light.
    path = _FONT_DIR / f"{family}-{weight}.otf"
    if not path.is_file():
        raise FileNotFoundError(f"Font not found: {path}")
    return path


def _wrap_to_width(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Greedy word-wrap. Respects existing newlines in text."""
    out: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            out.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if font.getlength(candidate) <= max_width:
                line = candidate
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def _fit_text(
    text: str,
    *,
    layer: TextLayer,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Try font_size_max..font_size_min, return (font, lines, used_size).

    Returns the largest size at which text fits both width (via wrap) and
    max_lines. If even font_size_min doesn't fit, returns min anyway and
    the lines list may be truncated to max_lines (caller renders as-is).
    """
    fp = _font_path(layer.font_family, layer.font_weight)
    sizes = (
        range(layer.font_size_max, (layer.font_size_min or layer.font_size_max) - 1, -1)
        if layer.font_size_min
        else [layer.font_size_max]
    )
    # Text is wrapped within the inner padding of the layer (and, if a
    # per-line highlight is also drawn, further inset by the highlight's
    # padding so the plate cannot spill outside).
    h_pad = layer.per_line_highlight.padding_x if layer.per_line_highlight else 0
    wrap_width = max(1, layer.width - 2 * layer.padding_x - 2 * h_pad)
    inner_h = max(1, layer.height - 2 * layer.padding_y)
    last_font = None
    last_lines: list[str] = []
    for size in sizes:
        font = ImageFont.truetype(str(fp), size=size)
        lines = _wrap_to_width(text, font, wrap_width)
        last_font = font
        last_lines = lines
        if len(lines) <= layer.max_lines:
            # Also need vertical fit
            line_h = size * layer.line_height
            if line_h * len(lines) <= inner_h + 1:
                return font, lines, size
    # Did not fit fully; return smallest we tried, clipped lines.
    assert last_font is not None
    return last_font, last_lines[: layer.max_lines], sizes[-1]


def _draw_image_layer(
    canvas: Image.Image,
    layer: ImageLayer,
    *,
    assets_root: Path,
) -> None:
    src_path = assets_root / layer.path
    img = Image.open(src_path).convert("RGBA")
    if img.size != (layer.width, layer.height):
        img = img.resize((layer.width, layer.height), Image.LANCZOS)
    canvas.alpha_composite(img, (layer.x, layer.y))


def _draw_hero_layer(
    canvas: Image.Image,
    layer: HeroLayer,
    *,
    hero: Image.Image,
) -> None:
    target_w, target_h = layer.width, layer.height
    src_w, src_h = hero.size
    if layer.fit == "cover":
        scale = max(target_w / src_w, target_h / src_h)
        new_w, new_h = round(src_w * scale), round(src_h * scale)
        resized = hero.resize((new_w, new_h), Image.LANCZOS)
        # center-crop
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        cropped = resized.crop((left, top, left + target_w, top + target_h))
        canvas.paste(cropped, (layer.x, layer.y))
    else:  # contain
        scale = min(target_w / src_w, target_h / src_h)
        new_w, new_h = round(src_w * scale), round(src_h * scale)
        resized = hero.resize((new_w, new_h), Image.LANCZOS)
        x = layer.x + (target_w - new_w) // 2
        y = layer.y + (target_h - new_h) // 2
        canvas.paste(resized, (x, y))


def _draw_text_layer(canvas: Image.Image, layer: TextLayer, text: str) -> None:
    if not text:
        return
    font, lines, used_size = _fit_text(text, layer=layer)
    draw = ImageDraw.Draw(canvas)
    # Use real font metrics so highlight covers descenders (р, д, у, etc).
    ascent, descent = font.getmetrics()
    text_h = ascent + descent
    line_h = used_size * layer.line_height
    block_h = line_h * len(lines)

    # Inner content box, inset by padding.
    inner_x0 = layer.x + layer.padding_x
    inner_y0 = layer.y + layer.padding_y
    inner_w = layer.width - 2 * layer.padding_x
    inner_h = layer.height - 2 * layer.padding_y

    # full-rect background (CTA plate / slogan plate)
    if layer.background is not None:
        bg = layer.background
        box = (layer.x, layer.y, layer.x + layer.width, layer.y + layer.height)
        if bg.radius > 0:
            draw.rounded_rectangle(box, radius=bg.radius, fill=bg.color)
        else:
            draw.rectangle(box, fill=bg.color)

    # vertical placement within inner box
    if layer.align_v == "top":
        y0 = inner_y0
    elif layer.align_v == "middle":
        y0 = inner_y0 + (inner_h - block_h) // 2
    else:  # bottom
        y0 = inner_y0 + inner_h - block_h

    # Precompute per-line geometry so we can draw ALL plates first and ALL
    # text afterwards. Otherwise, when `line_height < 1` (or descenders push
    # plates to overlap), plate N+1 paints over line N's already-drawn text
    # and chops the bottom off the previous line's glyphs.
    line_geom: list[tuple[float, float, str]] = []
    for i, line in enumerate(lines):
        line_w = font.getlength(line)
        if layer.align_h == "left":
            x = inner_x0
        elif layer.align_h == "center":
            x = inner_x0 + (inner_w - line_w) // 2
        else:  # right
            x = inner_x0 + inner_w - line_w
        y = y0 + i * line_h
        line_geom.append((x, y, line))

    # Pass 1: all per-line highlight plates.
    if layer.per_line_highlight is not None:
        h = layer.per_line_highlight
        for x, y, line in line_geom:
            if not line.strip():
                continue
            line_w = font.getlength(line)
            box = (
                x - h.padding_x,
                y - h.padding_y,
                x + line_w + h.padding_x,
                y + text_h + h.padding_y,
            )
            if h.radius > 0:
                draw.rounded_rectangle(box, radius=h.radius, fill=h.color)
            else:
                draw.rectangle(box, fill=h.color)

    # Pass 2: all text glyphs (on top of any plates).
    for x, y, line in line_geom:
        draw.text((x, y), line, font=font, fill=layer.color)


def compose(
    spec: TemplateSpec,
    *,
    hero: Image.Image | Path | bytes,
    texts: dict[str, str],
    assets_root: Path,
) -> Image.Image:
    """Render one PNG from spec + hero + slot texts.

    Args:
        spec: parsed TemplateSpec.
        hero: PIL Image, path to file, or raw bytes (PNG/JPEG).
        texts: dict mapping slot name (slogan/cta/age_rating) to string.
        assets_root: project root (used to resolve ImageLayer.path).

    Returns:
        PIL.Image.Image in RGBA mode, caller is responsible for saving.
    """
    if isinstance(hero, Path):
        hero_img = Image.open(hero).convert("RGBA")
    elif isinstance(hero, (bytes, bytearray)):
        hero_img = Image.open(io.BytesIO(hero)).convert("RGBA")
    else:
        hero_img = hero.convert("RGBA") if hero.mode != "RGBA" else hero

    canvas = Image.new("RGBA", (spec.width, spec.height), spec.background_color)

    # Stable sort by z; preserve declaration order for equal z.
    layers = sorted(enumerate(spec.layers), key=lambda iv: (iv[1].z, iv[0]))

    for _, layer in layers:
        if isinstance(layer, ImageLayer):
            _draw_image_layer(canvas, layer, assets_root=assets_root)
        elif isinstance(layer, HeroLayer):
            _draw_hero_layer(canvas, layer, hero=hero_img)
        elif isinstance(layer, TextLayer):
            text = texts.get(layer.slot, "")
            _draw_text_layer(canvas, layer, text)

    return canvas
