# Template spec — M3.3

> Replaces `docs/figma_template_spec.md` (archived under
> `docs/archive/figma_template_spec-2026-06-05-m3.2-broken.md`). The M3.2
> Figma MCP write-flow is dead — see
> `docs/archive/M3.2_BROKEN-2026-06-05.md` for why.

## What is a template

A template is a single banner layout described declaratively in
`config/templates.json`. Each template = canvas size + ordered list of
**layers**. At render time, `infra/composer.py` walks the layers and
draws each one onto an RGBA PIL canvas, then saves a PNG.

A template is identified by its **slug** (e.g. `banner_300x250`). Slugs
are the lingua franca between the wizard (`bot/wizard.py`), the brief
parser (`parse_brief`), and the renderer (`fill_templates_per_format`).

## Manifest shape

```json
{
  "version": "0.3.0",
  "comment": "free-form provenance note",
  "templates": {
    "<slug>": {
      "width": 300,
      "height": 250,
      "background_color": "#222222",
      "layers": [ ... ]
    }
  }
}
```

Loaded and validated by `infra.template_manifest.load_manifest()`. The
discriminator on `layers[].type` picks one of three Pydantic models —
`ImageLayer`, `HeroLayer`, `TextLayer`.

## Layer types

### `image`

Static decorative asset (currently: brand area line at the top of each
banner). `path` is resolved relative to the project root.

```json
{
  "type": "image",
  "path": "assets/brand/brand_area_line_300x37_v1.png",
  "x": 0, "y": 0,
  "width": 300, "height": 37,
  "z": 20
}
```

### `hero`

The user-uploaded PNG. `fit`:
- `cover` — scale to max(target/src) then center-crop. Fills the rect
  fully, may lose edges.
- `contain` — scale to min(target/src) then center. Letterbox on
  shorter axis using the canvas background color.

```json
{
  "type": "hero",
  "x": 0, "y": 37,
  "width": 300, "height": 188,
  "fit": "cover",
  "z": 10
}
```

There is exactly **one** hero layer per template. It receives the
`hero` argument to `compose()` (bytes / Path / PIL.Image).

### `text`

A slot driven by the winning copy. `slot` is one of:
- `slogan` — winner.slogan from the persona-loop,
- `cta` — winner.cta,
- `age_rating` — brief.age_rating (`"0+"` by default, configurable in
  the wizard).

```json
{
  "type": "text",
  "slot": "slogan",
  "x": 16, "y": 240,
  "width": 268, "height": 60,
  "font_family": "SBSansDisplay",
  "font_weight": "Semibold",
  "font_size_max": 24,
  "font_size_min": 16,
  "line_height": 1.0,
  "color": "#FFFFFF",
  "align_h": "left", "align_v": "top",
  "max_lines": 3,
  "per_line_highlight": {
    "color": "#CFF500", "padding_x": 4, "padding_y": 0
  },
  "z": 30
}
```

Auto-shrink: composer tries `font_size_max` first and steps down by 1
until either (a) the text wraps within `width` × `max_lines` and the
block fits `height`, or (b) `font_size_min` is hit. If neither is
true, the smallest size is used and overflowing lines are truncated.

Optional decorations:
- `per_line_highlight` — colored rect behind each non-empty line
  (Cloud.ru "lemon highlight" signature). Rect height uses real font
  metrics (`ascent + descent`) so descenders are covered.
- `background` — single colored rect across the whole `x,y,width,height`
  rect (used for CTA buttons, e.g. lemon background + dark text).

## Z-order

`compose()` sorts layers by `z` (ascending), then by declaration order
within the same `z`. Standard pattern per banner:

| z   | layer                             |
|-----|-----------------------------------|
| 10  | hero (covered by overlays above)  |
| 20  | brand_area_line (top strip image) |
| 30  | slogan text                       |
| 40  | cta text + background             |
| 50  | age_rating text                   |

This is a convention, not enforced — set z however the design requires.

## Fonts

All text uses `assets/fonts/SBSansDisplay-<Weight>.otf` (Cloud.ru
brand). Weights present: Light / Regular / Medium / Semibold / Bold.
`composer._font_path()` raises `FileNotFoundError` if a layer asks for
a weight that is not on disk.

## Adding a new banner slug

1. Decide canvas size and background color.
2. Pick layer geometry from the Figma master frame
   (`get_design_context` against Desktop MCP works for *reading* —
   that's still fine).
3. Export the brand_area_line strip from Figma as PNG into
   `assets/brand/brand_area_line_<W>x<H>.png`.
4. Append a new entry under `templates.<slug>` in
   `config/templates.json`.
5. Add `<slug>` to the wizard's format whitelist in `bot/wizard.py`
   AND to the slug whitelist in `prompts/parse_brief.md` (so the
   parser maps user-typed format names to the new slug).
6. Add the slug to `tests/unit/test_composer.py::test_compose_real_template_smoke`'s
   parametrize list — end-to-end smoke catches missing assets.

## What the composer does NOT do

- No drop shadow / blur / outline on text (no use case yet).
- No gradient layers — backgrounds are flat colors only.
- No multi-hero compositions — one user upload, one hero slot.
- No remote IO — fonts, brand strips, and the hero are read from
  disk / arguments.

## Related files

- `config/templates.json` — manifest data.
- `infra/template_manifest.py` — Pydantic schema + loader.
- `infra/composer.py` — render engine.
- `graph/nodes/fill_templates_per_format.py` — the node that loops
  over `brief.formats` and calls `compose()` per slug.
- `tests/unit/test_template_manifest.py` / `test_composer.py` /
  `test_fill_templates_node.py` — coverage.
