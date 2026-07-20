"""infra.hero_fit (M4-web fit engine, 2026-07-21): manual hero framing.

User photos and App1 renders come in arbitrary sizes/paddings, so the web UI
lets the user drag/zoom the image inside a fixed reference frame (рамка with
grid + head line). The chosen placement is a transform in reference-frame
units; the server bakes it into a reference-size RGBA "framed hero" that the
composer consumes verbatim (speaker formats via compose(hero_prefit=True),
visual formats via the file-space fit:crop math).

Contract:
  1. Transform(scale, x, y): hero pixel * scale = ref units, hero top-left is
     pasted at (x, y) in ref coords; canvas is transparent outside the photo.
  2. bake_frame(hero, ref_w, ref_h, t) renders exactly that, clipped.
  3. auto_transform(hero, box, mode, anchor_v) places the hero's ALPHA bbox
     into the target box (contain = fit fully, cover = fill), centered
     horizontally; anchor_v picks top or center vertically. It is the server
     default the UI starts from.
  4. Round-trip: bake_frame(auto_transform(contain)) puts the opaque object
     exactly into the box.
  5. Reference constants: SPEAKER_FRAME (window of the Figma reference
     speaker bbox 1494x2669), VISUAL_FRAME 1024 square + VISUAL_BOX (blade
     metaphor bbox 211,157,813,887).
"""

from __future__ import annotations

from PIL import Image

from infra.hero_fit import (
    SPEAKER_FRAME,
    VISUAL_BOX,
    VISUAL_FRAME,
    Transform,
    auto_transform,
    bake_frame,
)


def _red(px) -> bool:
    """LANCZOS-tolerant probe for the opaque red object."""
    r, g, b = px[:3]
    return abs(r - 200) <= 10 and abs(g - 30) <= 10 and abs(b - 30) <= 10 and px[3] > 200


def _hero_with_bbox() -> Image.Image:
    """100x100 transparent image with an opaque red 20x40 box at (10, 10)."""
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (20, 40), (200, 30, 30, 255)), (10, 10))
    return im


def _opaque_bbox(out: Image.Image) -> tuple[int, int, int, int]:
    """Half-alpha bbox: LANCZOS upscale smears the edge over ~3*scale px, but
    the 50% alpha contour stays within ~2px of the true object edge."""
    mask = out.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    return mask.getbbox()


def _close(bbox, expected, tol=3):
    return all(abs(a - b) <= tol for a, b in zip(bbox, expected))


def test_reference_constants():
    assert SPEAKER_FRAME == (1494, 2669)
    assert VISUAL_FRAME == (1024, 1024)
    assert VISUAL_BOX == (211, 157, 813, 887)


def test_bake_frame_scales_and_offsets():
    hero = Image.new("RGBA", (100, 200), (200, 30, 30, 255))
    out = bake_frame(hero, 500, 600, Transform(scale=2.0, x=10, y=20))
    assert out.size == (500, 600)
    assert out.getpixel((11, 21))[:3] == (200, 30, 30)
    assert out.getpixel((209, 419))[:3] == (200, 30, 30)
    # outside the pasted photo the canvas is transparent
    assert out.getpixel((5, 5))[3] == 0
    assert out.getpixel((250, 450))[3] == 0


def test_bake_frame_clips_offcanvas():
    hero = Image.new("RGBA", (100, 100), (30, 30, 200, 255))
    out = bake_frame(hero, 50, 50, Transform(scale=1.0, x=-60, y=-60))
    assert out.size == (50, 50)
    # only the bottom-right 40x40 of the hero lands on canvas
    assert out.getpixel((10, 10))[:3] == (30, 30, 200)
    assert out.getpixel((45, 45))[3] == 0


def test_auto_transform_contain_centers_bbox_in_box():
    t = auto_transform(_hero_with_bbox(), (0, 0, 200, 200), mode="contain")
    out = bake_frame(_hero_with_bbox(), 200, 200, t)
    # bbox 20x40 -> scale 5 -> 100x200; centered horizontally: x 50..150, y 0..200
    assert _red(out.getpixel((100, 100)))
    assert _close(_opaque_bbox(out), (50, 0, 150, 200))


def test_auto_transform_cover_anchor_top_fills_box():
    t = auto_transform(_hero_with_bbox(), (0, 0, 200, 200), mode="cover", anchor_v="top")
    out = bake_frame(_hero_with_bbox(), 200, 200, t)
    # bbox scaled x10 -> 200x400 anchored top: canvas fully covered
    assert _red(out.getpixel((100, 100)))
    assert _opaque_bbox(out) == (0, 0, 200, 200)


def test_auto_transform_respects_box_origin():
    t = auto_transform(_hero_with_bbox(), (50, 100, 150, 300), mode="contain")
    out = bake_frame(_hero_with_bbox(), 200, 400, t)
    # box 100x200, bbox ratio 1:2 -> object fills the box exactly
    assert _red(out.getpixel((100, 200)))
    assert _close(_opaque_bbox(out), (50, 100, 150, 300))
