"""Sensor source interface.

Each source's poll() returns a flat dict of dotted keys to floats,
e.g. {"cpu.temp": 54.0, "net.down_mbps": 12.3}. Sources are polled in
order and merged, later sources overriding earlier ones on key clashes
(so LHM's cpu.load wins over psutil's fallback).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SensorError(Exception):
    """A sensor source could not be initialised or polled."""


class SensorSource(ABC):
    name: str = "base"

    @abstractmethod
    def poll(self) -> dict[str, float]:
        """Return current values as a flat {dotted.key: float} dict."""

    def close(self) -> None:
        """Release any underlying resources (default: nothing)."""


def merge_polls(sources: list[SensorSource]) -> dict[str, float]:
    values: dict[str, float] = {}
    for source in sources:
        values.update(source.poll())
    return values
