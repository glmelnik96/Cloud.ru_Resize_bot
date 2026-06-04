"""Vendored copy of Phygital-bot {client, workflows, docs}.

Source: C:/Users/Глеб/Documents/Phygital-bot @ 2026-06-04 (commit unknown).
We copy minimal subset needed for brand text→image:
  - client/         (full)
  - workflows/      (base, brand_docs, brand_text2img, gemini_text, image_gen)
  - docs/           (Photo/Render/Isometric Enhancer + NanoBanana Scrubber)

The vendor dir is prepended to sys.path by infra.phygital_client.bootstrap()
so internal imports `from client.api import ...` / `from workflows.X import ...`
resolve transparently — no source edits to vendored files required.

Minimal vendor patches (kept under git, see file headers):
  - client/config.py: STORAGE_DIR env override (PHYGITAL_STORAGE_DIR), so
    session.json / brand_docs cache live in a docker volume, not in /app.
  - workflows/brand_docs.py: CACHE_FILE points at STORAGE_DIR not ROOT/storage.

Do NOT add new files here directly — keep this dir a clean vendor snapshot.
"""
