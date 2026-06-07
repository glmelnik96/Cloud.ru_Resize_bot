"""Regenerate the last manual-test render against the current templates.json.

Reads:
  /data/heroes/<last hero>.jpg
Writes:
  /data/renders/_verify_<slug>.png

Usage (inside docker):
  python -m tests.manual.regen_last_render

This is a development helper only — keep it out of the unit suite.
"""

from __future__ import annotations

from pathlib import Path

from infra.composer import compose
from infra.template_manifest import load_manifest

HERO = Path("/data/heroes/27fb7d4be0744a3a9f55c89cfcaade38_20260605T124207.jpg")
TEXTS = {
    "slogan": "Terraform, SDK и API без костылей",
    "cta": "Оценить",
    "age_rating": "0+",
}
OUT_DIR = Path("/data/renders")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    manifest = load_manifest(PROJECT_ROOT / "config" / "templates.json")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, spec in manifest.templates.items():
        img = compose(spec, hero=HERO, texts=TEXTS, assets_root=PROJECT_ROOT)
        out_path = OUT_DIR / f"_verify_{slug}.png"
        img.save(out_path, "PNG")
        print(f"wrote {out_path} ({spec.width}x{spec.height})")


if __name__ == "__main__":
    main()
