"""Render the M2 test pattern to a PNG, or push it to the display.

Offline preview (no device needed):
    python tools/test_pattern.py --save pattern.png [--size 800x480]

Push to the display (same as `ax206-panel --test-pattern`):
    python tools/test_pattern.py [--frames 100]
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", metavar="PNG",
                        help="render to a PNG file instead of the device")
    parser.add_argument("--size", default="800x480",
                        help="WxH for --save (default 800x480)")
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--fps", type=float, default=0.0)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--backend", default="auto",
                        choices=("auto", "libusb0", "libusb1"))
    args = parser.parse_args()

    if args.save:
        from ax206panel.patterns import make_base_pattern

        width, height = (int(v) for v in args.size.lower().split("x"))
        make_base_pattern(width, height).save(args.save)
        print(f"wrote {width}x{height} pattern to {args.save}")
        return 0

    from ax206panel import device
    from ax206panel.__main__ import cmd_test_pattern

    backend, name = device.get_backend(args.backend)
    print(f"backend: {name}")
    return cmd_test_pattern(backend, args.index, args.frames, args.fps)


if __name__ == "__main__":
    sys.exit(main())
