"""Sensor sources: psutil (always available) + LibreHardwareMonitor."""

from __future__ import annotations

import logging

from .base import SensorError, SensorSource, merge_polls

log = logging.getLogger(__name__)

__all__ = ["SensorError", "SensorSource", "merge_polls", "open_sources"]


def open_sources(use_lhm: bool = True) -> list[SensorSource]:
    """Open all available sensor sources.

    psutil comes first so LHM's better values (e.g. cpu.load) override it
    in merge_polls(). A missing/broken LHM setup degrades to psutil-only
    with a warning rather than failing.
    """
    from .psutil_src import PsutilSource

    sources: list[SensorSource] = [PsutilSource()]
    if use_lhm:
        try:
            from .lhm import LhmSource

            sources.append(LhmSource())
        except SensorError as exc:
            log.warning("LibreHardwareMonitor unavailable: %s", exc)
        except Exception as exc:
            log.warning("LibreHardwareMonitor failed to initialise: %s", exc)
    return sources
