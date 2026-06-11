"""Download LibreHardwareMonitorLib.dll and all its dependencies into <repo>/lib.

LibreHardwareMonitorLib's .NET Framework build needs several companion
NuGet assemblies at runtime (HidSharp, System.Memory, System.Management,
...), so this walks the nuspec dependency tree recursively and extracts
the best-matching DLL from each package. pythonnet on Windows runs on
.NET Framework, so net4x builds are preferred, then netstandard2.0.

LibreHardwareMonitorLib is MPL-2.0; downloading at setup time avoids
vendoring binaries in this repo.

    python tools/fetch_lhm.py
"""

from __future__ import annotations

import io
import json
import platform
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
FLAT = "https://api.nuget.org/v3-flatcontainer"

ARCH = {"AMD64": "x64", "ARM64": "arm64", "x86": "x86"}.get(
    platform.machine(), "x64")

ROOT_PACKAGE = "librehardwaremonitorlib"

# Meta/facade packages that ship no runtime DLLs worth extracting
SKIP_PACKAGES = {"netstandard.library", "microsoft.netcore.platforms"}


def http_get(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def latest_stable_version(package: str) -> str:
    versions = json.loads(http_get(f"{FLAT}/{package}/index.json"))["versions"]
    stable = [v for v in versions if "-" not in v]
    return (stable or versions)[-1]


def tfm_rank(prefix: str, tfm: str) -> int:
    """Higher = better for a .NET Framework 4.7.2 host. 0 = unusable."""
    bonus = 5000 if prefix.startswith("runtimes/") else 0
    if tfm == "netstandard2.0":
        return bonus + 1
    match = re.fullmatch(r"net(\d+)", tfm)  # net8.0-style TFMs don't match
    if match:
        version = int(match.group(1).ljust(3, "0"))  # net47 -> 470
        if version <= 480:
            return bonus + 1000 + version
    return 0


def extract_dlls(zf: zipfile.ZipFile) -> list[str]:
    """Extract DLLs from the best framework folder; return extracted names."""
    folders: dict[tuple[str, str], list[str]] = {}
    for name in zf.namelist():
        match = re.fullmatch(
            rf"((?:runtimes/win-{ARCH}/)?)lib/([^/]+)/([^/]+\.dll)", name)
        if match:
            folders.setdefault((match.group(1), match.group(2)), []).append(name)

    if not folders:
        return []
    best = max(folders, key=lambda k: tfm_rank(*k))
    if tfm_rank(*best) == 0:
        return []
    extracted = []
    for entry in folders[best]:
        target = LIB_DIR / Path(entry).name
        target.write_bytes(zf.read(entry))
        extracted.append(Path(entry).name)
    return extracted


def parse_dependencies(nuspec: bytes) -> list[tuple[str, str]]:
    """Return (package id, version) deps for the net472-ish group."""
    root = ET.fromstring(nuspec)
    groups: dict[str, list[tuple[str, str]]] = {}
    for elem in root.iter():
        if not elem.tag.endswith("}group"):
            continue
        tfm = elem.get("targetFramework", "")
        deps = []
        for dep in elem:
            if dep.tag.endswith("}dependency"):
                # version may be a range like "[1.0, 2.0)"; take the floor
                raw = dep.get("version", "")
                version = next((t for t in re.split(r"[\[\](),\s]+", raw) if t), "")
                deps.append((dep.get("id", "").lower(), version))
        groups[tfm] = deps

    for tfm in sorted((t for t in groups if t.startswith(".NETFramework")),
                      reverse=True):
        return groups[tfm]
    if ".NETStandard2.0" in groups:
        return groups[".NETStandard2.0"]
    return []


def fetch(package: str, version: str | None, done: set[str]) -> None:
    if package in done or package in SKIP_PACKAGES:
        return
    done.add(package)
    if not version:
        version = latest_stable_version(package)
    print(f"downloading {package} {version} ...")
    data = http_get(f"{FLAT}/{package}/{version}/{package}.{version}.nupkg")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        dlls = extract_dlls(zf)
        for dll in dlls:
            print(f"  -> lib/{dll}")
        if not dlls:
            print("  (no runtime DLLs - facade package, skipped)")
        nuspec_name = next(n for n in zf.namelist()
                           if n.endswith(".nuspec") and "/" not in n)
        deps = parse_dependencies(zf.read(nuspec_name))
    for dep_id, dep_version in deps:
        fetch(dep_id, dep_version, done)


def main() -> None:
    LIB_DIR.mkdir(exist_ok=True)
    fetch(ROOT_PACKAGE, None, set())
    print(f"\ndone - DLLs in {LIB_DIR}")


if __name__ == "__main__":
    main()
