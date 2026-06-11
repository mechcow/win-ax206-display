"""System tray icon (pystray): pause/resume, theme reload, quit."""

from __future__ import annotations

from PIL import Image, ImageDraw

import pystray
from pystray import Menu, MenuItem

from .app import App


def _icon_image() -> Image.Image:
    """Tiny gauge-arc logo, drawn so we ship no binary assets."""
    img = Image.new("RGBA", (64, 64), (13, 17, 23, 255))
    draw = ImageDraw.Draw(img)
    draw.arc((8, 8, 55, 55), 135, 405, fill=(48, 54, 61, 255), width=9)
    draw.arc((8, 8, 55, 55), 135, 330, fill=(63, 185, 80, 255), width=9)
    return img


def run_tray(app: App) -> None:
    """Run the tray icon on the calling (main) thread; blocks until Quit."""

    def on_toggle(icon, item):
        app.toggle_pause()

    def on_reload(icon, item):
        app.request_theme_reload()

    def on_quit(icon, item):
        icon.stop()

    menu = Menu(
        MenuItem(lambda item: "Resume" if app.paused else "Pause", on_toggle),
        MenuItem("Reload theme", on_reload),
        Menu.SEPARATOR,
        MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("ax206-panel", _icon_image(), "AX206 Panel", menu)
    icon.run()
