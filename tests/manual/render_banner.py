"""Render every format of a /banner scenario for visual QA.

Reads config/banner_templates.json, composes each format of the chosen
scenario with a sample hero + sample slot texts, and writes PNGs to
tests/manual/_banner_out/.

Background plates (assets/brand/webinar/<slug>_bg.png) are exported by
the designer from Figma. If a plate is missing this helper skips that
image layer (drawing on the flat background_color) and prints what is
still needed — so hero/title geometry can be eyeballed before the plates
land.

Usage:
  python -m tests.manual.render_banner            # webinar_visual
  python -m tests.manual.render_banner <scenario>
"""

from __future__ import annotations

import sys
from pathlib import Path

from infra.composer import compose
from infra.template_manifest import ImageLayer, TemplateSpec, load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "config" / "banner_templates.json"
HERO = PROJECT_ROOT / "assets" / "brand" / "webinar" / (
    "15f59d08d1e109a385252756b3116f581817f6d4.png"
)
OUT_DIR = Path(__file__).resolve().parent / "_banner_out"
_DATE = "2 июля"
_TIME = "11:00"
TEXTS = {
    "title": "Эволюция приложения в облаке: как настроить кеш с Redis и ничего не сломать",
    "subtitle": "и зарабатывать до 20% на рекомендациях сервисов Cloud.ru",
    "date": _DATE,
    "time": _TIME,
    "datetime": f"{_DATE} в {_TIME}",
    "speaker_name": "Артемий Мазаев",
    "speaker_role": "Менеджер продукта, Cloud.ru",
    "speaker": "Артемий Мазаев\nМенеджер продукта, Cloud.ru",
}


def _strip_missing_plates(spec: TemplateSpec) -> tuple[TemplateSpec, list[str]]:
    """Return a copy of spec with image layers whose file is missing
    removed, plus the list of missing paths."""
    missing: list[str] = []
    kept = []
    for layer in spec.layers:
        if isinstance(layer, ImageLayer):
            if not (PROJECT_ROOT / layer.path).exists():
                missing.append(layer.path)
                continue
        kept.append(layer)
    return spec.model_copy(update={"layers": kept}), missing


def main() -> None:
    scenario_id = sys.argv[1] if len(sys.argv) > 1 else "webinar_visual"
    manifest = load_manifest(MANIFEST)
    scenario = manifest.scenarios[scenario_id]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_missing: set[str] = set()
    for slug in scenario.formats:
        spec = manifest.templates[slug]
        spec, missing = _strip_missing_plates(spec)
        all_missing.update(missing)
        img = compose(spec, hero=HERO, texts=TEXTS, assets_root=PROJECT_ROOT, slug=slug)
        out_path = OUT_DIR / f"{slug}.png"
        img.save(out_path, "PNG")
        flag = "  (plate MISSING)" if missing else ""
        print(f"wrote {out_path.name} ({spec.width}x{spec.height}){flag}")

    if all_missing:
        print("\nPlates still needed (export from Figma):")
        for p in sorted(all_missing):
            print(f"  {p}")


if __name__ == "__main__":
    main()
