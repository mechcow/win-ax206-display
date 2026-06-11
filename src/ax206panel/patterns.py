"""Test patterns for validating blit, byte order and orientation (M2).

Layout (any resolution; the panel reports its own via get LCD params):
  - top half: 8 colour bars, left to right:
    white, yellow, cyan, green, magenta, red, blue, black.
    Wrong RGB565 byte order or R/B swap is obvious here (e.g. red and
    blue trade places, yellow turns cyan).
  - bottom half: 4 horizontal ramps (R, G, B, grey), dark left -> bright
    right. Banding or vertical tearing here means stride/width bugs.
  - white triangle + "TOP LEFT" label in the top-left corner and a
    "BOTTOM RIGHT" label bottom-right, so rotation/flip is unambiguous.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

# Standard colour-bar order, white at the far left
BAR_COLORS = [
    (255, 255, 255),
    (255, 255, 0),
    (0, 255, 255),
    (0, 255, 0),
    (255, 0, 255),
    (255, 0, 0),
    (0, 0, 255),
    (0, 0, 0),
]

RAMP_CHANNELS = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)]


def make_base_pattern(width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Colour bars in the top half
    bars_h = height // 2
    for i, color in enumerate(BAR_COLORS):
        x0 = i * width // len(BAR_COLORS)
        x1 = (i + 1) * width // len(BAR_COLORS)
        draw.rectangle((x0, 0, x1 - 1, bars_h - 1), fill=color)

    # Gradient ramps in the bottom half
    ramp = (np.arange(width, dtype=np.uint32) * 255 // max(1, width - 1)).astype(np.uint8)
    n = len(RAMP_CHANNELS)
    for row, mask in enumerate(RAMP_CHANNELS):
        y0 = bars_h + row * (height - bars_h) // n
        y1 = bars_h + (row + 1) * (height - bars_h) // n
        strip = np.zeros((y1 - y0, width, 3), dtype=np.uint8)
        for ch in range(3):
            if mask[ch]:
                strip[..., ch] = ramp
        img.paste(Image.fromarray(strip), (0, y0))

    # Orientation markers
    draw.polygon([(6, 6), (46, 6), (6, 46)], fill=(255, 255, 255),
                 outline=(0, 0, 0))
    draw.text((52, 8), "TOP LEFT", fill=(0, 0, 0))  # on the white bar
    label = "BOTTOM RIGHT"
    left, top, right, bottom = draw.textbbox((0, 0), label)
    draw.text((width - (right - left) - 8, height - (bottom - top) - 10),
              label, fill=(0, 0, 0))  # grey ramp is bright at this corner
    return img


def stamp_frame_marker(img: Image.Image, frame: int) -> Image.Image:
    """Overlay a moving block + frame counter so each blit is visible."""
    draw = ImageDraw.Draw(img)
    width, height = img.size
    x = 4 + (frame * 16) % max(1, width - 28)
    mid = height // 2
    draw.rectangle((x, mid - 10, x + 20, mid + 10),
                   fill=(255, 255, 255), outline=(0, 0, 0))
    # Rightmost colour bar is black, so white text is readable there
    draw.text((width - 90, 6), f"frame {frame}", fill=(255, 255, 255))
    return img
