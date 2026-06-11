# Project: ax206-panel — Open-source AIDA64 SensorPanel replacement for AX206 USB LCDs (Windows)

## Goal
A free, self-contained Windows app (Python) that drives a 3.5" AX206-based USB LCD (the "AIDA64 USB display" type) showing live system stats: CPU/GPU temps & load, RAM, fan speeds, network throughput, clock. Runs in the system tray, starts with Windows, themes defined in a simple config file.

## Background / prior art (read these first)
The AX206 is a hacked digital-photo-frame chipset. The USB protocol is already reverse-engineered — do NOT invent it, port it:

- **Protocol reference (C):** `drv_dpf.c` in lcd4linux forks, e.g. https://github.com/amd989/lcd4linux-ax206 and https://github.com/TangoCash/lcd4linux (file `drv_dpf.c`). This is the canonical open/blit/backlight implementation.
- **dpf-ax project:** https://github.com/dreamlayers/dpf-ax — original firmware hack, includes a dpf access library and Python modules worth mining.
- **Windows proof-of-concept (C++):** https://github.com/wjohnsaunders/Client-DPF-AX206 — framebuffer + libusb on Windows. Note its key finding: some units crash on partial-rect blits and require full-framebuffer blits. Design for full-frame blit as the default, partial blit as an optional optimization behind a config flag.

### Protocol summary (verify against drv_dpf.c, don't trust this blindly)
- Device enumerates over libusb; hacked frames typically VID `0x1908` PID `0x0102`. **Enumerate and log all devices rather than hardcoding** — my unit currently works with AIDA64 via the Zadig-installed `libusb-win32` driver and shows as "USB-Display" under libusb-win32 devices in Device Manager.
- Transport is USB bulk transfers wrapping vendor SCSI-style commands; the vendor command block starts with `0xcd` (16-byte CDB). Subcommands cover: get LCD parameters (width/height), set properties (brightness/backlight 0–7), and blit a rectangle of RGB565 pixel data.
- Pixel format: RGB565, check byte order against the reference driver.
- Expected resolution 480x320 (confirm via the "get LCD params" command at startup and size the framebuffer from the response).

## Tech stack
- Python 3.11+, single venv, `pip` only.
- `pyusb` for USB. Important: the installed Windows driver is **libusb-win32** (installed via Zadig for AIDA64). Use the `libusb0` backend in pyusb (`usb.backend.libusb0`). Do not require the user to switch to WinUSB — that would break AIDA64 fallback. If libusb0 backend proves unreliable, fall back to `libusb-package` + WinUSB and document the Zadig switch.
- `Pillow` for rendering the framebuffer (fonts, gauges, graphs).
- **Sensors:** `pythonnet` + `LibreHardwareMonitorLib.dll` (LibreHardwareMonitor, MPL-2.0) for CPU/GPU temps, loads, clocks, fans. Requires running as admin for most sensors — document this and add a manifest/elevation check with a clear error message. `psutil` as a secondary source for RAM/network/disk.
- `pystray` for the system tray icon (pause/resume, theme reload, quit).
- No GUI editor in v1 — themes are declarative YAML.

## Repo structure
```
ax206-panel/
  pyproject.toml
  README.md
  src/ax206panel/
    __main__.py          # entry point, CLI args (--theme, --list-devices, --test-pattern)
    device.py            # AX206 USB protocol: open, get_params, set_backlight, blit
    framebuffer.py       # RGB888 PIL image -> RGB565 bytes conversion
    sensors/
      base.py            # SensorSource interface: poll() -> dict[str, float]
      lhm.py             # LibreHardwareMonitor source
      psutil_src.py      # psutil source
    render/
      engine.py          # composes widgets onto a PIL canvas each tick
      widgets.py         # text, bar, arc gauge, sparkline (history ring buffer)
      theme.py           # YAML theme loader + validation
    app.py               # main loop: poll sensors -> render -> blit, target 2-4 fps
    tray.py              # pystray integration
  themes/
    default.yaml
  tools/
    dump_descriptors.py  # enumerate USB devices, print VID/PID/endpoints
    test_pattern.py      # draw gradients/colour bars to validate blit + byte order
  tests/
    test_framebuffer.py  # RGB565 conversion golden tests
    test_theme.py
```

## Milestones (implement and verify in order — stop after each for me to test on hardware)
1. **M1 — Device bring-up.** `--list-devices` enumerates USB devices via libusb0 backend. Open the AX206, send "get LCD params", print resolution. Then toggle backlight 0→7. *Acceptance: backlight visibly changes.*
2. **M2 — Blit.** `test_pattern.py` pushes full-frame colour bars and gradients. Get byte order/orientation right here. *Acceptance: clean, correctly-oriented test pattern, no crash after 100 consecutive frames.*
3. **M3 — Sensors.** `lhm.py` + `psutil_src.py` return a flat dict like `cpu.temp`, `gpu.load`, `net.down_mbps`. CLI mode prints values once per second. *Acceptance: CPU/GPU temps match HWiNFO within ~1°C.*
4. **M4 — Renderer + theme.** YAML theme maps widgets (text/bar/gauge/sparkline) to sensor keys with position, font, colour. Ship `default.yaml` (dark theme: CPU & GPU temp arcs, RAM bar, net sparklines, clock). *Acceptance: theme renders to PNG identically with `--render-only` (no device needed).*
5. **M5 — App.** Main loop at configurable fps (default 3), tray icon, graceful USB error recovery (device unplug → retry loop), `--install-startup` to register a Task Scheduler entry (needed for admin elevation at logon, instead of a Startup shortcut).
6. **M6 — Polish.** Theme hot-reload on file change, optional partial-rect blits behind `experimental_partial_blit: true`, day/night brightness schedule, README with Zadig/driver setup.

## Constraints & gotchas
- AIDA64 and this app cannot hold the device at the same time — claim the interface exclusively and fail with a clear message if busy.
- Default to **full-frame blits** (see Client-DPF-AX206 note); some firmware crashes otherwise.
- Keep CPU overhead under ~1% at 3 fps; reuse PIL objects, avoid per-frame font loading.
- All USB writes go through one thread; sensor polling on another; never block the blit loop on a slow sensor read.
- Code must run on Windows 11; don't use POSIX-only APIs.

## How to work
- Port protocol logic from `drv_dpf.c` faithfully, with comments mapping each command byte back to the reference source.
- After each milestone, give me the exact command to run and what I should observe on the panel.
- Write the RGB565 conversion with unit tests before touching the device.
