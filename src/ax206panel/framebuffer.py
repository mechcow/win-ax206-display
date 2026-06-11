"""RGB888 PIL image -> RGB565 for the AX206 blit data phase.

Byte order matches the _RGB565_0/_RGB565_1 macros in drv_dpf.c:149-150:

  byte 0 = (R & 0xf8)      | (G & 0xe0) >> 5
  byte 1 = (G & 0x1c) << 3 | (B & 0xf8) >> 3

i.e. the 16-bit value (R5 << 11 | G6 << 5 | B5) stored big-endian,
pixels in row-major order (left to right, top to bottom).

The array-level API (uint16 values in native order) exists so the app can
diff consecutive frames cheaply for partial-rect blits and skip
unchanged frames; convert to wire bytes only at the end.
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def image_to_rgb565_array(img: Image.Image) -> np.ndarray:
    """Convert a PIL image to a (height, width) uint16 RGB565 array."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.asarray(img, dtype=np.uint16)
    return (
        ((arr[..., 0] & 0xF8) << 8)   # R[7:3] -> bits 15..11
        | ((arr[..., 1] & 0xFC) << 3)  # G[7:2] -> bits 10..5
        | (arr[..., 2] >> 3)           # B[7:3] -> bits 4..0
    )


def rgb565_to_bytes(arr: np.ndarray) -> bytes:
    """Serialize an RGB565 array to wire bytes (big-endian per pixel)."""
    return np.ascontiguousarray(arr).astype(">u2").tobytes()


def image_to_rgb565(img: Image.Image) -> bytes:
    """Convert a PIL image to AX206 RGB565 bytes (2 bytes/pixel)."""
    return rgb565_to_bytes(image_to_rgb565_array(img))


def dirty_rect(prev: np.ndarray, cur: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding rect (x0, y0, x1, y1; x1/y1 exclusive) of changed pixels.

    Returns None when the frames are identical.
    """
    changed = prev != cur
    rows = changed.any(axis=1)
    if not rows.any():
        return None
    cols = changed.any(axis=0)
    row_idx = np.nonzero(rows)[0]
    col_idx = np.nonzero(cols)[0]
    return (int(col_idx[0]), int(row_idx[0]),
            int(col_idx[-1]) + 1, int(row_idx[-1]) + 1)
