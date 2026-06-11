"""AX206 USB LCD protocol driver.

Ported from drv_dpf.c (lcd4linux), specifically the embedded
"dpfcore4driver.c" section. The reference source is checked in at
reference/drv_dpf.c (fetched from https://github.com/amd989/lcd4linux-ax206);
line numbers in comments below refer to that file.

Transport: the hacked AX206 firmware speaks USB Mass Storage Bulk-Only
Transport (BOT) framing on bulk endpoints OUT=0x01 / IN=0x81. Every command
is up to three bulk transfers (wrap_scsi(), drv_dpf.c:753):

  1. A 31-byte CBW (Command Block Wrapper) on the OUT endpoint, embedding a
     16-byte vendor CDB whose opcode is 0xcd.
  2. An optional data phase: bulk OUT (blit pixel data) or bulk IN
     (parameter reads).
  3. A 13-byte CSW (Command Status Wrapper) read from the IN endpoint;
     must start with "USBS", byte 12 is the SCSI status code. The reference
     retries this read up to 5 times.

Vendor CDB layout (all multi-byte fields little-endian):

  cdb[0] = 0xcd                vendor opcode
  cdb[5] = 2                   "get LCD parameters" (drv_dpf.c:635):
                               read 5 bytes -> width u16, height u16, 1 spare
  cdb[5] = 6                   extended command (g_excmd, drv_dpf.c:680),
                               subcommand in cdb[6]:
    cdb[6] = 0x01 SETPROPERTY  cdb[7:9] = property id (0x01 = brightness),
                               cdb[9:11] = value 0..7   (drv_dpf.c:716)
    cdb[6] = 0x12 BLIT         cdb[7:9]=x0, cdb[9:11]=y0, cdb[11:13]=x1-1,
                               cdb[13:15]=y1-1, cdb[15]=0; data phase is
                               (x1-x0)*(y1-y0)*2 bytes of RGB565
                               (drv_dpf.c:692)

RGB565 byte order (for the blit data phase, drv_dpf.c:149):
  byte 0 = (R & 0xf8)      | (G & 0xe0) >> 5     (high byte first)
  byte 1 = (G & 0x1c) << 3 | (B & 0xf8) >> 3
"""

from __future__ import annotations

import logging
import struct

import usb.core
import usb.util

log = logging.getLogger(__name__)

# drv_dpf.c:516
AX206_VID = 0x1908
AX206_PID = 0x0102

# drv_dpf.c:519
USBCMD_SETPROPERTY = 0x01
USBCMD_BLIT = 0x12
PROPERTY_BRIGHTNESS = 0x01  # drv_dpf.c:726

# drv_dpf.c:750
ENDPT_OUT = 0x01
ENDPT_IN = 0x81

# BOT framing constants (g_buf, drv_dpf.c:735)
CBW_SIGNATURE = b"USBC"
CBW_TAG = b"\xde\xad\xbe\xef"  # fixed tag, never varied by the reference
CSW_SIGNATURE = b"USBS"
CSW_LEN = 13

# Timeouts in ms, matching the libusb_bulk_transfer calls in wrap_scsi()
CBW_TIMEOUT = 1000
DATA_OUT_TIMEOUT = 3000
DATA_IN_TIMEOUT = 4000
CSW_TIMEOUT = 5000
CSW_RETRIES = 5


class DeviceError(Exception):
    """Base class for AX206 device errors."""


class BackendError(DeviceError):
    """No usable libusb backend DLL was found."""


class DeviceNotFoundError(DeviceError):
    """No AX206 device (1908:0102) on the bus."""


class DeviceBusyError(DeviceError):
    """Interface 0 could not be claimed (another app holds the device)."""


class ProtocolError(DeviceError):
    """Device sent an invalid or error reply."""


