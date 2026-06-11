"""Wire-format tests for the AX206 protocol port.

Expected byte sequences are derived by hand from reference/drv_dpf.c
(g_buf / g_excmd / wrap_scsi), so these tests pin the port to the
reference implementation without needing hardware.
"""

import pytest

from ax206panel import device


def test_cbw_get_lcd_params():
    # dpf_ax_open() sends cdb[0]=0xcd, cdb[5]=2 and reads 5 bytes
    # (drv_dpf.c:628-636). wrap_scsi() wraps it in the g_buf CBW with
    # dCBWDataTransferLength=5, flags=0, LUN=0, CB length=0x10.
    cdb = device.cdb_get_lcd_params()
    cbw = device.build_cbw(cdb, 5)
    expected = (
        b"USBC"                  # dCBWSignature
        b"\xde\xad\xbe\xef"      # dCBWTag
        b"\x05\x00\x00\x00"      # dCBWDataTransferLength = 5
        b"\x00"                  # bmCBWFlags (reference leaves 0 even for IN)
        b"\x00"                  # bCBWLUN
        b"\x10"                  # bCBWCBLength = 16
        b"\xcd\x00\x00\x00\x00\x02"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    )
    assert cbw == expected
    assert len(cbw) == 31


def test_cdb_set_backlight():
    # dpf_ax_setbacklight(7): cdb[6]=0x01, property 0x0001, value 7
    # (drv_dpf.c:716-732)
    cdb = device.cdb_set_property(device.PROPERTY_BRIGHTNESS, 7)
    assert cdb == bytes(
        [0xCD, 0, 0, 0, 0, 6,
         0x01,        # USBCMD_SETPROPERTY
         0x01, 0x00,  # PROPERTY_BRIGHTNESS, little-endian
         0x07, 0x00,  # value 7, little-endian
         0, 0, 0, 0, 0])


def test_cdb_blit_full_frame_480x320():
    # dpf_ax_screen_blit() with rect [0, 0, 480, 320]: end coordinates are
    # sent minus one (drv_dpf.c:698-707): 479=0x01df, 319=0x013f.
    cdb = device.cdb_blit(0, 0, 480, 320)
    assert cdb == bytes(
        [0xCD, 0, 0, 0, 0, 6,
         0x12,        # USBCMD_BLIT
         0x00, 0x00,  # x0
         0x00, 0x00,  # y0
         0xDF, 0x01,  # x1 - 1 = 479
         0x3F, 0x01,  # y1 - 1 = 319
         0x00])


def test_cbw_blit_data_length():
    # Full 480x320 frame = 480*320*2 = 307200 = 0x0004b000 bytes,
    # little-endian in dCBWDataTransferLength (drv_dpf.c:764).
    cbw = device.build_cbw(device.cdb_blit(0, 0, 480, 320), 480 * 320 * 2)
    assert cbw[8:12] == b"\x00\xb0\x04\x00"


def test_cdb_blit_offset_rect():
    # Non-zero origin exercises the low/high byte split of all four fields.
    cdb = device.cdb_blit(16, 300, 480, 320)
    assert cdb[7:9] == b"\x10\x00"    # x0 = 16
    assert cdb[9:11] == b"\x2c\x01"   # y0 = 300
    assert cdb[11:13] == b"\xdf\x01"  # x1 - 1 = 479
    assert cdb[13:15] == b"\x3f\x01"  # y1 - 1 = 319


def test_build_cbw_rejects_bad_cdb_length():
    with pytest.raises(ValueError):
        device.build_cbw(b"\xcd" * 15, 0)
