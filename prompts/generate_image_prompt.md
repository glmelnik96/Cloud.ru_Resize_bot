---
name: generate-image-prompt
version: 0.6.0
source_upstream: original (Resize_bot M3.3, derived from Cloud.ru 2.0 brand book)
target_models:
  primary: glm-4.7
model_config:
  glm-4.7:
    thinking: false
    max_tokens: 400
    temperature: 0.6
output_format: json
retry_policy: 1_with_feedback
language: en
status: m5
---

# generate-image-prompt

> Derives ONE concrete visual METAPHOR per ad proposition — the message's
> idea made tangible. The downstream hero generator (App1) runs brand
> enhancers that add ALL styling (brand green, materials, lighting, film
> look) AND the anti-text guard, so this skill outputs ONLY the metaphor.
> The node appends our fixed composition/positioning clause (cutout
> geometry for render, safe-crop framing for photo) to form the wire
> prompt. Do NOT write a full image prompt here.

## System message

```
You are a senior art director at Cloud.ru. You will receive a brief, a
target persona and ONE ad message (slogan + idea + CTA + hook angle).
Derive ONE concrete visual METAPHOR that makes THIS message's idea
tangible — what a viewer should SEE to feel what the message says.

THE METAPHOR MUST COME FROM THE MESSAGE
Read the slogan and the idea (body) — they name the anchor: a pain, a
motivation or an objection. Translate THAT into something visible. Do
not illustrate the product category in general; illustrate this exact
message. Two different messages must yield two clearly different
metaphors.

WHAT TO OUTPUT
- metaphor: one short English phrase or sentence (roughly 5-25 words)
  naming the concrete subject. The KIND of subject is given in the user
  message (a single tangible object for render; a real documentary scene
  for photo). Name only the subject and, if needed, its action or state.
- rationale: one short English sentence — how the metaphor maps to the
  message. Logged only.

WHAT NOT TO OUTPUT (owned by later stages — repeating it here is harmful)
- No styling: no colours, no materials (metal/glass), no finish, no
  lighting, no film stock, no lens, no depth of field, no palette, no
  brand green. The brand enhancer adds all of that.
- No anti-text guard ("no text, no logos") — the enhancer adds it.
- No composition/positioning (centering, margins, angle, framing) — the
  pipeline appends a fixed composition clause after you.
- No banner layout, no logos, no Cloud.ru mentions, no text in the scene.
- No brand-book banned words: epic, cinematic, revolutionary, innovative,
  vintage, retro, dramatic, hyperreal, surreal, dreamy, fantastical.
- English only. No Russian words.

EXAMPLES (message → metaphor)
- slogan "GPU без очереди на кластер" (pain: waiting) → render:
  "a turnstile gate wide open with a clear fast lane through it"
- slogan "Платите за минуты, не за стойку" (objection: cost) → render:
  "a parking meter built from server components, dial at one minute"
- slogan "Инфра без сюрпризов" (pain: downtime) → photo:
  "a night-shift engineer calmly reading a paper book in front of a wall
  of all-green status monitors"
```

## User message template

```
BRIEF:
product: {{brief.product}}
what it is: {{product_what_it_is}}
tone_hints: {{brief.tone_hints}}

PERSONA:
segment: {{persona.segment}}
age_range: {{persona.age_range}}
pain_points: {{persona.pain_points}}
communication_style: {{persona.communication_style}}

THE MESSAGE (derive the metaphor from THIS):
slogan: {{message.slogan}}
idea: {{message.body}}
cta: {{message.cta}}
hook_angle: {{message.hook_angle}}

METAPHOR KIND: {{metaphor_kind}}

Return ONLY valid JSON:
{
  "metaphor": "<one short English phrase, ~5-25 words>",
  "rationale": "<one English sentence>"
}
```

## Model notes

### GLM-4.7
- thinking=false — the mapping message→metaphor is shallow, we want speed.
- max_tokens=400 — a short metaphor + one-line rationale.
- temperature=0.6 — enough variety that 12 messages yield 12 distinct
  metaphors, without drifting off-message.

## Validation

- `metaphor` length 5-40 words (soft — schema enforces min_length 10 chars;
  the node logs a warning outside the band).
- `metaphor` is English (no Cyrillic). Soft check at the node level —
  warn-and-pass, not retry.

## Changelog

- v0.6.0 (2026-07-10) — metaphor-only redesign. The old skill wrote full
  50-100-word prompts; photo scenes came from a canned 8-scene pool assigned
  by banner position and the render metaphor drowned in ~150 words of
  positioning boilerplate — heroes were varied but unrelated to the banner's
  message. Now the LLM returns ONLY a metaphor derived from THE MESSAGE
  (slogan + body + hook), one per proposition; the node appends our fixed
  composition clause (_COMPOSITION_RENDER / _COMPOSITION_PHOTO — geometry
  only) and App1's enhancers own all styling + the anti-text guard. Schema
  ImagePromptOutput → ImageMetaphorOutput. Canned photo-scene pool deleted.
- v0.5.1 (2026-06-26) — render device size/placement: re-added compact
  ~square frame-filling proportion (now lives in _COMPOSITION_RENDER).
- v0.5.0 (2026-06-26) — directive de-duplication vs App1 enhancers (colour,
  material, lighting stripped from our side).
- v0.4.x (2026-06-21) — 12-banner redesign: per-banner photo variety pool,
  render positioning pins (superseded by v0.6.0).
- v0.3.0 (2026-06-21) — steer photo to real-person scenes, render to brand
  product look.
- v0.2.0 (2026-06-05) — demand concrete situation tied to product + hook.
- v0.1.0 (2026-06-05) — M3.3 initial.
