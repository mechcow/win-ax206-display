# ax206-panel

Open-source AIDA64 SensorPanel replacement for AX206-based 3.5" USB LCDs
("AIDA64 USB display" type, hacked digital-photo-frame chipset) on Windows.

**Status: all milestones complete.** Device bring-up (M1), full-frame
RGB565 blits (M2), sensors via LibreHardwareMonitor + psutil (M3), YAML
themes with text/bar/gauge/sparkline widgets (M4), the resident app —
threaded sensor/blit loops, tray icon, USB unplug recovery, Task
Scheduler startup (M5) — and polish: theme hot-reload, day/night
brightness schedule, optional partial blits (M6).

## Protocol

The USB protocol is ported from `drv_dpf.c` (lcd4linux, dpf-ax project);
the reference source is checked in at [reference/drv_dpf.c](reference/drv_dpf.c)
and [src/ax206panel/device.py](src/ax206panel/device.py) maps every command
byte back to it. The device speaks USB Mass Storage Bulk-Only Transport
framing around 16-byte vendor CDBs (opcode `0xcd`) on bulk endpoints
`0x01`/`0x81`; VID:PID `1908:0102`.

## Driver setup

Two drivers are needed, both one-time installs:

### 1. libusb-win32 for the display (Zadig)

The display must be bound to the **libusb-win32** driver — the same setup
AIDA64's USB LCD support uses, so AIDA64 keeps working as a fallback. If
the panel shows as "USB-Display" with a driver error in Device Manager,
or `--list-devices` finds nothing:

1. Download Zadig from <https://zadig.akeo.ie> and run it.
2. Options → **List All Devices**.
3. Select **USB-Display** (USB ID `1908 0102`) in the dropdown.
4. Choose **libusb-win32** as the target driver (use the up/down arrows —
   do **not** pick WinUSB, that breaks AIDA64 compatibility).
5. Click **Install Driver** / **Replace Driver**.

Afterwards the panel appears under "libusb-win32 devices" in Device
Manager. The matching `libusb0.dll` is normally installed to System32 by
Zadig; this app also finds it in vcpkg/scoop/chocolatey trees (e.g. after
`vcpkg install libusb-win32`).

### 2. PawnIO for CPU sensors

LibreHardwareMonitor 0.9.5+ reads Intel/AMD CPU temperatures, clocks and
package power through the signed **PawnIO** driver (the old WinRing0
driver is blocklisted by Windows 11). Install it once from
<https://pawnio.eu>. Without it the app still runs, but `cpu.temp` shows
`--`.

### Troubleshooting

- **"device busy" / could not claim interface 0** — AIDA64 or another
  instance of this app (check the tray and Task Scheduler) holds the
  panel. Only one program can use it at a time.
- **`--list-devices` shows 0 devices** — the panel isn't bound to
  libusb-win32 (see Zadig steps above), or it's unplugged. Run
  `python tools\dump_descriptors.py` for a deeper look.
- **`cpu.temp` shows `--`** — install PawnIO and run from an elevated
  terminal. `--lhm-report` and `--list-sensors` show what LHM can see.

## Install (on the machine with the display)

```powershell
git clone <this repo>
cd win-ax206-display
py -3.11 -m venv .venv
.venv\Scripts\pip install -e .[dev]
```

## Milestone 1 test

```powershell
# 1. What can libusb see? The AX206 should be flagged in the list.
.venv\Scripts\ax206-panel --list-devices

# 2. Open the device, print resolution (expect 480x320)
.venv\Scripts\ax206-panel --info

# 3. Acceptance test: backlight off for 1s, then fades up 1..7
.venv\Scripts\ax206-panel --backlight-test
```

If `--list-devices` shows nothing useful, run the deeper diagnostic:

```powershell
.venv\Scripts\python tools\dump_descriptors.py
```

## Milestone 2 test

```powershell
# Static colour bars + ramps; compare against tools/test_pattern.py --save
.venv\Scripts\ax206-panel --test-pattern

# Soak test: 100 consecutive full-frame blits with a moving marker
.venv\Scripts\ax206-panel --test-pattern --frames 100
```

## Milestone 3 test (sensors)

One-time setup — download LibreHardwareMonitorLib + dependencies (MPL-2.0)
from NuGet into `lib/`:

```powershell
.venv\Scripts\python tools\fetch_lhm.py
```

CPU temperatures/clocks/package power additionally need the **PawnIO**
driver (LibreHardwareMonitor 0.9.5+ uses it for MSR access instead of the
blocklisted WinRing0; HWiNFO uses it too). One-time install from
<https://pawnio.eu>.

Then, from an **elevated** (Run as Administrator) terminal — most CPU/GPU
temperature and fan sensors are unavailable otherwise:

```powershell
# Live values once per second; compare cpu.temp/gpu.temp against HWiNFO
.venv\Scripts\ax206-panel --sensors

# Everything LibreHardwareMonitor exposes (for theme authors)
.venv\Scripts\ax206-panel --list-sensors
```

## Milestone 4 test (theme renderer)

```powershell
# Render themes/default.yaml + live sensor values to render.png
.venv\Scripts\ax206-panel --render-only

# Custom theme / output path
.venv\Scripts\ax206-panel --render-only --theme themes\default.yaml --out preview.png
```

Themes are YAML files mapping widgets (text / bar / gauge / sparkline) to
sensor keys — see the comment header in
[src/ax206panel/render/theme.py](src/ax206panel/render/theme.py) for the
format and [themes/default.yaml](themes/default.yaml) for a full example.
Use `--list-sensors` to discover what your machine exposes.

## Running the panel (Milestone 5)

From an elevated terminal (for full sensor data):

```powershell
# Tray mode (default): icon offers Pause/Resume, Reload theme, Quit
.venv\Scripts\ax206-panel

# Console mode, Ctrl+C to stop
.venv\Scripts\ax206-panel --no-tray

# Options: --theme PATH --fps 3 --brightness 7
```

Unplugging the panel is fine — the app retries every few seconds and
resumes when it's back. Quitting blanks the screen.

While running, the theme file is watched and hot-reloaded when you save
it (a broken edit is rejected with a log message and the old theme stays
up). Identical frames are never re-sent over USB. Two optional theme
keys (see the header of
[src/ax206panel/render/theme.py](src/ax206panel/render/theme.py)):

```yaml
brightness_schedule:    # dim the backlight at night (levels 0-7)
  day: 7
  night: 1
  day_start: "07:00"
  night_start: "22:00"

experimental_partial_blit: true   # transfer only the changed rect.
  # Faster, but some AX206 firmware crashes on partial rects - if your
  # panel freezes or corrupts, remove this (full-frame is the default).
```

Start automatically at logon (elevated, via Task Scheduler — a Startup
shortcut can't elevate):

```powershell
.venv\Scripts\ax206-panel --install-startup    # from an elevated terminal
.venv\Scripts\ax206-panel --uninstall-startup
```

Unit tests (no hardware needed):

```powershell
.venv\Scripts\python -m pytest
```

## Roadmap

See [ax206-sensor-panel-plan.md](ax206-sensor-panel-plan.md) — M2 blit &
test patterns, M3 sensors (LibreHardwareMonitor + psutil), M4 YAML themes
and renderer, M5 tray app, M6 polish.
