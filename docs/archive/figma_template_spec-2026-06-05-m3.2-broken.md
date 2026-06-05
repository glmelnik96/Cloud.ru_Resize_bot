# Figma template spec (M3.2+)

> **STATUS 2026-06-05 — ARCHITECTURALLY BROKEN.** The MCP write flow this
> spec assumes (`upload_assets`, `use_figma`) does not exist on the local
> Desktop MCP and is closed to third-party bots on the cloud MCP. Every
> per-format call falls back to PIL. See [M3.2_BROKEN.md](./M3.2_BROKEN.md)
> for root cause and redesign options. Keeping this document for repo
> archaeology; do **not** treat it as current.

Resize_bot renders ads by reusing master frames the design team already builds
in Figma. Layer names stay free; the bot finds slots through a JSON manifest
in this repo.

## Adding a template

1. **Build the frame in Figma.** Use any layer naming. Required content:
   - One text node for the slogan.
   - One image-fillable node for the hero (rectangle, frame with image fill,
     etc — anything `upload_assets` can target).
   - Optionally one text node for the CTA (e.g. inside a button/tag rect).
2. **Pick a slug.** Format: `<channel>_<width>x<height>` is the convention but
   not enforced. Examples: `vk_post_1080x1080`, `tg_post_1080x1350`,
   `ig_story_1080x1920`. Whatever you pick, add it to the LLM whitelist in
   `prompts/parse_brief.md` and bump that prompt's version.
3. **Get node ids.** Easiest way: run
   `python scripts/figma_dump_structure.py --file-key <FK> --page-id <PAGE>`
   from a host with Figma Desktop running. Pick the slogan / hero / CTA ids
   from the dump.
4. **Edit `config\figma_templates.json`.** Add a `templates.<slug>` entry:

```json
"<slug>": {
  "frame_id": "3302:516",
  "width": 1080,
  "height": 1080,
  "slots": {
    "slogan_text_id": "3302:520",
    "hero_image_id": "3302:522",
    "cta_text_id": null
  }
}
```

   `cta_text_id` is the only optional slot — set to `null` if the template has
   no CTA region.

5. **Test.** `/new` with a brief that mentions the new slug → ZIP should
   contain the new format's PNG straight from Figma (not a PIL stub).

## Invariants

- One Figma file per manifest (`file_key`). All templates live in the same file.
- Slugs are unique within `templates`.
- `width`/`height` mirror the frame dimensions — they're used for the PIL
  fallback's canvas size when MCP can't be reached.
- The bot never edits Figma without a successful HITL hero approval upstream.

## Failure modes

- **MCP unreachable on boot** → `figma_mcp_unavailable` log; every format falls
  back to PIL.
- **MCP fails mid-render** → that one format falls back to PIL with
  `figma_format_fallback reason=mcp_error`; admin gets a TG ping (1h dedupe).
- **Slug missing from manifest** → that format falls back to PIL with
  `reason=manifest_miss`. Not an alert — this is a brief/whitelist drift bug.

## Old spec

The pre-M3.2 spec (which assumed `{{slogan}}/{{body}}/{{cta}}/{{hero_image}}`
layer naming) is archived at
`docs/archive/figma_template_spec-2026-06-04-pre-m3.2.md`.
