"""YAML theme loader + validation.

A theme is a background plus a list of widgets bound to sensor keys:

    name: my-theme
    size: [800, 480]          # optional; defaults to the device size
    background: "#0d1117"
    brightness_schedule:      # optional day/night backlight (0-7)
      day: 7
      night: 1
      day_start: "07:00"
      night_start: "22:00"
    experimental_partial_blit: false  # blit only the changed rect; some
                                      # AX206 firmware crashes on this
    defaults:                 # fallbacks applied to every widget
      font: "Segoe UI"
      font_size: 24
      color: "#e6edf3"
    widgets:
      - type: text            # static text: strftime() expanded each tick
        pos: [400, 30]
        text: "%H:%M:%S"
      - type: text            # sensor text: format applied to the value
        pos: [60, 340]
        sensor: cpu.load
        format: "{:.0f}%"
      - type: bar
        pos: [340, 170]
        size: [120, 18]
        sensor: ram.percent   # min/max default 0/100
      - type: gauge           # 270-degree arc, 135 -> 405 degrees
        pos: [60, 130]        # top-left of the bounding square
        size: 180             # diameter
        sensor: cpu.temp
        min: 30
        max: 100
      - type: sparkline
        pos: [60, 360]
        size: [300, 80]
        sensor: net.down_mbps
        max: auto             # rescale to the history's peak
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from PIL import ImageColor, ImageFont

log = logging.getLogger(__name__)


class ThemeError(Exception):
    """Theme file is missing, malformed, or fails validation."""


# Map friendly names to Windows font files (Pillow searches the system
# fonts directory for bare filenames on Windows).
FONT_ALIASES = {
    "Segoe UI": "segoeui.ttf",
    "Segoe UI Bold": "segoeuib.ttf",
    "Segoe UI Light": "segoeuil.ttf",
    "Consolas": "consola.ttf",
    "Consolas Bold": "consolab.ttf",
    "Arial": "arial.ttf",
    "Arial Bold": "arialbd.ttf",
}

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def load_font(name: str, size: int):
    """Load (and cache) a font - never per frame (plan: keep CPU low)."""
    key = (name, size)
    if key in _font_cache:
        return _font_cache[key]
    candidates = [FONT_ALIASES.get(name, name)]
    if not candidates[0].lower().endswith((".ttf", ".otf", ".ttc")):
        candidates.append(candidates[0] + ".ttf")
    font = None
    for cand in candidates:
        try:
            font = ImageFont.truetype(cand, size)
            break
        except OSError:
            continue
    if font is None:
        log.warning("font %r not found, using built-in default", name)
        font = ImageFont.load_default(size=size)
    _font_cache[key] = font
    return font


WIDGET_TYPES = ("text", "bar", "gauge", "sparkline")

# type -> required keys (beyond the common ones)
REQUIRED = {
    "text": ["pos"],
    "bar": ["pos", "size", "sensor"],
    "gauge": ["pos", "size", "sensor"],
    "sparkline": ["pos", "size", "sensor"],
}


@dataclass
class Schedule:
    """Day/night backlight schedule, times as minutes since midnight."""
    day_level: int
    night_level: int
    day_start: int
    night_start: int

    def brightness_at(self, minutes: int) -> int:
        """Backlight level for a time of day (minutes since midnight)."""
        if self.day_start <= self.night_start:
            is_day = self.day_start <= minutes < self.night_start
        else:  # day period crosses midnight
            is_day = minutes >= self.day_start or minutes < self.night_start
        return self.day_level if is_day else self.night_level


@dataclass
class Theme:
    name: str
    size: tuple[int, int] | None  # None = use device resolution
    background: tuple[int, int, int]
    defaults: dict
    widgets: list[dict] = field(default_factory=list)
    schedule: Schedule | None = None
    # Some AX206 firmware crashes on partial-rect blits (see
    # Client-DPF-AX206), hence full-frame default + opt-in flag.
    partial_blit: bool = False


def _color(value, where: str) -> tuple[int, int, int]:
    try:
        rgb = ImageColor.getrgb(value)
        return rgb[:3]
    except (ValueError, TypeError) as exc:
        raise ThemeError(f"{where}: invalid color {value!r}: {exc}") from exc


def _xy(value, where: str) -> tuple[int, int]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) for v in value)):
        raise ThemeError(f"{where}: expected [x, y], got {value!r}")
    return int(value[0]), int(value[1])


def _parse_hhmm(value, where: str) -> int:
    try:
        hours, minutes = str(value).split(":")
        hours, minutes = int(hours), int(minutes)
        if not (0 <= hours < 24 and 0 <= minutes < 60):
            raise ValueError
        return hours * 60 + minutes
    except ValueError as exc:
        raise ThemeError(f"{where}: expected time 'HH:MM', got {value!r}") from exc


def _parse_level(value, where: str) -> int:
    if not isinstance(value, int) or not 0 <= value <= 7:
        raise ThemeError(f"{where}: backlight level must be 0-7, got {value!r}")
    return value


def _parse_schedule(data: dict, where: str) -> Schedule:
    if not isinstance(data, dict):
        raise ThemeError(f"{where}: must be a mapping")
    for req in ("day", "night", "day_start", "night_start"):
        if req not in data:
            raise ThemeError(f"{where}: missing '{req}'")
    return Schedule(
        day_level=_parse_level(data["day"], f"{where}.day"),
        night_level=_parse_level(data["night"], f"{where}.night"),
        day_start=_parse_hhmm(data["day_start"], f"{where}.day_start"),
        night_start=_parse_hhmm(data["night_start"], f"{where}.night_start"),
    )


def load_theme(path: str | Path) -> Theme:
    path = Path(path)
    if not path.is_file():
        raise ThemeError(f"theme file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ThemeError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ThemeError(f"{path}: theme must be a YAML mapping")

    size = None
    if "size" in data:
        size = _xy(data["size"], f"{path}: size")

    background = _color(data.get("background", "#000000"),
                        f"{path}: background")

    defaults = {
        "font": "Segoe UI",
        "font_size": 24,
        "color": "#e6edf3",
    }
    defaults.update(data.get("defaults") or {})
    defaults["color"] = _color(defaults["color"], f"{path}: defaults.color")

    raw_widgets = data.get("widgets")
    if not isinstance(raw_widgets, list) or not raw_widgets:
        raise ThemeError(f"{path}: 'widgets' must be a non-empty list")

    widgets = []
    for i, cfg in enumerate(raw_widgets):
        where = f"{path}: widgets[{i}]"
        if not isinstance(cfg, dict):
            raise ThemeError(f"{where}: must be a mapping")
        wtype = cfg.get("type")
        if wtype not in WIDGET_TYPES:
            raise ThemeError(f"{where}: unknown type {wtype!r} "
                             f"(expected one of {WIDGET_TYPES})")
        for req in REQUIRED[wtype]:
            if req not in cfg:
                raise ThemeError(f"{where} ({wtype}): missing '{req}'")
        if wtype == "text" and "text" not in cfg and "sensor" not in cfg:
            raise ThemeError(f"{where} (text): needs 'text' or 'sensor'")

        out = dict(cfg)
        out["pos"] = _xy(cfg["pos"], f"{where}: pos")
        if "size" in cfg and wtype != "gauge":
            out["size"] = _xy(cfg["size"], f"{where}: size")
        for color_key in ("color", "bg_color"):
            if color_key in cfg:
                out[color_key] = _color(cfg[color_key],
                                        f"{where}: {color_key}")
        widgets.append(out)

    schedule = None
    if "brightness_schedule" in data:
        schedule = _parse_schedule(data["brightness_schedule"],
                                   f"{path}: brightness_schedule")

    return Theme(
        name=str(data.get("name", path.stem)),
        size=size,
        background=background,
        defaults=defaults,
        widgets=widgets,
        schedule=schedule,
        partial_blit=bool(data.get("experimental_partial_blit", False)),
    )
