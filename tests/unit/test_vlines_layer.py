"""VLinesLayer (M4-web landscapes, 2026-07-17): the Cloud.ru top brand bar
carries a row of evenly-spaced thin vertical grey ticks between the logo and the
"облачные и ИИ-сервисы" tagline (Figma node 3517:12138, ~80 lines, period
9.7554px, width 1.286px, colour #646464). It tiles vertical lines across a band
at a float period, clipped to the band rect."""

from __future__ import annotations

from PIL import Image

from infra.composer import compose
from infra.template_manifest import TemplateSpec


def _spec(layers):
    return TemplateSpec.model_validate(
        {"width": 200, "height": 100, "background_color": "#222222", "layers": layers}
    )


def test_vlines_tiles_at_period_and_color():
    spec = _spec(
        [
            {
                "type": "vlines",
                "x": 20,
                "y": 30,
                "width": 100,
                "height": 40,
                "color": "#646464",
                "line_width": 2,
                "period": 10.0,
                "z": 1,
            }
        ]
    )
    img = compose(spec, hero=None, texts={}, assets_root=__import__("pathlib").Path("."))
    px = img.load()
    # first line at band origin x=20
    assert px[20, 50][:3] == (0x64, 0x64, 0x64)
    # gap between lines is dark background
    assert px[25, 50][:3] == (0x22, 0x22, 0x22)
    # next line one period over (x=30)
    assert px[30, 50][:3] == (0x64, 0x64, 0x64)
    # a line near the far end (period 10 -> x=110 within width 100 band, so last at 110? clipped; check x=110 is last inside 20+100=120)
    assert px[110, 50][:3] == (0x64, 0x64, 0x64)
    # above the band (y<30) is untouched dark
    assert px[20, 20][:3] == (0x22, 0x22, 0x22)


def test_vlines_belongs_to_brand_group():
    """The ticks are static brand furniture, so a brand-only artifact draws
    them (and hero-only leaves them out)."""
    spec = _spec(
        [
            {
                "type": "vlines",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 40,
                "color": "#646464",
                "line_width": 2,
                "period": 10.0,
                "z": 1,
            }
        ]
    )
    brand = compose(spec, hero=None, texts={}, assets_root=__import__("pathlib").Path("."), only={"brand"})
    assert brand.load()[0, 10][3] == 0xFF
    hero = compose(spec, hero=None, texts={}, assets_root=__import__("pathlib").Path("."), only={"hero"})
    assert hero.load()[0, 10][3] == 0
