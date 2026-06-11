"""Enumerate USB devices and dump VID/PID, configurations and endpoints.

Standalone diagnostic - needs only pyusb. Tries the libusb0 backend first
(the Zadig/libusb-win32 driver used for AIDA64), then libusb1. Run it on
the machine the display is plugged into:

    python tools/dump_descriptors.py
"""

from __future__ import annotations

import usb.core

AX206_VID = 0x1908
AX206_PID = 0x0102


def get_backends():
    from ax206panel.device import _find_libusb0_dll
    found = []

    import usb.backend.libusb0 as libusb0
    be = None
    try:
        be = libusb0.get_backend()
    except Exception:
        pass
    if be is None:
        dll = _find_libusb0_dll()
        if dll:
            print(f"libusb0: using DLL at {dll}")
            try:
                be = libusb0.get_backend(find_library=lambda _: dll)
            except Exception:
                pass
    if be is not None:
        found.append(("libusb0", be))
    else:
        print("libusb0 backend unavailable: libusb0.dll not found")

    try:
        import usb.backend.libusb1 as libusb1
        be = libusb1.get_backend()
        if be is not None:
            found.append(("libusb1", be))
    except Exception as exc:
        print(f"libusb1 backend unavailable: {exc}")
    return found


def dump_device(dev) -> None:
    is_ax206 = dev.idVendor == AX206_VID and dev.idProduct == AX206_PID
    tag = "  <-- AX206 display" if is_ax206 else ""
    print(f"\n{dev.idVendor:04x}:{dev.idProduct:04x}  bus {dev.bus} "
          f"addr {dev.address}{tag}")
    for attr in ("manufacturer", "product", "serial_number"):
        try:
            value = getattr(dev, attr)
        except Exception:
            value = "(unreadable)"
        print(f"  {attr}: {value}")
    try:
        for cfg in dev:
            print(f"  config {cfg.bConfigurationValue}:")
            for intf in cfg:
                print(f"    interface {intf.bInterfaceNumber} "
                      f"alt {intf.bAlternateSetting} "
                      f"class 0x{intf.bInterfaceClass:02x}")
                for ep in intf:
                    direction = "IN " if ep.bEndpointAddress & 0x80 else "OUT"
                    print(f"      endpoint 0x{ep.bEndpointAddress:02x} "
                          f"{direction} maxpacket {ep.wMaxPacketSize}")
    except Exception as exc:
        print(f"  (could not read configuration: {exc})")


def main() -> None:
    backends = get_backends()
    if not backends:
        print("No libusb backend found. Install libusb-win32 via Zadig "
              "(puts libusb0.dll in System32).")
        return
    for name, backend in backends:
        devs = list(usb.core.find(find_all=True, backend=backend))
        print(f"\n===== backend {name}: {len(devs)} device(s) =====")
        if name == "libusb0":
            print("(libusb0 only sees devices bound to the libusb-win32 "
                  "driver)")
        for dev in devs:
            dump_device(dev)


if __name__ == "__main__":
    main()
