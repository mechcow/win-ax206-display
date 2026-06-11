"""psutil sensor source: RAM, network throughput, disk usage.

Also provides cpu.load as a fallback; the LHM source overrides it when
available (see merge order in sensors/__init__.py).
"""

from __future__ import annotations

import time

import psutil

from .base import SensorSource

GIB = 1024 ** 3


class PsutilSource(SensorSource):
    name = "psutil"

    def __init__(self, disk_path: str = "C:\\"):
        self._disk_path = disk_path
        # Prime the counters so the first poll() reports a rate, not garbage
        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        psutil.cpu_percent(interval=None)  # first call always returns 0.0

    def poll(self) -> dict[str, float]:
        out: dict[str, float] = {}

        vm = psutil.virtual_memory()
        out["ram.percent"] = float(vm.percent)
        out["ram.used_gb"] = (vm.total - vm.available) / GIB
        out["ram.total_gb"] = vm.total / GIB

        now = time.monotonic()
        net = psutil.net_io_counters()
        dt = max(1e-6, now - self._last_time)
        out["net.down_mbps"] = (net.bytes_recv - self._last_net.bytes_recv) * 8 / dt / 1e6
        out["net.up_mbps"] = (net.bytes_sent - self._last_net.bytes_sent) * 8 / dt / 1e6
        self._last_net = net
        self._last_time = now

        out["disk.used_percent"] = float(psutil.disk_usage(self._disk_path).percent)
        out["cpu.load"] = float(psutil.cpu_percent(interval=None))
        return out
