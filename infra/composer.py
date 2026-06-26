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

import structlog
from PIL import Image, ImageDraw, ImageFont

from infra.template_manifest import (
    FrameLayer,
    GradientLayer,
    HeroCutoutLayer,
    HeroLayer,
    ImageLayer,
    PatternDotsLayer,
    TemplateSpec,
    TextLayer,
)

log = structlog.get_logger(__name__)


# Fonts live in assets/fonts/ as SBSansDisplay-<Weight>.otf
_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

_ELLIPSIS = "\u2026"
_NBSP = "\u00a0"

# Minimum background gap kept between consecutive per-line highlight plates so
# they never merge into one solid block when line_height is tight.
_MIN_PLATE_GAP = 2

# Russian prepositions / conjunctions / particles that must never end a line
# ("висячие предлоги"). They are glued to the FOLLOWING word with a
# non-breaking space so the wrapper keeps them together. Any 1-letter word is
# glued too (covers "и", "а", "в", "с", "к", "о", "у", "я").
_GLUE_WORDS = frozenset(
    """
    и а но да или либо то же бы ли не ни
    в во с со к ко о об обо у из изо от ото до по на за над под при про
    для без перед через между около кроме среди вокруг ради сквозь
    что как так уже еще ещё вот лишь даже чем тем под
    """.split()
)


def _apply_orphan_glue(text: str) -> str:
    """Glue short prepositions/conjunctions (and any 1-letter word) to the next
    word with a non-breaking space so they never get stranded at a line end."""
    out_lines: list[str] = []
    for paragraph in text.split("\n"):
        tokens = paragraph.split()
        if not tokens:
            out_lines.append("")
            continue
        glued: list[str] = []
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            is_glue = (len(tok) == 1 and tok.isalpha()) or tok.lower() in _GLUE_WORDS
            if is_glue and i + 1 < n:
                glued.append(tok + _NBSP + tokens[i + 1])
                i += 2
            else:
                glued.append(tok)
                i += 1
        out_lines.append(" ".join(glued))
    return "\n".join(out_lines)


def _font_path(family: str, weight: str) -> Path:
    """Map (family, weight) -> font file. Raises FileNotFoundError if missing."""
    # Family is "SBSansDisplay"; weight one of Regular/Semibold/Bold/Medium/Light.
    path = _FONT_DIR / f"{family}-{weight}.otf"
    if not path.is_file():
        raise FileNotFoundError(f"Font not found: {path}")
    return path


def _text_width(
    font: ImageFont.FreeTypeFont, text: str, tracking: float = 0.0
) -> float:
    """Rendered width of ``text`` including letter spacing. With tracking=0 this
    is PIL's native ``getlength`` (kerning preserved); otherwise we add
    ``tracking`` px after every glyph except the last, matching the per-glyph
    advance used when drawing tracked text."""
    if not text:
        return 0.0
    w = font.getlength(text)
    if tracking:
        w += tracking * (len(text) - 1)
    return w


def _break_long_word(
    word: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    tracking: float = 0.0,
) -> list[str]:
    """Hard-break a single word that is wider than max_width at the character
    level so it never overflows the layer rect (long URLs, compound nouns,
    glued-together words). Returns >=1 chunks, each <= max_width (except a lone
    over-wide character, which is unavoidable)."""
    chunks: list[str] = []
    cur = ""
    for ch in word:
        if not cur or _text_width(font, cur + ch, tracking) <= max_width:
            cur += ch
        else:
            chunks.append(cur)
            cur = ch
    if cur:
        chunks.append(cur)
    return chunks


