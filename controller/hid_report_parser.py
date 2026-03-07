"""
hid_report_parser.py — Transport-aware HID report parser for DualSense Edge.

DualSense Edge HID input report formats:
  USB  (0x01, 64 bytes): fields at native offsets below
  BT   (0x31, 78 bytes): extra byte at raw[1] shifts all subsequent fields +1
  The extra 14 bytes in BT (78-64) are additional fields at the end of the report.

USB canonical offsets are defined in USB_OFFSETS.  BT_OFFSETS = USB + 1 for every field.
Use detect_transport() on the first raw report to determine the transport, then
pass the TransportType to parse_report() for all subsequent reports in the session.

Note on IMU offsets:
  pydualsense has a BT IMU parsing bug: it reads accel/gyro from raw inReport[16:]
  instead of the normalized states[16:] (which has the +1 BT shift applied).
  This module uses the correct offset tables. DualSenseReader.poll() uses ds.states
  directly to bypass the pydualsense bug.
"""

import enum
import struct


class TransportType(enum.Enum):
    USB       = "usb"        # Report ID 0x01, 64 bytes
    BLUETOOTH = "bt"         # Report ID 0x31, 78 bytes
    UNKNOWN   = "unknown"    # Unrecognised format; parsed as USB with warning


# ---------------------------------------------------------------------------
# Canonical byte offsets (USB).  BT = USB offset + 1 for every field.
# Community-documented DualSense Edge USB HID report layout.
# ---------------------------------------------------------------------------
USB_OFFSETS: dict[str, int] = dict(
    lx=1,         ly=2,
    rx=3,         ry=4,
    l2=5,         r2=6,
    buttons_0=8,  buttons_1=9,
    # IMU: gyro at [16-21], accel at [22-27] in USB report
    gyro_x=16,  gyro_y=18,  gyro_z=20,
    accel_x=22, accel_y=24, accel_z=26,
)

BT_OFFSETS: dict[str, int] = {k: v + 1 for k, v in USB_OFFSETS.items()}


# ---------------------------------------------------------------------------
# Transport detection
# ---------------------------------------------------------------------------

def detect_transport(raw: bytes) -> TransportType:
    """
    Auto-detect transport type from the first byte and length of a raw HID report.

    Args:
        raw: Raw HID report bytes as received from hidapi.read().

    Returns:
        TransportType.USB (64 bytes, ID 0x01), BLUETOOTH (78 bytes, ID 0x31),
        or UNKNOWN for anything else.
    """
    if len(raw) == 64 and raw[0] == 0x01:
        return TransportType.USB
    if len(raw) == 78 and raw[0] == 0x31:
        return TransportType.BLUETOOTH
    return TransportType.UNKNOWN


# ---------------------------------------------------------------------------
# Report parser
# ---------------------------------------------------------------------------

def parse_report(raw: bytes, transport: TransportType | None = None) -> dict:
    """
    Parse a raw DualSense Edge HID input report into a transport-independent dict.

    For the same physical controller state, parse_report() returns identical field
    values regardless of whether the transport is USB or Bluetooth.

    Args:
        raw:       Raw bytes from hidapi.read().
        transport: If None, auto-detected from raw. Specify explicitly to avoid
                   the detection overhead on every report in a session.

    Returns:
        dict with keys: transport, lx, ly, rx, ry, l2, r2, buttons_0, buttons_1,
        gyro_x, gyro_y, gyro_z, accel_x, accel_y, accel_z.
        IMU values are raw int16 (not scaled).
    """
    if transport is None:
        transport = detect_transport(raw)

    if transport == TransportType.BLUETOOTH:
        off = BT_OFFSETS
    else:
        off = USB_OFFSETS  # also used for UNKNOWN (best-effort fallback)

    def _u8(key: str) -> int:
        idx = off[key]
        return raw[idx] if len(raw) > idx else 0

    def _i16(key: str) -> int:
        idx = off[key]
        if len(raw) >= idx + 2:
            return struct.unpack_from("<h", raw, idx)[0]
        return 0

    return {
        "transport":  transport.value,
        "lx":         _u8("lx"),
        "ly":         _u8("ly"),
        "rx":         _u8("rx"),
        "ry":         _u8("ry"),
        "l2":         _u8("l2"),
        "r2":         _u8("r2"),
        "buttons_0":  _u8("buttons_0"),
        "buttons_1":  _u8("buttons_1"),
        "gyro_x":     _i16("gyro_x"),
        "gyro_y":     _i16("gyro_y"),
        "gyro_z":     _i16("gyro_z"),
        "accel_x":    _i16("accel_x"),
        "accel_y":    _i16("accel_y"),
        "accel_z":    _i16("accel_z"),
    }