def _find_libusb0_dll() -> str | None:
    """Search candidate paths for libusb0.dll beyond the default DLL search.

    pyusb's libusb0 backend calls ctypes.CDLL('libusb0') which only finds
    the DLL if it is on PATH or in System32. This helper also checks common
    locations where package managers (vcpkg via scoop, chocolatey, etc.)
    install the library.
    """
    import os
    import glob as _glob

    candidates: list[str] = []

    # vcpkg installed via scoop: scoop installs to a per-user Apps directory.
    scoop_root = os.path.expandvars(r"%USERPROFILE%\scoop\apps\vcpkg\current")
    candidates.append(
        os.path.join(scoop_root, r"installed\x64-windows\bin\libusb0.dll"))
    candidates.append(
        os.path.join(scoop_root, r"installed\x86-windows\bin\libusb0.dll"))

    # vcpkg installed stand-alone at a few common roots.
    for root in (r"C:\vcpkg", r"C:\src\vcpkg",
                 os.path.expandvars(r"%LOCALAPPDATA%\vcpkg")):
        candidates.append(
            os.path.join(root, r"installed\x64-windows\bin\libusb0.dll"))
        candidates.append(
            os.path.join(root, r"installed\x86-windows\bin\libusb0.dll"))

    # Chocolatey.
    candidates.append(
        r"C:\ProgramData\chocolatey\lib\libusb-win32\lib\libusb0.dll")

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def get_backend(name: str = "auto"):
    """Return (pyusb backend, backend name).

    Prefers libusb0 because the Zadig-installed driver for AIDA64 use is
    libusb-win32 (libusb0.dll); WinUSB/libusb-1.0 is a fallback only.
    Also searches vcpkg/scoop/chocolatey install trees so that
    'vcpkg install libusb-win32' works without adding the DLL to PATH.
    """
    errors: list[str] = []

    if name in ("auto", "libusb0"):
        import usb.backend.libusb0 as libusb0

        # First try the default DLL search (PATH / System32).
        be = None
        try:
            be = libusb0.get_backend()
        except Exception as exc:  # pragma: no cover
            errors.append(f"libusb0 default load: {exc}")

        # Fall back to an explicit path from known package manager locations.
        if be is None:
            dll_path = _find_libusb0_dll()
            if dll_path:
                log.debug("libusb0: found via package manager at %s", dll_path)
                try:
                    be = libusb0.get_backend(
                        find_library=lambda _: dll_path)
                except Exception as exc:  # pragma: no cover
                    errors.append(f"libusb0 explicit load ({dll_path}): {exc}")

        if be is not None:
            return be, "libusb0"
        errors.append(
            "libusb0: libusb0.dll not found on PATH/System32 or in common "
            "vcpkg/scoop/chocolatey locations (install libusb-win32 for the "
            "device via Zadig, or run 'vcpkg install libusb-win32' and add "
            "its bin dir to PATH)")
        if name == "libusb0":
            raise BackendError("; ".join(errors))

    if name in ("auto", "libusb1"):
        try:
            import usb.backend.libusb1 as libusb1

            be = libusb1.get_backend()
        except Exception as exc:  # pragma: no cover
            be = None
            errors.append(f"libusb1: {exc}")
        if be is not None:
            if name == "auto":
                log.warning("libusb0 backend unavailable, falling back to "
                            "libusb1 (device must be bound to WinUSB)")
            return be, "libusb1"
        errors.append("libusb1: libusb-1.0.dll not found")

    raise BackendError(
        "no libusb backend available: " + "; ".join(errors) +
        ". On the machine with the display, install libusb-win32 for the "
        "device via Zadig (the same setup AIDA64 uses); that places "
        "libusb0.dll in System32."
    )