def _wrap_to_width(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    tracking: float = 0.0,
) -> list[str]:
    """Greedy word-wrap. Respects existing newlines; words wider than
    max_width are hard-broken at the character level so text never spills
    outside the rect."""
    out: list[str] = []
    for paragraph in text.split("\n"):
        # Split on regular spaces only; non-breaking spaces (orphan glue) stay
        # inside their word so glued prepositions never wrap to a line end.
        words = [w for w in paragraph.split(" ") if w]
        if not words:
            out.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if _text_width(font, candidate, tracking) <= max_width:
                line = candidate
                continue
            # word doesn't fit on the current line
            if line:
                out.append(line)
                line = ""
            if _text_width(font, word, tracking) <= max_width:
                line = word
            else:
                # word itself is wider than the rect — hard-break it
                parts = _break_long_word(word, font, max_width, tracking)
                out.extend(parts[:-1])
                line = parts[-1]
        out.append(line)
    return out


def _ellipsize(
    line: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    tracking: float = 0.0,
) -> str:
    """Trim the line's tail so that line + ellipsis fits max_width."""
    if _text_width(font, line + _ELLIPSIS, tracking) <= max_width:
        return line + _ELLIPSIS
    trimmed = line
    while trimmed:
        trimmed = trimmed[:-1].rstrip()
        if _text_width(font, trimmed + _ELLIPSIS, tracking) <= max_width:
            return trimmed + _ELLIPSIS
    return _ELLIPSIS


def _fit_text(
    text: str,
    *,
    layer: TextLayer,
) -> tuple[ImageFont.FreeTypeFont, list[str], int, bool]:
    """Try font_size_max..font_size_min, return (font, lines, used_size, truncated).

    Returns the largest size at which text fits both width (via wrap) and
    max_lines. If even font_size_min doesn't fit, returns min anyway, the
    lines list is clipped to max_lines with the last line ellipsized, and
    truncated=True.
    """
    fp = _font_path(layer.font_family, layer.font_weight)
    text = _apply_orphan_glue(text)
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
    last_size = sizes[-1]
    for size in sizes:
        font = ImageFont.truetype(str(fp), size=size)
        tracking = layer.letter_spacing * size
        lines = _wrap_to_width(text, font, wrap_width, tracking)
        last_font = font
        last_lines = lines
        last_size = size
        if len(lines) <= layer.max_lines:
            # Also need vertical fit
            line_h = size * layer.line_height
            if line_h * len(lines) <= inner_h + 1:
                return font, lines, size, False
    # Did not fit fully; return smallest we tried, clipped lines with an
    # ellipsis on the last visible line instead of silently dropping text.
    assert last_font is not None
    truncated = len(last_lines) > layer.max_lines
    clipped = last_lines[: layer.max_lines]
    if truncated and clipped:
        clipped[-1] = _ellipsize(
            clipped[-1], last_font, wrap_width, layer.letter_spacing * last_size
        )
    return last_font, clipped, sizes[-1], truncated


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


# Background-removal tools (App1/Photoroom) leave faint near-transparent stray
# pixels around the cutout. `Image.getbbox()` counts any alpha>0 pixel as
# content, so that halo blows the crop box out toward the whole frame and the
# real object scales down and floats. Crop on alpha>=this threshold instead so
# only the solid object drives the scale; the real object's anti-aliased edge is
# alpha ~100-255, well above this, so legitimate detail is never clipped.
_ALPHA_CROP_THRESHOLD = 32


