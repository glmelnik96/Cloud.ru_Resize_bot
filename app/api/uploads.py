"""Multipart upload guard shared by the webinar + HITL image endpoints.

Every hero upload passes through :func:`read_image_upload`, which bounds the
bytes pulled into memory, rejects non-image payloads, and blocks
decompression-bomb dimensions — all at the request boundary, before anything
touches disk or PIL's full decoder.
"""
from __future__ import annotations

from io import BytesIO

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

# Header dimensions above this are almost certainly a decompression bomb rather
# than a real photo; PIL reads size from the header without decoding pixels.
_MAX_PIXELS = 50_000_000  # 50 megapixels


async def read_image_upload(
    file: UploadFile | None, *, max_bytes: int, required: bool = True
) -> bytes:
    """Read + validate an uploaded image, returning its bytes.

    Reads at most ``max_bytes + 1`` so an oversized upload never fully lands in
    memory. Raises ``HTTPException`` (413 too large / 422 empty·invalid·missing)
    on any failure. Returns ``b""`` when ``file`` is None and ``required`` is
    False (the visual variant may generate its hero instead of uploading).
    """
    if file is None:
        if required:
            raise HTTPException(422, "no file provided")
        return b""
    data = await file.read(max_bytes + 1)
    if not data:
        raise HTTPException(422, "empty file")
    if len(data) > max_bytes:
        raise HTTPException(413, f"file too large (max {max_bytes} bytes)")
    try:
        with Image.open(BytesIO(data)) as im:
            w, h = im.size
            if w * h > _MAX_PIXELS:
                raise HTTPException(422, "image dimensions too large")
            im.verify()  # structural check without decoding pixels
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(422, "invalid image file") from exc
    return data