def build_cbw(cdb: bytes, data_len: int) -> bytes:
    """Build the 31-byte Command Block Wrapper around a 16-byte CDB.

    Mirrors g_buf + the per-call patching in wrap_scsi() (drv_dpf.c:735-767).
    bmCBWFlags stays 0x00 even for IN transfers: the reference never sets
    bit 7 and the firmware does not check it.
    """
    if len(cdb) != 16:
        raise ValueError(f"CDB must be 16 bytes, got {len(cdb)}")
    return b"".join((
        CBW_SIGNATURE,                 # dCBWSignature
        CBW_TAG,                       # dCBWTag
        struct.pack("<I", data_len),   # dCBWDataTransferLength
        b"\x00",                       # bmCBWFlags (always 0, see above)
        b"\x00",                       # bCBWLUN
        bytes((len(cdb),)),            # bCBWCBLength = 0x10
        cdb,
    ))


def cdb_get_lcd_params() -> bytes:
    """CDB for "get LCD parameters" (drv_dpf.c:629-635)."""
    cdb = bytearray(16)
    cdb[0] = 0xCD
    cdb[5] = 2
    return bytes(cdb)


def _cdb_extended(subcommand: int) -> bytearray:
    """Base extended CDB, g_excmd with cdb[6] = subcommand (drv_dpf.c:680)."""
    cdb = bytearray(16)
    cdb[0] = 0xCD
    cdb[5] = 6
    cdb[6] = subcommand
    return cdb


def cdb_set_property(prop: int, value: int) -> bytes:
    """CDB for SETPROPERTY (dpf_ax_setbacklight, drv_dpf.c:716-732)."""
    cdb = _cdb_extended(USBCMD_SETPROPERTY)
    cdb[7] = prop & 0xFF
    cdb[8] = (prop >> 8) & 0xFF
    cdb[9] = value & 0xFF
    cdb[10] = (value >> 8) & 0xFF
    return bytes(cdb)


def cdb_blit(x0: int, y0: int, x1: int, y1: int) -> bytes:
    """CDB for BLIT of rect [x0,y0)..(x1,y1) exclusive (drv_dpf.c:692-710)."""
    cdb = _cdb_extended(USBCMD_BLIT)
    cdb[7] = x0 & 0xFF
    cdb[8] = (x0 >> 8) & 0xFF
    cdb[9] = y0 & 0xFF
    cdb[10] = (y0 >> 8) & 0xFF
    cdb[11] = (x1 - 1) & 0xFF
    cdb[12] = ((x1 - 1) >> 8) & 0xFF
    cdb[13] = (y1 - 1) & 0xFF
    cdb[14] = ((y1 - 1) >> 8) & 0xFF
    cdb[15] = 0
    return bytes(cdb)


def find_ax206(backend) -> list:
    """Return all AX206 devices on the bus (may be empty)."""
    return list(usb.core.find(find_all=True, idVendor=AX206_VID,
                              idProduct=AX206_PID, backend=backend))


