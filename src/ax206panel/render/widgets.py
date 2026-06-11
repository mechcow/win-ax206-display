"""Render widgets: text, bar, arc gauge, sparkline.

Each widget is constructed once from its validated theme config (fonts
loaded up front, never per frame) and re-rendered every tick with the
current sensor values dict.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

from PIL import ImageDraw

from .theme import Theme, load_font

log = logging.getLogger(__name__)

MISSING_TEXT = "--"


class Widget:
    def __init__(self, cfg: dict, theme: Theme):
        self.cfg = cfg
        self.pos = cfg["pos"]
        self.sensor = cfg.get("sensor")
        self.color = cfg.get("color", theme.defaults["color"])

    def value(self, values: dict[str, float]) -> float | None:
        if self.sensor is None:
            return None
        return values.get(self.sensor)

    def render(self, draw: ImageDraw.ImageDraw, values: dict[str, float],
               now: datetime) -> None:
        raise NotImplementedError


class TextWidget(Widget):
    def __init__(self, cfg, theme):
        super().__init__(cfg, theme)
        self.static = cfg.get("text")          # strftime-expanded
        self.format = cfg.get("format", "{:.0f}")
        self.anchor = cfg.get("anchor", "la")  # PIL anchor, e.g. mm, ma, ra
        self.font = load_font(cfg.get("font", theme.defaults["font"]),
                              int(cfg.get("font_size",
                                          theme.defaults["font_size"])))

    def render(self, draw, values, now):
        if self.static is not None:
            text = now.strftime(self.static)
        else:
            value = self.value(values)
            text = MISSING_TEXT if value is None else self.format.format(value)
        draw.text(self.pos, text, font=self.font, fill=self.color,
                  anchor=self.anchor)


class BarWidget(Widget):
    def __init__(self, cfg, theme):
        super().__init__(cfg, theme)
        self.size = cfg["size"]
        self.min = float(cfg.get("min", 0))
        self.max = float(cfg.get("max", 100))
        self.bg_color = cfg.get("bg_color", (40, 44, 52))

    def render(self, draw, values, now):
        x, y = self.pos
        w, h = self.size
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=self.bg_color,
                       outline=self.color, width=1)
        value = self.value(values)
        if value is None:
            return
        frac = max(0.0, min(1.0, (value - self.min) / (self.max - self.min)))
        fill_w = round((w - 4) * frac)
        if fill_w > 0:
            draw.rectangle((x + 2, y + 2, x + 2 + fill_w - 1, y + h - 3),
                           fill=self.color)


class GaugeWidget(Widget):
    """270-degree arc gauge. PIL angles: 0 = 3 o'clock, clockwise; the
    default 135..405 sweep puts the gap at the bottom."""

    def __init__(self, cfg, theme):
        super().__init__(cfg, theme)
        size = cfg["size"]
        self.diameter = int(size if isinstance(size, (int, float)) else size[0])
        self.min = float(cfg.get("min", 0))
        self.max = float(cfg.get("max", 100))
        self.start = float(cfg.get("start_angle", 135))
        self.end = float(cfg.get("end_angle", 405))
        self.width = int(cfg.get("width", max(6, self.diameter // 12)))
        self.bg_color = cfg.get("bg_color", (40, 44, 52))
        self.show_value = cfg.get("show_value", True)
        self.format = cfg.get("format", "{:.0f}")
        self.font = load_font(cfg.get("font", theme.defaults["font"]),
                              int(cfg.get("font_size", self.diameter // 4)))

    def render(self, draw, values, now):
        x, y = self.pos
        bbox = (x, y, x + self.diameter - 1, y + self.diameter - 1)
        draw.arc(bbox, self.start, self.end, fill=self.bg_color,
                 width=self.width)
        value = self.value(values)
        if value is None:
            text = MISSING_TEXT
        else:
            frac = max(0.0, min(1.0,
                                (value - self.min) / (self.max - self.min)))
            if frac > 0:
                draw.arc(bbox, self.start,
                         self.start + (self.end - self.start) * frac,
                         fill=self.color, width=self.width)
            text = self.format.format(value)
        if self.show_value:
            center = (x + self.diameter // 2, y + self.diameter // 2)
            draw.text(center, text, font=self.font, fill=self.color,
                      anchor="mm")


class SparklineWidget(Widget):
    """Line graph of the sensor's recent history, one sample per pixel of
    width (so a 300px sparkline at 1 Hz shows 5 minutes)."""

    def __init__(self, cfg, theme):
        super().__init__(cfg, theme)
        self.size = cfg["size"]
        self.min = float(cfg.get("min", 0))
        self.max = cfg.get("max", "auto")  # number or "auto"
        self.bg_color = cfg.get("bg_color")
        self.history: deque[float] = deque(maxlen=self.size[0])

    def render(self, draw, values, now):
        x, y = self.pos
        w, h = self.size
        if self.bg_color is not None:
            draw.rectangle((x, y, x + w - 1, y + h - 1), fill=self.bg_color)

        value = self.value(values)
        if value is not None:
            self.history.append(value)
        if not self.history:
            return

        if self.max == "auto":
            top = max(max(self.history), self.min + 1e-9)
        else:
            top = float(self.max)
        span = top - self.min

        points = []
        for i, sample in enumerate(self.history):
            frac = max(0.0, min(1.0, (sample - self.min) / span))
            points.append((x + i, y + h - 1 - round(frac * (h - 1))))
        if len(points) == 1:
            draw.point(points[0], fill=self.color)
        else:
            draw.line(points, fill=self.color, width=1)


WIDGET_CLASSES = {
    "text": TextWidget,
    "bar": BarWidget,
    "gauge": GaugeWidget,
    "sparkline": SparklineWidget,
}


def make_widget(cfg: dict, theme: Theme) -> Widget:
    return WIDGET_CLASSES[cfg["type"]](cfg, theme)
