"""LibreHardwareMonitor sensor source (pythonnet + LibreHardwareMonitorLib.dll).

The DLL is not vendored; download it with:

    python tools/fetch_lhm.py

which places LibreHardwareMonitorLib.dll (and its HidSharp dependency)
in <repo>/lib. Override the location with the AX206_LHM_DLL env var.

Most sensors (CPU/GPU temperature, fans) require Administrator rights;
without elevation LHM silently reports far fewer sensors.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

from .base import SensorError, SensorSource

log = logging.getLogger(__name__)

DLL_ENV = "AX206_LHM_DLL"

# Preferred sensor names for the curated keys, tried in order.
# "Core Average" over "CPU Package": Intel's package sensor reports the
# hottest instantaneous spot and reads 20-30C above the cores on short
# bursts; Core Average matches what HWiNFO shows as the core temperature.
CPU_TEMP_PREFERENCE = ["Core (Tctl/Tdie)", "Core Average", "CPU Package",
                       "Core Max"]
GPU_TEMP_PREFERENCE = ["GPU Core", "GPU Hot Spot"]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - non-Windows
        return False


def _find_dll() -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get(DLL_ENV)
    if env:
        candidates.append(Path(env))
    # repo layout: src/ax206panel/sensors/lhm.py -> repo root is parents[3]
    candidates.append(Path(__file__).resolve().parents[3]
                      / "lib" / "LibreHardwareMonitorLib.dll")
    candidates.append(Path.cwd() / "lib" / "LibreHardwareMonitorLib.dll")
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


class LhmSource(SensorSource):
    name = "lhm"

    def __init__(self):
        dll = _find_dll()
        if dll is None:
            raise SensorError(
                "LibreHardwareMonitorLib.dll not found - run "
                "'python tools/fetch_lhm.py' first (or set "
                f"{DLL_ENV} to the DLL path)")

        if not is_admin():
            log.warning(
                "not running as Administrator - LibreHardwareMonitor will "
                "report few or no CPU/GPU temperature and fan sensors. "
                "Run from an elevated terminal for full data.")

        sys.path.append(str(dll.parent))
        import clr  # pythonnet

        clr.AddReference("LibreHardwareMonitorLib")
        from LibreHardwareMonitor import Hardware

        self._Hardware = Hardware
        self._computer = Hardware.Computer()
        self._computer.IsCpuEnabled = True
        self._computer.IsGpuEnabled = True
        self._computer.IsMemoryEnabled = True
        self._computer.IsMotherboardEnabled = True  # SuperIO fans/temps
        self._computer.Open()

        # LHM >= 0.9.5 reads CPU MSRs (temps, clocks, package power)
        # through the PawnIO driver only; without it those sensors stay
        # empty no matter the elevation.
        if "cpu.temp" not in self.poll():
            log.warning(
                "no CPU temperature available - LibreHardwareMonitor needs "
                "the PawnIO driver for CPU temps/clocks/power. Install it "
                "from https://pawnio.eu (one-time, signed driver), then "
                "re-run elevated.")

    def close(self) -> None:
        self._computer.Close()

    # -- raw collection ----------------------------------------------------

    def _walk(self):
        """Yield (hardware_type_name, sensor) for every sensor, updated."""

        def visit(hw):
            hw.Update()
            hw_type = str(hw.HardwareType)
            for sensor in hw.Sensors:
                yield hw_type, sensor
            for sub in hw.SubHardware:
                yield from visit(sub)

        for hw in self._computer.Hardware:
            yield from visit(hw)

    def dump(self) -> list[str]:
        """Human-readable hardware/sensor tree (for theme authors and
        debugging - a hardware node with no Temperature sensors usually
        means LHM's ring0 driver failed to load)."""
        lines = [f"admin: {is_admin()}"]

        def visit(hw, depth):
            hw.Update()
            lines.append(f"{'  ' * depth}[{str(hw.HardwareType)}] "
                         f"{str(hw.Name)} ({len(list(hw.Sensors))} sensors)")
            for sensor in hw.Sensors:
                value = sensor.Value
                value_str = f"{float(value):.2f}" if value is not None else "-"
                lines.append(f"{'  ' * (depth + 1)}"
                             f"{str(sensor.Identifier):<44} "
                             f"{str(sensor.SensorType):<12} "
                             f"{str(sensor.Name):<28} {value_str}")
            for sub in hw.SubHardware:
                visit(sub, depth + 1)

        for hw in self._computer.Hardware:
            visit(hw, 0)
        return lines

    def report(self) -> str:
        """LHM's own diagnostic report - includes the ring0 driver status,
        which explains missing CPU temperatures (e.g. driver blocked by
        Windows Memory Integrity)."""
        return str(self._computer.GetReport())

    # -- curated keys -------------------------------------------------------

    def poll(self) -> dict[str, float]:
        readings: list[tuple[str, str, str, float]] = []
        for hw_type, sensor in self._walk():
            if sensor.Value is None:
                continue
            readings.append((hw_type, str(sensor.SensorType),
                             str(sensor.Name), float(sensor.Value)))

        out: dict[str, float] = {}

        def pick(rows, preference):
            by_name = {name: value for _, _, name, value in rows}
            for wanted in preference:
                if wanted in by_name:
                    return by_name[wanted]
            return max(by_name.values()) if by_name else None

        cpu = [r for r in readings if r[0] == "Cpu"]

        # Several GPUs may be present (e.g. discrete NVIDIA + Intel iGPU);
        # use only the primary one - discrete preferred - so the iGPU's
        # zeros don't clobber the real values.
        gpu_types = {r[0] for r in readings if r[0].startswith("Gpu")}
        primary_gpu = None
        for preference in ("GpuNvidia", "GpuAmd", "GpuIntel"):
            if preference in gpu_types:
                primary_gpu = preference
                break
        gpu = [r for r in readings if r[0] == primary_gpu]

        cpu_temps = [r for r in cpu if r[1] == "Temperature"]
        cpu_temp = pick(cpu_temps, CPU_TEMP_PREFERENCE)
        if cpu_temp is not None:
            out["cpu.temp"] = cpu_temp
        for _, _, name, value in cpu_temps:
            if name == "CPU Package":
                out["cpu.temp_package"] = value  # hot-spot, for theme use
        for _, stype, name, value in cpu:
            if stype == "Load" and name == "CPU Total":
                out["cpu.load"] = value
            elif stype == "Power" and name in ("Package", "CPU Package"):
                out["cpu.power_w"] = value

        gpu_temp = pick([r for r in gpu if r[1] == "Temperature"],
                        GPU_TEMP_PREFERENCE)
        if gpu_temp is not None:
            out["gpu.temp"] = gpu_temp
        for _, stype, name, value in gpu:
            if stype == "Load" and name == "GPU Core":
                out["gpu.load"] = value
            elif stype == "Power" and name in ("GPU Package", "GPU Power"):
                out["gpu.power_w"] = value
            elif stype == "Fan" and "fan.gpu" not in out:
                out["fan.gpu"] = value

        fans = [r for r in readings
                if r[1] == "Fan" and not r[0].startswith("Gpu")]
        for i, (_, _, name, value) in enumerate(fans):
            out[f"fan.{i}"] = value

        return out
