"""Theme loading/validation and render engine tests (no device, no fonts
guaranteed - widget output is checked structurally, not byte-golden)."""

from pathlib import Path

import pytest

from ax206panel.render.engine import RenderEngine
from ax206panel.render.theme import ThemeError, load_theme

REPO = Path(__file__).resolve().parents[1]
DEFAULT_THEME = REPO / "themes" / "default.yaml"

SAMPLE_VALUES = {
    "cpu.temp": 55.0, "cpu.load": 42.0, "cpu.power_w": 28.0,
    "gpu.temp": 60.0, "gpu.load": 13.0,
    "ram.percent": 51.0, "ram.used_gb": 16.2, "ram.total_gb": 31.7,
    "net.down_mbps": 12.5, "net.up_mbps": 1.2,
    "disk.used_percent": 44.0,
}


def write_theme(tmp_path, body: str) -> Path:
    path = tmp_path / "theme.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_default_theme_loads():
    theme = load_theme(DEFAULT_THEME)
    assert theme.size == (800, 480)
    assert theme.widgets
    types = {w["type"] for w in theme.widgets}
    assert types == {"text", "bar", "gauge", "sparkline"}


def test_default_theme_renders():
    engine = RenderEngine(load_theme(DEFAULT_THEME))
    img = engine.render(SAMPLE_VALUES)
    assert img.size == (800, 480)
    # something other than the background got drawn
    colors = {color for _, color in img.getcolors(maxcolors=100000)}
    assert len(colors) > 10


def test_render_with_missing_values_does_not_crash():
    engine = RenderEngine(load_theme(DEFAULT_THEME))
    img = engine.render({})  # every sensor shows the "--" placeholder
    assert img.size == (800, 480)


def test_sparkline_history_accumulates():
    engine = RenderEngine(load_theme(DEFAULT_THEME))
    sparklines = [w for w in engine._widgets
                  if type(w).__name__ == "SparklineWidget"]
    assert sparklines
    for i in range(5):
        engine.render(dict(SAMPLE_VALUES, **{"net.down_mbps": float(i)}))
    assert len(sparklines[0].history) == 5


def test_missing_file():
    with pytest.raises(ThemeError, match="not found"):
        load_theme("no/such/theme.yaml")


def test_unknown_widget_type(tmp_path):
    path = write_theme(tmp_path, """
widgets:
  - type: blink
    pos: [0, 0]
""")
    with pytest.raises(ThemeError, match="unknown type 'blink'"):
        load_theme(path)


def test_missing_required_key(tmp_path):
    path = write_theme(tmp_path, """
widgets:
  - type: bar
    pos: [0, 0]
    size: [100, 10]
""")
    with pytest.raises(ThemeError, match="missing 'sensor'"):
        load_theme(path)


def test_bad_color(tmp_path):
    path = write_theme(tmp_path, """
background: "not-a-color"
widgets:
  - type: text
    pos: [0, 0]
    text: "hi"
""")
    with pytest.raises(ThemeError, match="invalid color"):
        load_theme(path)


def test_text_needs_text_or_sensor(tmp_path):
    path = write_theme(tmp_path, """
widgets:
  - type: text
    pos: [0, 0]
""")
    with pytest.raises(ThemeError, match="needs 'text' or 'sensor'"):
        load_theme(path)


MINIMAL_WIDGET = """
widgets:
  - type: text
    pos: [0, 0]
    text: "hi"
"""


def test_m6_flags_default_off():
    theme = load_theme(DEFAULT_THEME)
    assert theme.partial_blit is False
    assert theme.schedule is None


def test_brightness_schedule_parses(tmp_path):
    path = write_theme(tmp_path, """
brightness_schedule:
  day: 7
  night: 1
  day_start: "07:00"
  night_start: "22:30"
experimental_partial_blit: true
""" + MINIMAL_WIDGET)
    theme = load_theme(path)
    assert theme.partial_blit is True
    sched = theme.schedule
    assert (sched.day_level, sched.night_level) == (7, 1)
    assert (sched.day_start, sched.night_start) == (7 * 60, 22 * 60 + 30)


def test_schedule_brightness_at():
    from ax206panel.render.theme import Schedule

    sched = Schedule(day_level=7, night_level=1,
                     day_start=7 * 60, night_start=22 * 60)
    assert sched.brightness_at(6 * 60 + 59) == 1
    assert sched.brightness_at(7 * 60) == 7
    assert sched.brightness_at(12 * 60) == 7
    assert sched.brightness_at(22 * 60) == 1
    assert sched.brightness_at(23 * 60) == 1

    # day period crossing midnight (night-shift user)
    flipped = Schedule(day_level=7, night_level=1,
                       day_start=22 * 60, night_start=6 * 60)
    assert flipped.brightness_at(23 * 60) == 7
    assert flipped.brightness_at(2 * 60) == 7
    assert flipped.brightness_at(12 * 60) == 1


def test_schedule_bad_time(tmp_path):
    path = write_theme(tmp_path, """
brightness_schedule:
  day: 7
  night: 1
  day_start: "7am"
  night_start: "22:00"
""" + MINIMAL_WIDGET)
    with pytest.raises(ThemeError, match="expected time 'HH:MM'"):
        load_theme(path)


def test_schedule_bad_level(tmp_path):
    path = write_theme(tmp_path, """
brightness_schedule:
  day: 9
  night: 1
  day_start: "07:00"
  night_start: "22:00"
""" + MINIMAL_WIDGET)
    with pytest.raises(ThemeError, match="must be 0-7"):
        load_theme(path)
