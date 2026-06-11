"""Tests for the psutil sensor source and the merge logic.

LHM needs the .NET DLL and admin rights, so it is exercised manually via
`ax206-panel --sensors`, not here.
"""

from ax206panel.sensors.base import SensorSource, merge_polls
from ax206panel.sensors.psutil_src import PsutilSource

REQUIRED_KEYS = {
    "ram.percent", "ram.used_gb", "ram.total_gb",
    "net.down_mbps", "net.up_mbps",
    "disk.used_percent", "cpu.load",
}


def test_psutil_poll_keys_and_types():
    values = PsutilSource().poll()
    assert REQUIRED_KEYS <= values.keys()
    assert all(isinstance(v, float) for v in values.values())


def test_psutil_values_sane():
    values = PsutilSource().poll()
    assert 0 <= values["ram.percent"] <= 100
    assert 0 < values["ram.used_gb"] <= values["ram.total_gb"]
    assert values["net.down_mbps"] >= 0
    assert 0 <= values["disk.used_percent"] <= 100


def test_merge_later_source_wins():
    class Fake(SensorSource):
        def __init__(self, name, values):
            self.name = name
            self._values = values

        def poll(self):
            return dict(self._values)

    merged = merge_polls([
        Fake("a", {"cpu.load": 1.0, "only.a": 2.0}),
        Fake("b", {"cpu.load": 9.0}),
    ])
    assert merged == {"cpu.load": 9.0, "only.a": 2.0}
