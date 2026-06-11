"""Main application: poll sensors -> render theme -> blit to the panel.

Threading model (plan constraint: all USB writes on one thread, sensor
polling on another, never block the blit loop on a slow sensor read):

  - sensor thread: merge_polls() every second into a shared snapshot
  - blit thread: at the configured fps, render the latest snapshot and
    do ALL device I/O (blit, backlight, reconnect)
  - main thread: tray icon (or just waits with --no-tray)

USB recovery: any USB error drops the device handle and the blit thread
retries opening every few seconds until the panel comes back.

Polish (M6): the theme file is watched for changes (mtime) and hot-
reloaded; identical frames are never re-sent; with the theme's
experimental_partial_blit flag only the changed rect is transferred;
an optional brightness_schedule dims the backlight at night.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

import usb.core

from . import device as device_mod
from .framebuffer import dirty_rect, image_to_rgb565_array, rgb565_to_bytes
from .render.engine import RenderEngine
from .render.theme import ThemeError, load_theme
from .sensors import merge_polls, open_sources

log = logging.getLogger(__name__)

SENSOR_INTERVAL = 1.0
RECONNECT_INTERVAL = 3.0
THEME_WATCH_INTERVAL = 2.0


class App:
    def __init__(self, theme_path: str, fps: float = 3.0,
                 backend: str = "auto", index: int = 0, brightness: int = 7):
        self.theme_path = theme_path
        self.fps = fps
        self.backend_name = backend
        self.index = index
        self.brightness = brightness

        self._values: dict[str, float] = {}
        self._values_lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._reload_requested = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sources = []
        self._dev: device_mod.AX206Device | None = None
        self._engine: RenderEngine | None = None
        self._reconnect_warned = False
        self._prev_frame = None          # last RGB565 array actually sent
        self._applied_brightness = None  # last backlight level sent
        self._theme_mtime = 0.0
        self._theme_checked = 0.0

    # -- tray-facing controls ------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def toggle_pause(self) -> None:
        if self._paused.is_set():
            self._paused.clear()
            log.info("resumed")
        else:
            self._paused.set()
            log.info("paused (backlight off)")

    def request_theme_reload(self) -> None:
        self._reload_requested.set()

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Open everything; raises DeviceError/ThemeError on bad setup so
        the CLI can fail with a clear message before going resident."""
        self._sources = open_sources()
        self._backend, backend_name = device_mod.get_backend(self.backend_name)
        log.debug("backend: %s", backend_name)

        self._dev = device_mod.AX206Device.open(index=self.index,
                                                backend=self._backend)
        self._engine = self._build_engine()
        self._apply_brightness()
        log.info("panel %dx%d, theme '%s', %.1f fps%s",
                 self._dev.width, self._dev.height,
                 self._engine.theme.name, self.fps,
                 " (partial blits)" if self._engine.theme.partial_blit else "")

        self._threads = [
            threading.Thread(target=self._sensor_loop, name="sensors",
                             daemon=True),
            threading.Thread(target=self._blit_loop, name="blit",
                             daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def run_forever(self) -> None:
        """Block until Ctrl+C (used with --no-tray)."""
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        if self._dev is not None:
            # Blank the panel on clean shutdown (stale stats are worse
            # than a dark screen).
            try:
                black = bytes(self._dev.width * self._dev.height * 2)
                self._dev.blit(black)
                self._dev.set_backlight(0)
            except (usb.core.USBError, device_mod.DeviceError):
                pass
            self._dev.close()
            self._dev = None
        for source in self._sources:
            source.close()
        log.info("stopped")

    # -- worker threads ----------------------------------------------------------

    def _sensor_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                values = merge_polls(self._sources)
                with self._values_lock:
                    self._values = values
            except Exception as exc:
                log.warning("sensor poll failed: %s", exc)
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, SENSOR_INTERVAL - elapsed))

    def _blit_loop(self) -> None:
        period = 1.0 / self.fps
        while not self._stop.is_set():
            started = time.monotonic()

            if self._paused.is_set():
                # Blank once on entering pause; _apply_brightness restores
                # on resume because _applied_brightness is cleared.
                if self._applied_brightness is not None and self._dev is not None:
                    self._device_call(lambda: self._dev.set_backlight(0))
                    self._applied_brightness = None
                self._stop.wait(0.2)
                continue

            self._check_theme_file(started)
            if self._reload_requested.is_set():
                self._reload_requested.clear()
                self._reload_theme()

            if self._dev is None:
                self._try_reconnect()
                if self._dev is None:
                    continue

            self._apply_brightness()

            with self._values_lock:
                values = dict(self._values)
            img = self._engine.render(values)
            if img.size != (self._dev.width, self._dev.height):
                img = img.resize((self._dev.width, self._dev.height))
            self._send_frame(img)

            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, period - elapsed))

    # -- helpers ---------------------------------------------------------------

    def _send_frame(self, img) -> None:
        """Diff against the last sent frame: skip identical frames, and
        with experimental_partial_blit send only the changed rect."""
        arr = image_to_rgb565_array(img)
        prev = self._prev_frame
        if prev is not None and prev.shape == arr.shape:
            rect = dirty_rect(prev, arr)
            if rect is None:
                return  # nothing changed on screen
            if self._engine.theme.partial_blit:
                x0, y0, x1, y1 = rect
                data = rgb565_to_bytes(arr[y0:y1, x0:x1])
                if self._device_call(lambda: self._dev.blit(data, rect)):
                    self._prev_frame = arr
                return
        if self._device_call(lambda: self._dev.blit(rgb565_to_bytes(arr))):
            self._prev_frame = arr

    def _target_brightness(self) -> int:
        schedule = self._engine.theme.schedule if self._engine else None
        if schedule is None:
            return self.brightness
        now = datetime.now()
        return schedule.brightness_at(now.hour * 60 + now.minute)

    def _apply_brightness(self) -> None:
        desired = self._target_brightness()
        if desired == self._applied_brightness or self._dev is None:
            return
        if self._device_call(lambda: self._dev.set_backlight(desired)):
            if self._applied_brightness is not None:
                log.info("backlight -> %d", desired)
            self._applied_brightness = desired

    def _check_theme_file(self, now_mono: float) -> None:
        """Hot-reload: watch the theme file's mtime."""
        if now_mono - self._theme_checked < THEME_WATCH_INTERVAL:
            return
        self._theme_checked = now_mono
        try:
            mtime = os.stat(self.theme_path).st_mtime
        except OSError:
            return  # file briefly missing (editor save); check again later
        if self._theme_mtime and mtime != self._theme_mtime:
            log.info("theme file changed, reloading")
            self._reload_requested.set()
        self._theme_mtime = mtime

    def _build_engine(self) -> RenderEngine:
        theme = load_theme(self.theme_path)
        try:
            self._theme_mtime = os.stat(self.theme_path).st_mtime
        except OSError:
            pass
        return RenderEngine(theme,
                            device_size=(self._dev.width, self._dev.height))

    def _reload_theme(self) -> None:
        try:
            self._engine = self._build_engine()
            self._prev_frame = None  # layout changed: force a full redraw
            log.info("theme reloaded: %s", self.theme_path)
        except ThemeError as exc:
            log.error("theme reload failed, keeping old theme: %s", exc)

    def _device_call(self, action) -> bool:
        """Run a device operation; on USB failure drop the handle so the
        blit loop enters the reconnect path. Returns True on success."""
        try:
            action()
            return True
        except (usb.core.USBError, device_mod.DeviceError) as exc:
            log.warning("USB error (%s) - device lost, will reconnect", exc)
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
            self._prev_frame = None
            self._applied_brightness = None
            self._reconnect_warned = False
            return False

    def _try_reconnect(self) -> None:
        try:
            self._dev = device_mod.AX206Device.open(index=self.index,
                                                    backend=self._backend)
            self._prev_frame = None
            self._applied_brightness = None  # loop re-applies brightness
            log.info("device reconnected (%dx%d)",
                     self._dev.width, self._dev.height)
        except (usb.core.USBError, device_mod.DeviceError) as exc:
            if not self._reconnect_warned:
                log.warning("device unavailable (%s) - retrying every %.0fs",
                            exc, RECONNECT_INTERVAL)
                self._reconnect_warned = True
            self._stop.wait(RECONNECT_INTERVAL)
