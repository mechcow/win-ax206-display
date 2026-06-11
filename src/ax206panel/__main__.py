"""CLI entry point.

Milestone 1 commands:
  --list-devices    enumerate USB devices visible to the libusb backend
  --info            open the AX206 and print its resolution
  --backlight N     set backlight level 0-7
  --backlight-test  sweep backlight 0 -> 7 (the M1 acceptance test)

Milestone 2 commands:
  --test-pattern    blit colour bars + gradients; --frames 100 for the
                    M2 soak test (full-frame blits, no partial rects)

Milestone 3 commands (no device needed; run elevated for full sensors):
  --sensors         print sensor values once per second (Ctrl+C to stop)
  --list-sensors    dump every sensor LibreHardwareMonitor exposes

Milestone 4 commands:
  --render-only     render the theme + live sensor values to a PNG
                    (no device needed; --theme / --out to override paths)

Milestone 5 (default action - no flag needed):
  ax206-panel       run the panel: sensors -> theme -> blit at --fps,
                    with a tray icon (pause/resume, reload theme, quit)
  --no-tray         run in the console instead (Ctrl+C to stop)
  --install-startup register a Task Scheduler logon task (elevated, so
                    sensors work); --uninstall-startup removes it
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import usb.core

from . import __version__, device
from .render.theme import ThemeError
from .sensors import SensorError

log = logging.getLogger("ax206panel")


def cmd_list_devices(backend, backend_name: str) -> int:
    devs = list(usb.core.find(find_all=True, backend=backend))
    print(f"backend: {backend_name} - {len(devs)} device(s) visible")
    if backend_name == "libusb0":
        print("(libusb0 only sees devices bound to the libusb-win32 driver, "
              "so a short list here is normal)")
    for dev in devs:
        is_ax206 = (dev.idVendor == device.AX206_VID
                    and dev.idProduct == device.AX206_PID)
        # String descriptor reads can fail without the right driver access;
        # never let them kill the listing.
        strings = []
        for attr in ("manufacturer", "product"):
            try:
                value = getattr(dev, attr)
            except (usb.core.USBError, ValueError, NotImplementedError):
                value = None
            if value:
                strings.append(value)
        desc = " ".join(strings) or "(no string descriptors readable)"
        tag = "  <-- AX206 display" if is_ax206 else ""
        print(f"  {dev.idVendor:04x}:{dev.idProduct:04x}  bus {dev.bus} "
              f"addr {dev.address}  {desc}{tag}")
    return 0


def cmd_info(backend, index: int) -> int:
    with device.AX206Device.open(index=index, backend=backend) as dev:
        print(f"AX206 #{index}: {dev.width}x{dev.height}")
    return 0


def cmd_backlight(backend, index: int, level: int) -> int:
    with device.AX206Device.open(index=index, backend=backend) as dev:
        print(f"AX206 #{index}: {dev.width}x{dev.height}")
        dev.set_backlight(level)
        print(f"backlight set to {level}")
    return 0


def cmd_backlight_test(backend, index: int) -> int:
    with device.AX206Device.open(index=index, backend=backend) as dev:
        print(f"AX206 #{index}: {dev.width}x{dev.height}")
        print("backlight off (0) for 1s...")
        dev.set_backlight(0)
        time.sleep(1.0)
        for level in range(1, 8):
            print(f"backlight {level}")
            dev.set_backlight(level)
            time.sleep(0.5)
        print("done - panel should have faded from off to full brightness "
              "and stayed at 7")
    return 0


def cmd_test_pattern(backend, index: int, frames: int, fps: float) -> int:
    # Imported here so device-only commands don't need Pillow/numpy loaded.
    from . import framebuffer, patterns

    with device.AX206Device.open(index=index, backend=backend) as dev:
        print(f"AX206 #{index}: {dev.width}x{dev.height}")
        base = patterns.make_base_pattern(dev.width, dev.height)
        start = time.perf_counter()
        for n in range(frames):
            if frames > 1:
                img = patterns.stamp_frame_marker(base.copy(), n)
            else:
                img = base
            dev.blit(framebuffer.image_to_rgb565(img))
            if frames > 1 and (n + 1) % 10 == 0:
                print(f"  frame {n + 1}/{frames}")
            if fps > 0:
                time.sleep(1.0 / fps)
        elapsed = time.perf_counter() - start
        print(f"done: {frames} frame(s) in {elapsed:.2f}s "
              f"({frames / elapsed:.1f} fps incl. throttle)")
    return 0


def cmd_sensors(interval: float, once: bool) -> int:
    from .sensors import merge_polls, open_sources

    sources = open_sources()
    print(f"sources: {', '.join(s.name for s in sources)} "
          "(Ctrl+C to stop)")
    try:
        while True:
            values = merge_polls(sources)
            print(f"--- {time.strftime('%H:%M:%S')} "
                  f"({len(values)} values) ---")
            for key in sorted(values):
                print(f"  {key:<20} {values[key]:8.1f}")
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        for source in sources:
            source.close()


def cmd_list_sensors() -> int:
    from .sensors.lhm import LhmSource

    source = LhmSource()
    try:
        for line in source.dump():
            print(line)
    finally:
        source.close()
    return 0


def default_theme_path() -> str:
    # repo layout: src/ax206panel/__main__.py -> repo root is parents[2]
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2] / "themes" / "default.yaml")


def cmd_render_only(theme_path: str, out_path: str) -> int:
    from .render.engine import RenderEngine
    from .render.theme import load_theme
    from .sensors import merge_polls, open_sources

    theme = load_theme(theme_path)
    engine = RenderEngine(theme, device_size=(800, 480))
    sources = open_sources()
    try:
        values = merge_polls(sources)
    finally:
        for source in sources:
            source.close()
    img = engine.render(values)
    img.save(out_path)
    print(f"theme '{theme.name}' ({engine.size[0]}x{engine.size[1]}, "
          f"{len(values)} sensor values) -> {out_path}")
    return 0


TASK_NAME = "ax206-panel"


def cmd_run(args) -> int:
    from .app import App

    app = App(theme_path=args.theme or default_theme_path(),
              fps=args.fps if args.fps > 0 else 3.0,
              backend=args.backend, index=args.index,
              brightness=args.brightness)
    app.start()
    if args.no_tray:
        print("panel running - Ctrl+C to stop")
        app.run_forever()
    else:
        from .tray import run_tray

        print("panel running - use the tray icon to pause/quit")
        run_tray(app)
        app.stop()
    return 0


def cmd_install_startup(args) -> int:
    import subprocess
    from pathlib import Path

    # pythonw.exe = no console window for a logon task
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.is_file() else python
    theme = str(Path(args.theme or default_theme_path()).resolve())
    fps = args.fps if args.fps > 0 else 3.0
    command = (f'"{exe}" -m ax206panel --theme "{theme}" '
               f'--fps {fps} --brightness {args.brightness}')

    # /RL HIGHEST runs the task elevated at logon, which the LHM sensors
    # need - a Startup-folder shortcut can't do that.
    result = subprocess.run(
        ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "ONLOGON",
         "/RL", "HIGHEST", "/TR", command],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error: schtasks failed: {result.stderr.strip()}\n"
              "(creating a /RL HIGHEST task requires an elevated terminal)",
              file=sys.stderr)
        return 1
    print(f"startup task '{TASK_NAME}' installed:\n  {command}\n"
          f"It runs elevated at every logon. Remove with --uninstall-startup.")
    return 0


def cmd_uninstall_startup() -> int:
    import subprocess

    result = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error: schtasks failed: {result.stderr.strip()}",
              file=sys.stderr)
        return 1
    print(f"startup task '{TASK_NAME}' removed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ax206-panel",
        description="AX206 USB LCD sensor panel. With no action flag, runs "
                    "the panel (sensors -> theme -> blit) with a tray icon.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--backend", choices=("auto", "libusb0", "libusb1"),
                        default="auto",
                        help="libusb backend (default: prefer libusb0, the "
                             "Zadig/AIDA64 libusb-win32 driver)")
    parser.add_argument("--index", type=int, default=0,
                        help="which AX206 to open if several are attached")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="debug logging")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--list-devices", action="store_true",
                         help="list USB devices visible to the backend")
    actions.add_argument("--info", action="store_true",
                         help="open the display and print its resolution")
    actions.add_argument("--backlight", type=int, metavar="0-7",
                         help="set backlight level and exit")
    actions.add_argument("--backlight-test", action="store_true",
                         help="sweep backlight 0->7 (M1 acceptance test)")
    actions.add_argument("--test-pattern", action="store_true",
                         help="blit colour bars + gradients (M2)")
    actions.add_argument("--sensors", action="store_true",
                         help="print sensor values every --interval seconds")
    actions.add_argument("--list-sensors", action="store_true",
                         help="dump all LibreHardwareMonitor sensors")
    actions.add_argument("--lhm-report", action="store_true",
                         help="print LHM's diagnostic report (ring0 driver "
                              "status etc.)")
    actions.add_argument("--render-only", action="store_true",
                         help="render theme to PNG without a device (M4)")
    actions.add_argument("--install-startup", action="store_true",
                         help="register an elevated Task Scheduler logon task")
    actions.add_argument("--uninstall-startup", action="store_true",
                         help="remove the Task Scheduler logon task")
    parser.add_argument("--theme", default=None,
                        help="theme YAML path (default: themes/default.yaml)")
    parser.add_argument("--out", default="render.png",
                        help="output PNG for --render-only")
    parser.add_argument("--fps", type=float, default=0.0,
                        help="panel refresh rate when running "
                             "(default 3; also throttles --test-pattern)")
    parser.add_argument("--brightness", type=int, default=7, metavar="0-7",
                        help="initial backlight level when running (default 7)")
    parser.add_argument("--no-tray", action="store_true",
                        help="run in the console without a tray icon")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="poll interval for --sensors (default 1s)")
    parser.add_argument("--once", action="store_true",
                        help="with --sensors: print one reading and exit")
    parser.add_argument("--frames", type=int, default=1,
                        help="number of test-pattern frames to blit "
                             "(use 100 for the M2 soak test)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")

    try:
        # Sensor commands don't touch USB - dispatch before backend setup
        if args.sensors:
            return cmd_sensors(args.interval, args.once)
        if args.list_sensors:
            return cmd_list_sensors()
        if args.lhm_report:
            from .sensors.lhm import LhmSource

            source = LhmSource()
            try:
                print(source.report())
            finally:
                source.close()
            return 0
        if args.render_only:
            return cmd_render_only(args.theme or default_theme_path(),
                                   args.out)
        if args.install_startup:
            return cmd_install_startup(args)
        if args.uninstall_startup:
            return cmd_uninstall_startup()

        backend, backend_name = device.get_backend(args.backend)
        log.debug("using backend %s", backend_name)
        if args.list_devices:
            return cmd_list_devices(backend, backend_name)
        if args.info:
            return cmd_info(backend, args.index)
        if args.backlight is not None:
            return cmd_backlight(backend, args.index, args.backlight)
        if args.backlight_test:
            return cmd_backlight_test(backend, args.index)
        if args.test_pattern:
            return cmd_test_pattern(backend, args.index, args.frames,
                                    args.fps)
        return cmd_run(args)  # default action: run the panel
    except (device.DeviceError, SensorError, ThemeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