class AX206Device:
    """One opened AX206 display. Use as a context manager or call close()."""

    def __init__(self, usb_dev):
        self._dev = usb_dev
        self.width: int = 0
        self.height: int = 0

    @classmethod
    def open(cls, index: int = 0, backend=None) -> "AX206Device":
        """Find AX206 #index on the bus, claim it and read its resolution.

        Equivalent to dpf_ax_open() (drv_dpf.c:548-646).
        """
        devs = find_ax206(backend)
        if not devs:
            raise DeviceNotFoundError(
                f"no AX206 device ({AX206_VID:04x}:{AX206_PID:04x}) found. "
                "Run --list-devices to see what the backend can see; note "
                "that the libusb0 backend only sees devices bound to the "
                "libusb-win32 driver."
            )
        if index >= len(devs):
            raise DeviceNotFoundError(
                f"AX206 index {index} requested but only {len(devs)} found")
        self = cls(devs[index])
        self._claim()
        self.width, self.height = self._get_lcd_params()
        log.info("AX206 opened: %dx%d", self.width, self.height)
        return self

    def _claim(self) -> None:
        try:
            self._dev.set_configuration()
        except usb.core.USBError as exc:
            # Often fails harmlessly if the device is already configured;
            # the claim below is the real exclusivity check.
            log.debug("set_configuration: %s (continuing)", exc)
        try:
            usb.util.claim_interface(self._dev, 0)  # drv_dpf.c:619
        except usb.core.USBError as exc:
            raise DeviceBusyError(
                "could not claim interface 0 - is AIDA64 (or another "
                f"instance of this app) still using the display? ({exc})"
            ) from exc

    def close(self) -> None:
        """Release the interface (dpf_ax_close, drv_dpf.c:652)."""
        if self._dev is None:
            return
        try:
            usb.util.release_interface(self._dev, 0)
        except usb.core.USBError as exc:
            log.debug("release_interface: %s", exc)
        usb.util.dispose_resources(self._dev)
        self._dev = None

    def __enter__(self) -> "AX206Device":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- protocol ---------------------------------------------------------

    def _wrap_scsi(self, cdb: bytes, data_out: bytes | None = None,
                   read_len: int = 0) -> bytes | None:
        """CBW -> optional data phase -> CSW. Port of wrap_scsi()."""
        dev = self._dev
        if dev is None:
            raise DeviceError("device is closed")
        data_len = len(data_out) if data_out is not None else read_len

        dev.write(ENDPT_OUT, build_cbw(cdb, data_len), CBW_TIMEOUT)

        data_in: bytes | None = None
        if data_out is not None:
            written = dev.write(ENDPT_OUT, data_out, DATA_OUT_TIMEOUT)
            if written != len(data_out):
                raise ProtocolError(
                    f"short bulk write: {written}/{len(data_out)} bytes")
        elif read_len:
            data_in = bytes(dev.read(ENDPT_IN, read_len, DATA_IN_TIMEOUT))
            if len(data_in) != read_len:
                raise ProtocolError(
                    f"short bulk read: {len(data_in)}/{read_len} bytes")

        csw = None
        for attempt in range(1, CSW_RETRIES + 1):
            try:
                csw = bytes(dev.read(ENDPT_IN, CSW_LEN, CSW_TIMEOUT))
            except usb.core.USBError as exc:
                log.warning("CSW read failed (attempt %d/%d): %s",
                            attempt, CSW_RETRIES, exc)
                continue
            if len(csw) == CSW_LEN:
                break
            log.warning("short CSW (%d bytes, attempt %d/%d)",
                        len(csw), attempt, CSW_RETRIES)
        if csw is None or len(csw) != CSW_LEN:
            raise ProtocolError("no CSW reply from device")
        if not csw.startswith(CSW_SIGNATURE):
            raise ProtocolError(f"invalid CSW reply: {csw.hex()}")
        status = csw[12]
        if status != 0:
            raise ProtocolError(f"device returned SCSI status {status}")
        return data_in

    def _get_lcd_params(self) -> tuple[int, int]:
        """Read width/height (drv_dpf.c:628-644)."""
        buf = self._wrap_scsi(cdb_get_lcd_params(), read_len=5)
        assert buf is not None
        width = buf[0] | (buf[1] << 8)
        height = buf[2] | (buf[3] << 8)
        return width, height

    def set_backlight(self, level: int) -> None:
        """Set backlight 0 (off) .. 7 (max), clamped like the reference."""
        level = max(0, min(7, int(level)))
        self._wrap_scsi(cdb_set_property(PROPERTY_BRIGHTNESS, level))

    def blit(self, pixels: bytes, rect: tuple[int, int, int, int] | None = None) -> None:
        """Send RGB565 pixel data for rect (x0, y0, x1, y1), x1/y1 exclusive.

        Defaults to the full frame - some firmware crashes on partial-rect
        blits (see Client-DPF-AX206), so callers should prefer full frames.
        """
        if rect is None:
            rect = (0, 0, self.width, self.height)
        x0, y0, x1, y1 = rect
        expected = (x1 - x0) * (y1 - y0) * 2
        if len(pixels) != expected:
            raise ValueError(
                f"pixel buffer is {len(pixels)} bytes, rect {rect} needs {expected}")
        self._wrap_scsi(cdb_blit(x0, y0, x1, y1), data_out=pixels)
