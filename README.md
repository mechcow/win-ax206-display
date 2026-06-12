# ax206-panel

Open-source AIDA64 SensorPanel replacement for AX206-based USB LCDs
("AIDA64 USB display" type, hacked digital-photo-frame chipset) on
Windows.

Features:

- Hardware sensors via LibreHardwareMonitor (CPU/GPU temperature, load,
  power) and psutil (RAM, disk, network)
- YAML themes with text, bar, gauge and sparkline widgets, hot-reloaded
  when you save the file
- System tray app with Pause/Resume, theme reload and quit
- Survives unplugging the panel — reconnects automatically
- Day/night backlight brightness schedule
- Start-at-logon via Task Scheduler

## Protocol

The USB protocol is ported from `drv_dpf.c` (lcd4linux, dpf-ax project);
the reference source is checked in at [reference/drv_dpf.c](reference/drv_dpf.c)
and [src/ax206panel/device.py](src/ax206panel/device.py) maps every command
byte back to it. The device speaks USB Mass Storage Bulk-Only Transport
framing around 16-byte vendor CDBs (opcode `0xcd`) on bulk endpoints
`0x01`/`0x81`; VID:PID `1908:0102`.

## Installation

### 1. Install the app

```powershell
git clone https://github.com/mechcow/win-ax206-display.git
cd win-ax206-display
py -3.11 -m venv .venv
.venv\Scripts\pip install -e .
```

### 2. Bind the display to libusb-win32 (Zadig)

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

### 3. Sensor libraries

Download LibreHardwareMonitorLib + dependencies (MPL-2.0) from NuGet
into `lib/`:

```powershell
.venv\Scripts\python tools\fetch_lhm.py
```

CPU temperatures/clocks/package power additionally need the **PawnIO**
driver (LibreHardwareMonitor 0.9.5+ uses it for MSR access instead of
the blocklisted WinRing0; HWiNFO uses it too). One-time install from
<https://pawnio.eu>.

### 4. Check it works

```powershell
.venv\Scripts\ax206-panel --list-devices    # the AX206 should be listed
.venv\Scripts\ax206-panel --info            # opens it, prints resolution
.venv\Scripts\ax206-panel --test-pattern    # colour bars on the panel
```

From an **elevated** (Run as Administrator) terminal — most CPU/GPU
temperature sensors are unavailable otherwise:

```powershell
.venv\Scripts\ax206-panel --sensors         # live values once per second
.venv\Scripts\ax206-panel --list-sensors    # everything LHM exposes
```

## Running the panel

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

Start automatically at logon (elevated, via Task Scheduler — a Startup
shortcut can't elevate):

```powershell
.venv\Scripts\ax206-panel --install-startup    # from an elevated terminal
.venv\Scripts\ax206-panel --uninstall-startup
```

## Themes

Themes are YAML files mapping widgets (text / bar / gauge / sparkline) to
sensor keys — see the comment header in
[src/ax206panel/render/theme.py](src/ax206panel/render/theme.py) for the
format and [themes/default.yaml](themes/default.yaml) for a full example.
Use `--list-sensors` to discover what your machine exposes, and
`--render-only` to preview a theme to a PNG without touching the device:

```powershell
.venv\Scripts\ax206-panel --render-only --theme themes\default.yaml --out preview.png
```

While the app is running, the theme file is watched and hot-reloaded when
you save it (a broken edit is rejected with a log message and the old
theme stays up). Identical frames are never re-sent over USB. Two
optional theme keys:

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

## Troubleshooting

- **"device busy" / could not claim interface 0** — AIDA64 or another
  instance of this app (check the tray and Task Scheduler) holds the
  panel. Only one program can use it at a time.
- **`--list-devices` shows 0 devices** — the panel isn't bound to
  libusb-win32 (see Zadig steps above), or it's unplugged. Run
  `python tools\dump_descriptors.py` for a deeper look.
- **`cpu.temp` shows `--`** — install PawnIO and run from an elevated
  terminal. `--lhm-report` and `--list-sensors` show what LHM can see.

## Development

```powershell
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pytest          # unit tests, no hardware needed
```

## License

GPL-2.0-or-later — the USB protocol code is ported from lcd4linux's
GPL-licensed `drv_dpf.c`. See [LICENSE](LICENSE).
