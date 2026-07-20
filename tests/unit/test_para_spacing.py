"""TextLayer.para_spacing (M4-web, 2026-07-20): Figma paragraph gaps.

The 1600x900 VK_AD title (node 3552:11191) is THREE Figma paragraphs with
10px spacing between them (leading-none pitch 50 + mb-[10px]). The composer
draws a uniform line pitch, so a blank wrapped line ("\n\n" in the source
text) normally eats a whole line_h. Contract:
  1. para_spacing=None (default): unchanged behaviour — a blank line advances
     the following line by a full line_h.
  2. para_spacing=G: blank lines collapse and instead add G px before the
     next paragraph's lines; non-blank line pitch stays line_h.
  3. Blank lines do not count against max_lines when para_spacing is set.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from infra.composer import compose
from infra.template_manifest import TemplateSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
_WHITE = (255, 255, 255)


def _spec(para_spacing: float | None, max_lines: int = 4) -> TemplateSpec:
    layer = {
        "type": "text",
        "name": "t",
        "slot": "title",
        "x": 0,
        "y": 0,
        "width": 400,
        "height": 300,
        "font_family": "SBSansDisplay",
        "font_weight": "Semibold",
        "font_size_max": 50,
        "line_height": 1.0,
        "color": "#FFFFFF",
        "align_h": "left",
        "align_v": "top",
        "max_lines": max_lines,
    }
    if para_spacing is not None:
        layer["para_spacing"] = para_spacing
    return TemplateSpec.model_validate(
        {"width": 400, "height": 300, "background_color": "#222222", "layers": [layer]}
    )


def _ink_rows(img: Image.Image) -> list[tuple[int, int]]:
    """y-bands of white ink."""
    a = np.asarray(img.convert("RGB"))
    m = (a[..., 0] > 200) & (a[..., 1] > 200) & (a[..., 2] > 200)
    rows = m.any(axis=1)
    bands, start = [], None
    for i, v in enumerate(rows):
        if v and start is None:
            start = i
        if not v and start is not None:
            bands.append((start, i))
            start = None
    if start is not None:
        bands.append((start, len(rows)))
    return bands


def test_default_blank_line_advances_full_pitch():
    img = compose(_spec(None), assets_root=REPO_ROOT, texts={"title": "Оба\n\nДва"})
    bands = _ink_rows(img)
    assert len(bands) == 2
    # second paragraph sits two pitches (100px) below the first line's band
    assert 95 <= bands[1][0] - bands[0][0] <= 105


def test_para_spacing_replaces_blank_line_with_gap():
    img = compose(_spec(10.0), assets_root=REPO_ROOT, texts={"title": "Оба\n\nДва"})
    bands = _ink_rows(img)
    assert len(bands) == 2
    # pitch 50 + gap 10 = 60 between the two cap tops
    assert 55 <= bands[1][0] - bands[0][0] <= 65


def test_para_spacing_keeps_plain_lines_at_line_pitch():
    img = compose(_spec(10.0), assets_root=REPO_ROOT, texts={"title": "Оба\nДва"})
    bands = _ink_rows(img)
    assert len(bands) == 2
    assert 45 <= bands[1][0] - bands[0][0] <= 55


def test_blank_lines_do_not_count_against_max_lines():
    # 3 visible lines + 2 blanks; max_lines=3 must still fit at full size
    img = compose(_spec(10.0, max_lines=3), assets_root=REPO_ROOT, texts={"title": "Оба\n\nДва\n\nТри"})
    bands = _ink_rows(img)
    assert len(bands) == 3
    assert 55 <= bands[1][0] - bands[0][0] <= 65
    assert 55 <= bands[2][0] - bands[1][0] <= 65
