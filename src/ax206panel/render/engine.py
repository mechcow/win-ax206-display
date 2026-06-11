"""Render engine: composes theme widgets onto a reused PIL canvas."""

from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw

from .theme import Theme
from .widgets import make_widget


class RenderEngine:
    def __init__(self, theme: Theme, device_size: tuple[int, int] | None = None):
        size = theme.size or device_size
        if size is None:
            raise ValueError("theme has no size and no device size given")
        self.size = size
        self.theme = theme
        # Canvas and widgets (incl. fonts, sparkline history) live for the
        # whole session - nothing is allocated per frame.
        self._img = Image.new("RGB", size, theme.background)
        self._draw = ImageDraw.Draw(self._img)
        self._widgets = [make_widget(cfg, theme) for cfg in theme.widgets]

    def render(self, values: dict[str, float]) -> Image.Image:
        """Draw one frame; the returned image is reused between calls."""
        self._draw.rectangle((0, 0, self.size[0] - 1, self.size[1] - 1),
                             fill=self.theme.background)
        now = datetime.now()
        for widget in self._widgets:
            widget.render(self._draw, values, now)
        return self._img
