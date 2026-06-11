"""Golden tests for the RGB565 conversion.

Expected bytes are computed by hand from the _RGB565_0/_RGB565_1 macros
in reference/drv_dpf.c:149-150.
"""

import numpy as np
from PIL import Image

from ax206panel.framebuffer import (dirty_rect, image_to_rgb565,
                                    image_to_rgb565_array, rgb565_to_bytes)


def solid(color, size=(1, 1)):
    return Image.new("RGB", size, color)


def test_primary_colors():
    assert image_to_rgb565(solid((255, 0, 0))) == b"\xf8\x00"    # red
    assert image_to_rgb565(solid((0, 255, 0))) == b"\x07\xe0"    # green
    assert image_to_rgb565(solid((0, 0, 255))) == b"\x00\x1f"    # blue
    assert image_to_rgb565(solid((255, 255, 255))) == b"\xff\xff"
    assert image_to_rgb565(solid((0, 0, 0))) == b"\x00\x00"


def test_mixed_color_macro_golden():
    # R=0x12, G=0x34, B=0x56 through the drv_dpf.c macros:
    #   byte0 = (0x12 & 0xf8) | (0x34 & 0xe0) >> 5      = 0x10 | 0x01 = 0x11
    #   byte1 = (0x34 & 0x1c) << 3 | (0x56 & 0xf8) >> 3 = 0xa0 | 0x0a = 0xaa
    assert image_to_rgb565(solid((0x12, 0x34, 0x56))) == b"\x11\xaa"


def test_low_bits_truncated_not_rounded():
    # The macros mask, so values below each channel's step quantize to 0.
    assert image_to_rgb565(solid((7, 3, 7))) == b"\x00\x00"


def test_row_major_pixel_order():
    img = Image.new("RGB", (2, 2))
    img.putpixel((0, 0), (255, 0, 0))      # top-left: red
    img.putpixel((1, 0), (0, 255, 0))      # top-right: green
    img.putpixel((0, 1), (0, 0, 255))      # bottom-left: blue
    img.putpixel((1, 1), (255, 255, 255))  # bottom-right: white
    assert image_to_rgb565(img) == b"\xf8\x00\x07\xe0\x00\x1f\xff\xff"


def test_output_length_and_mode_conversion():
    img = Image.new("RGBA", (800, 480), (10, 20, 30, 255))
    assert len(image_to_rgb565(img)) == 800 * 480 * 2


def test_array_path_matches_bytes_path():
    img = Image.new("RGB", (3, 2), (0x12, 0x34, 0x56))
    assert rgb565_to_bytes(image_to_rgb565_array(img)) == image_to_rgb565(img)


def test_dirty_rect_identical_frames():
    arr = np.zeros((4, 6), dtype=np.uint16)
    assert dirty_rect(arr, arr.copy()) is None


def test_dirty_rect_single_pixel():
    prev = np.zeros((4, 6), dtype=np.uint16)
    cur = prev.copy()
    cur[2, 3] = 0xF800
    assert dirty_rect(prev, cur) == (3, 2, 4, 3)


def test_dirty_rect_bounding_box_spans_changes():
    prev = np.zeros((10, 10), dtype=np.uint16)
    cur = prev.copy()
    cur[1, 2] = 1
    cur[7, 8] = 1
    assert dirty_rect(prev, cur) == (2, 1, 9, 8)


def test_dirty_rect_full_frame_change():
    prev = np.zeros((4, 6), dtype=np.uint16)
    cur = np.ones((4, 6), dtype=np.uint16)
    assert dirty_rect(prev, cur) == (0, 0, 6, 4)
