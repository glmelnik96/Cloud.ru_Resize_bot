"""Validate text-fill across every webinar_visual format with short / medium /
long / extreme titles, using a real generated cutout. One contact sheet per
length tier so layout regressions (overflow, collisions, tiny text) are visible
at a glance.

Usage: python -m tests.manual.validate_text_fill
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image, ImageDraw

from infra.banner_render import load_banner_manifest, render_scenario

ROOT = Path(__file__).resolve().parents[2]
HEROES = ROOT / "tests" / "manual" / "_heroes"
OUT = ROOT / "tests" / "manual" / "_banner_out"

TITLES = {
    "short": "Облако Cloud.ru",
    "medium": "5 уязвимостей в вашем Kubernetes",
    "long": "Как настроить кеш с Redis в облаке и ничего не сломать на проде",
    "xlong": (
        "Полный практический разбор построения отказоустойчивой "
        "мультиоблачной архитектуры с нуля для крупного энтерпрайза"
    ),
}


def _sheet(files, scenario_prefix: str, out_name: str) -> None:
    cols = 4
    cellw = 300
    pad = 6
    thumbs = []
    for rec in sorted(files, key=lambda r: r["format"]):
        nm = rec["format"].replace(scenario_prefix, "")
        im = Image.open(rec["path"]).convert("RGBA")
        s = cellw / im.size[0]
        thumbs.append((nm, im.resize((cellw, max(1, int(im.size[1] * s))))))
    rows = (len(thumbs) + cols - 1) // cols
    rowh = [0] * rows
    for i, (_, im) in enumerate(thumbs):
        rowh[i // cols] = max(rowh[i // cols], im.size[1])
    W = cols * (cellw + pad) + pad
    H = sum(rowh) + rows * 16 + pad
    sheet = Image.new("RGBA", (W, H), (40, 40, 40, 255))
    d = ImageDraw.Draw(sheet)
    y = pad
    for r in range(rows):
        x = pad
        for c in range(cols):
            i = r * cols + c
            if i >= len(thumbs):
                break
            nm, im = thumbs[i]
            d.text((x, y), nm, fill=(255, 255, 0, 255))
            sheet.alpha_composite(im, (x, y + 14))
            x += cellw + pad
        y += rowh[r] + 16
    sheet.convert("RGB").save(OUT / out_name, quality=82)
    print(f"wrote {out_name} ({len(thumbs)} formats)")


async def main() -> None:
    m = load_banner_manifest()
    for tier, title in TITLES.items():
        texts = {"title": title, "date": "2 июля", "time": "11:00",
                 "speaker_name": "Артемий Мазаев",
                 "speaker_role": "Менеджер продукта, Cloud.ru",
                 "subtitle": "и зарабатывать до 20% на рекомендациях сервисов Cloud.ru"}
        files = await render_scenario(
            scenario_id="webinar_visual", hero=HEROES / "visual_device.png",
            texts=texts, out_dir=OUT / f"_val_{tier}", session_id=f"val_{tier}", manifest=m,
        )
        _sheet(files, "webinar_visual_", f"_val_visual_{tier}.jpg")


if __name__ == "__main__":
    asyncio.run(main())