def _content_bbox(hero: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of the solid cutout, ignoring a faint bg-removal halo.

    For images with an alpha channel we threshold the alpha so near-transparent
    stray pixels don't inflate the box; opaque images fall back to getbbox()."""
    if "A" in hero.getbands():
        alpha = hero.getchannel("A")
        mask = alpha.point(lambda v: 255 if v >= _ALPHA_CROP_THRESHOLD else 0)
        return mask.getbbox()
    return hero.getbbox()


def _draw_hero_cutout_layer(
    canvas: Image.Image,
    layer: HeroCutoutLayer,
    *,
    hero: Image.Image,
) -> None:
    """Scale the alpha cutout into the rect and alpha-composite it.

    fit="contain": whole cutout fits inside the rect (may leave margins).
    fit="cover": cutout fills the rect; overflow is clipped against the rect
    edges (anchor decides which side bleeds off). Cover keeps the green panel
    full like the Figma mockups instead of floating a small object in voids.

    Background-removed heroes (App1 render scenario + Photoroom) arrive as a
    full-frame PNG that is mostly transparent padding with the real object
    somewhere inside. We first crop to the alpha bounding box so the *visible
    object* — not the arbitrary padding the cutout tool left around it — drives
    scaling and anchoring. Without this, identical objects render at wildly
    different sizes and float at different heights depending on how much empty
    space the cutter happened to leave. A fully transparent hero (no bbox) is a
    no-op.
    """
    bbox = _content_bbox(hero)
    if bbox is None:
        return
    if bbox != (0, 0, hero.width, hero.height):
        hero = hero.crop(bbox)

    target_w, target_h = layer.width, layer.height
    src_w, src_h = hero.size
    if layer.fit == "cover":
        scale = max(target_w / src_w, target_h / src_h)
    else:
        scale = min(target_w / src_w, target_h / src_h)
    if scale > 1.0 and not layer.allow_upscale:
        scale = 1.0
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    resized = (
        hero
        if (new_w, new_h) == (src_w, src_h)
        else hero.resize((new_w, new_h), Image.LANCZOS)
    )

    # Position the (possibly oversized) cutout within the rect by anchor.
    if layer.anchor_h == "left":
        x = layer.x
    elif layer.anchor_h == "center":
        x = layer.x + (target_w - new_w) // 2
    else:  # right
        x = layer.x + target_w - new_w

    if layer.anchor_v == "top":
        y = layer.y
    elif layer.anchor_v == "middle":
        y = layer.y + (target_h - new_h) // 2
    else:  # bottom
        y = layer.y + target_h - new_h

    if layer.fit == "cover":
        # Clip the cutout to the rect so overflow doesn't paint over other
        # zones (logo, text, neighbouring panels).
        crop_l = max(0, layer.x - x)
        crop_t = max(0, layer.y - y)
        crop_r = min(new_w, layer.x + target_w - x)
        crop_b = min(new_h, layer.y + target_h - y)
        if crop_r <= crop_l or crop_b <= crop_t:
            return
        if (crop_l, crop_t, crop_r, crop_b) != (0, 0, new_w, new_h):
            resized = resized.crop((crop_l, crop_t, crop_r, crop_b))
            x += crop_l
            y += crop_t

    canvas.alpha_composite(resized, (x, y))


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _draw_pattern_dots(canvas: Image.Image, layer: PatternDotsLayer) -> None:
    """Tile a dot grid over the rect, alpha-composited so it stays subtle."""
    overlay = Image.new("RGBA", (layer.width, layer.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fill = (*_hex_rgb(layer.color), layer.alpha)
    d = layer.dot_size
    y = 0
    while y < layer.height:
        x = 0
        while x < layer.width:
            draw.rectangle((x, y, x + d - 1, y + d - 1), fill=fill)
            x += layer.spacing_x
        y += layer.spacing_y
    canvas.alpha_composite(overlay, (layer.x, layer.y))


def _draw_frame_layer(canvas: Image.Image, layer: FrameLayer) -> None:
    """Draw a solid border of `thickness` around the layer rect; interior is
    left untouched so the body / hero show through."""
    t = layer.thickness
    x0, y0 = layer.x, layer.y
    x1, y1 = layer.x + layer.width, layer.y + layer.height
    draw = ImageDraw.Draw(canvas)
    # four filled bars (top, bottom, left, right)
    draw.rectangle((x0, y0, x1, y0 + t), fill=layer.color)            # top
    draw.rectangle((x0, y1 - t, x1, y1), fill=layer.color)            # bottom
    draw.rectangle((x0, y0, x0 + t, y1), fill=layer.color)            # left
    draw.rectangle((x1 - t, y0, x1, y1), fill=layer.color)            # right
    # corner tabs that thicken the frame so it reads 'broken'/stepped
    for tab in layer.tabs:
        draw.rectangle(
            (tab.x, tab.y, tab.x + tab.width, tab.y + tab.height), fill=layer.color
        )


def _draw_gradient_layer(canvas: Image.Image, layer: GradientLayer) -> None:
    """Composite a linear single-colour alpha gradient over the rect."""
    w, h = layer.width, layer.height
    base = Image.new("RGBA", (w, h), layer.color)
    span = (h if layer.direction == "vertical" else w) - 1 or 1
    mask = Image.new("L", (w, h))
    px = mask.load()
    for i in range(layer.height if layer.direction == "vertical" else layer.width):
        a = round(layer.from_alpha + (layer.to_alpha - layer.from_alpha) * i / span)
        if layer.direction == "vertical":
            for x in range(w):
                px[x, i] = a
        else:
            for y in range(h):
                px[i, y] = a
    base.putalpha(mask)
    canvas.alpha_composite(base, (layer.x, layer.y))


def _draw_text_layer(canvas: Image.Image, layer: TextLayer, text: str) -> bool:
    """Draw one text layer. Returns True if the text was truncated."""
    if not text:
        return False
    font, lines, used_size, truncated = _fit_text(text, layer=layer)
    tracking = layer.letter_spacing * used_size
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

    # full-rect background (CTA plate / slogan plate / outlined badge)
    if layer.background is not None:
        bg = layer.background
        box = (layer.x, layer.y, layer.x + layer.width, layer.y + layer.height)
        outline = bg.border_color if (bg.border_color and bg.border_width) else None
        width = bg.border_width if outline else 1
        if bg.radius > 0:
            draw.rounded_rectangle(
                box, radius=bg.radius, fill=bg.color, outline=outline, width=width
            )
        else:
            draw.rectangle(box, fill=bg.color, outline=outline, width=width)

    # vertical placement within inner box
    if layer.align_v == "top":
        y0 = inner_y0
    elif layer.align_v == "middle":
        # Center on the real glyph INK, not the font's design box. PIL's top
        # anchor places y at the ascender line, leaving the font's internal top
        # leading above the ink (and the descent below it) uncounted; centering
        # on `block_h` alone drifts the visible text low (CTA plate looked
        # bottom-heavy). Measure the ink span of the actual lines and center it.
        inked = [ln for ln in lines if ln.strip()]
        if inked:
            first_top = min(font.getbbox(ln)[1] for ln in inked)
            last_bottom = max(font.getbbox(ln)[3] for ln in inked)
        else:
            first_top, last_bottom = 0, text_h
        ink_h = (len(lines) - 1) * line_h + (last_bottom - first_top)
        y0 = inner_y0 + (inner_h - ink_h) / 2 - first_top
    else:  # bottom
        y0 = inner_y0 + inner_h - block_h

    # Precompute per-line geometry so we can draw ALL plates first and ALL
    # text afterwards. Otherwise, when `line_height < 1` (or descenders push
    # plates to overlap), plate N+1 paints over line N's already-drawn text
    # and chops the bottom off the previous line's glyphs.
    line_geom: list[tuple[float, float, str]] = []
    for i, line in enumerate(lines):
        line_w = _text_width(font, line, tracking)
        if layer.align_h == "left":
            x = inner_x0
        elif layer.align_h == "center":
            x = inner_x0 + (inner_w - line_w) // 2
        else:  # right
            x = inner_x0 + inner_w - line_w
        y = y0 + i * line_h
        line_geom.append((x, y, line))

    # Pass 1: all per-line highlight plates (+ optional underline). Drawn on a
    # transparent overlay so a sub-255 alpha lets the photo show through.
    if layer.per_line_highlight is not None:
        h = layer.per_line_highlight
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        plate_fill = (*_hex_rgb(h.color), h.alpha)

        # Plate bottom, clamped so a plate never reaches into the next line's
        # plate: with a tight line_height the full-metrics plate height
        # (text_h + 2*padding_y) can exceed the line pitch and adjacent green
        # plates merge into one solid block. Cap the bottom edge so at least
        # _MIN_PLATE_GAP px of background survive between consecutive plates.
        def _plate_bottom(i: int, y: float) -> float:
            full = y + text_h + h.padding_y
            if i + 1 < len(line_geom):
                next_top = line_geom[i + 1][1] - h.padding_y
                return min(full, next_top - _MIN_PLATE_GAP)
            return full

        # Sub-pass A: all plates.
        for i, (x, y, line) in enumerate(line_geom):
            if not line.strip():
                continue
            line_w = _text_width(font, line, tracking)
            box = (
                x - h.padding_x,
                y - h.padding_y,
                x + line_w + h.padding_x,
                _plate_bottom(i, y),
            )
            if h.radius > 0:
                odraw.rounded_rectangle(box, radius=h.radius, fill=plate_fill)
            else:
                odraw.rectangle(box, fill=plate_fill)
        # Sub-pass B: all underlines, drawn after every plate so a following
        # line's plate can never paint over the previous line's underline.
        if h.underline_color and h.underline_height > 0:
            for x, y, line in line_geom:
                if not line.strip():
                    continue
                line_w = _text_width(font, line, tracking)
                uy0 = y + text_h + h.padding_y + h.underline_gap
                odraw.rectangle(
                    (
                        x - h.padding_x,
                        uy0,
                        x + line_w + h.padding_x,
                        uy0 + h.underline_height,
                    ),
                    fill=_hex_rgb(h.underline_color),
                )
        canvas.alpha_composite(overlay)

    # Pass 2: all text glyphs (on top of any plates). With letter spacing we
    # advance glyph-by-glyph (PIL has no native tracking); otherwise draw the
    # whole line at once so kerning is preserved byte-for-byte.
    for x, y, line in line_geom:
        if tracking:
            cx = x
            for ch in line:
                draw.text((cx, y), ch, font=font, fill=layer.color)
                cx += font.getlength(ch) + tracking
        else:
            draw.text((x, y), line, font=font, fill=layer.color)

    return truncated


def compose(
    spec: TemplateSpec,
    *,
    hero: Image.Image | Path | bytes | None = None,
    texts: dict[str, str],
    assets_root: Path,
    slug: str | None = None,
) -> Image.Image:
    """Render one PNG from spec + hero + slot texts.

    Args:
        spec: parsed TemplateSpec.
        hero: PIL Image, path to file, or raw bytes (PNG/JPEG). May be
            None for templates without hero/hero_cutout layers (M4
            text-only covers); a hero layer with hero=None raises.
        texts: dict mapping slot name (slogan/cta/age_rating in M3,
            title/date/speaker_* in M4) to string.
        assets_root: project root (used to resolve ImageLayer.path).
        slug: optional format slug, used only in text_truncated logging.

    Returns:
        PIL.Image.Image in RGBA mode, caller is responsible for saving.
    """
    if hero is None:
        hero_img = None
    elif isinstance(hero, Path):
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
        elif isinstance(layer, PatternDotsLayer):
            _draw_pattern_dots(canvas, layer)
        elif isinstance(layer, FrameLayer):
            _draw_frame_layer(canvas, layer)
        elif isinstance(layer, GradientLayer):
            _draw_gradient_layer(canvas, layer)
        elif isinstance(layer, (HeroLayer, HeroCutoutLayer)):
            if hero_img is None:
                raise ValueError(
                    f"spec ({slug or 'unknown'}) has a {layer.type} layer "
                    "but compose() was called with hero=None"
                )
            if isinstance(layer, HeroLayer):
                _draw_hero_layer(canvas, layer, hero=hero_img)
            else:
                _draw_hero_cutout_layer(canvas, layer, hero=hero_img)
        elif isinstance(layer, TextLayer):
            if layer.fixed_content is not None:
                text = layer.fixed_content
            else:
                text = texts.get(layer.slot or "", "")
            if _draw_text_layer(canvas, layer, text):
                log.warning(
                    "text_truncated",
                    slug=slug,
                    slot=layer.slot,
                    format_size=f"{spec.width}x{spec.height}",
                )

    return canvas
