"""
Phase 61 — test_hid_report_parser.py

Tests for controller/hid_report_parser.py.

Groups:
  1. Transport detection (USB / BT / UNKNOWN)
  2. USB report parsing — byte offsets and field values
  3. BT report parsing — +1 offset shift applied correctly
  4. USB vs BT produce identical field values for same controller state
  5. Short/malformed reports — graceful fallback, no crash
  6. IMU int16 little-endian sign handling
  7. Explicit transport override skips auto-detection
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "controller"))

from hid_report_parser import (
    BT_OFFSETS,
    USB_OFFSETS,
    TransportType,
    detect_transport,
    parse_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_usb_report(
    lx=128, ly=128, rx=128, ry=128,
    l2=0, r2=0, buttons_0=0, buttons_1=0,
    gyro_x=0, gyro_y=0, gyro_z=0,
    accel_x=0, accel_y=0, accel_z=0,
) -> bytes:
    """Build a valid 64-byte USB HID report (report ID 0x01)."""
    raw = bytearray(64)
    raw[0] = 0x01
    raw[USB_OFFSETS["lx"]] = lx & 0xFF
    raw[USB_OFFSETS["ly"]] = ly & 0xFF
    raw[USB_OFFSETS["rx"]] = rx & 0xFF
    raw[USB_OFFSETS["ry"]] = ry & 0xFF
    raw[USB_OFFSETS["l2"]] = l2 & 0xFF
    raw[USB_OFFSETS["r2"]] = r2 & 0xFF
    raw[USB_OFFSETS["buttons_0"]] = buttons_0 & 0xFF
    raw[USB_OFFSETS["buttons_1"]] = buttons_1 & 0xFF
    struct.pack_into("<h", raw, USB_OFFSETS["gyro_x"], gyro_x)
    struct.pack_into("<h", raw, USB_OFFSETS["gyro_y"], gyro_y)
    struct.pack_into("<h", raw, USB_OFFSETS["gyro_z"], gyro_z)
    struct.pack_into("<h", raw, USB_OFFSETS["accel_x"], accel_x)
    struct.pack_into("<h", raw, USB_OFFSETS["accel_y"], accel_y)
    struct.pack_into("<h", raw, USB_OFFSETS["accel_z"], accel_z)
    return bytes(raw)


def _make_bt_report(
    lx=128, ly=128, rx=128, ry=128,
    l2=0, r2=0, buttons_0=0, buttons_1=0,
    gyro_x=0, gyro_y=0, gyro_z=0,
    accel_x=0, accel_y=0, accel_z=0,
) -> bytes:
    """Build a valid 78-byte BT HID report (report ID 0x31)."""
    raw = bytearray(78)
    raw[0] = 0x31
    raw[BT_OFFSETS["lx"]] = lx & 0xFF
    raw[BT_OFFSETS["ly"]] = ly & 0xFF
    raw[BT_OFFSETS["rx"]] = rx & 0xFF
    raw[BT_OFFSETS["ry"]] = ry & 0xFF
    raw[BT_OFFSETS["l2"]] = l2 & 0xFF
    raw[BT_OFFSETS["r2"]] = r2 & 0xFF
    raw[BT_OFFSETS["buttons_0"]] = buttons_0 & 0xFF
    raw[BT_OFFSETS["buttons_1"]] = buttons_1 & 0xFF
    struct.pack_into("<h", raw, BT_OFFSETS["gyro_x"], gyro_x)
    struct.pack_into("<h", raw, BT_OFFSETS["gyro_y"], gyro_y)
    struct.pack_into("<h", raw, BT_OFFSETS["gyro_z"], gyro_z)
    struct.pack_into("<h", raw, BT_OFFSETS["accel_x"], accel_x)
    struct.pack_into("<h", raw, BT_OFFSETS["accel_y"], accel_y)
    struct.pack_into("<h", raw, BT_OFFSETS["accel_z"], accel_z)
    return bytes(raw)


# ---------------------------------------------------------------------------
# Group 1: Transport detection
# ---------------------------------------------------------------------------

class TestDetectTransport(unittest.TestCase):

    def test_usb_detected_by_id_and_length(self):
        raw = _make_usb_report()
        self.assertEqual(detect_transport(raw), TransportType.USB)

    def test_bt_detected_by_id_and_length(self):
        raw = _make_bt_report()
        self.assertEqual(detect_transport(raw), TransportType.BLUETOOTH)

    def test_wrong_length_64_wrong_id(self):
        raw = bytearray(64)
        raw[0] = 0x31  # BT id but USB length
        self.assertEqual(detect_transport(bytes(raw)), TransportType.UNKNOWN)

    def test_wrong_length_78_wrong_id(self):
        raw = bytearray(78)
        raw[0] = 0x01  # USB id but BT length
        self.assertEqual(detect_transport(bytes(raw)), TransportType.UNKNOWN)

    def test_empty_report_returns_unknown(self):
        self.assertEqual(detect_transport(b""), TransportType.UNKNOWN)

    def test_arbitrary_length_unknown(self):
        self.assertEqual(detect_transport(bytes(32)), TransportType.UNKNOWN)


# ---------------------------------------------------------------------------
# Group 2: USB report parsing
# ---------------------------------------------------------------------------

class TestParseUSBReport(unittest.TestCase):

    def test_transport_field_is_usb(self):
        raw = _make_usb_report()
        result = parse_report(raw)
        self.assertEqual(result["transport"], "usb")

    def test_stick_axes_round_trip(self):
        raw = _make_usb_report(lx=10, ly=200, rx=55, ry=99)
        result = parse_report(raw)
        self.assertEqual(result["lx"], 10)
        self.assertEqual(result["ly"], 200)
        self.assertEqual(result["rx"], 55)
        self.assertEqual(result["ry"], 99)

    def test_trigger_values_round_trip(self):
        raw = _make_usb_report(l2=100, r2=200)
        result = parse_report(raw)
        self.assertEqual(result["l2"], 100)
        self.assertEqual(result["r2"], 200)

    def test_button_bytes_round_trip(self):
        raw = _make_usb_report(buttons_0=0b00100000, buttons_1=0xFF)
        result = parse_report(raw)
        self.assertEqual(result["buttons_0"], 0b00100000)
        self.assertEqual(result["buttons_1"], 0xFF)

    def test_cross_button_bit5_of_buttons_0(self):
        # Per CLAUDE.md: cross = (buttons_0 >> 5) & 1
        raw = _make_usb_report(buttons_0=0b00100000)
        result = parse_report(raw)
        self.assertEqual((result["buttons_0"] >> 5) & 1, 1)

    def test_all_fields_present(self):
        raw = _make_usb_report()
        result = parse_report(raw)
        expected_keys = {
            "transport", "lx", "ly", "rx", "ry", "l2", "r2",
            "buttons_0", "buttons_1",
            "gyro_x", "gyro_y", "gyro_z",
            "accel_x", "accel_y", "accel_z",
        }
        self.assertEqual(set(result.keys()), expected_keys)


# ---------------------------------------------------------------------------
# Group 3: BT report parsing — +1 offset shift
# ---------------------------------------------------------------------------

class TestParseBTReport(unittest.TestCase):

    def test_transport_field_is_bt(self):
        raw = _make_bt_report()
        result = parse_report(raw)
        self.assertEqual(result["transport"], "bt")

    def test_bt_offsets_are_usb_plus_one(self):
        for key in USB_OFFSETS:
            self.assertEqual(BT_OFFSETS[key], USB_OFFSETS[key] + 1, key)

    def test_bt_stick_axes_round_trip(self):
        raw = _make_bt_report(lx=77, ly=88, rx=11, ry=22)
        result = parse_report(raw)
        self.assertEqual(result["lx"], 77)
        self.assertEqual(result["ly"], 88)
        self.assertEqual(result["rx"], 11)
        self.assertEqual(result["ry"], 22)

    def test_bt_triggers_round_trip(self):
        raw = _make_bt_report(l2=42, r2=84)
        result = parse_report(raw)
        self.assertEqual(result["l2"], 42)
        self.assertEqual(result["r2"], 84)


# ---------------------------------------------------------------------------
# Group 4: USB vs BT produce identical field values for same state
# ---------------------------------------------------------------------------

class TestUSBvsBTParity(unittest.TestCase):

    def test_identical_state_same_parsed_values(self):
        kwargs = dict(
            lx=50, ly=150, rx=200, ry=100,
            l2=128, r2=64,
            buttons_0=0b01010101, buttons_1=0b10101010,
            gyro_x=1000, gyro_y=-500, gyro_z=250,
            accel_x=-2000, accel_y=100, accel_z=9800,
        )
        usb_result = parse_report(_make_usb_report(**kwargs))
        bt_result  = parse_report(_make_bt_report(**kwargs))

        for key in ("lx", "ly", "rx", "ry", "l2", "r2",
                    "buttons_0", "buttons_1",
                    "gyro_x", "gyro_y", "gyro_z",
                    "accel_x", "accel_y", "accel_z"):
            self.assertEqual(usb_result[key], bt_result[key], f"Mismatch on {key}")

    def test_transport_field_differs(self):
        usb_result = parse_report(_make_usb_report())
        bt_result  = parse_report(_make_bt_report())
        self.assertNotEqual(usb_result["transport"], bt_result["transport"])


# ---------------------------------------------------------------------------
# Group 5: Short/malformed reports — graceful fallback, no crash
# ---------------------------------------------------------------------------

class TestMalformedReports(unittest.TestCase):

    def test_empty_report_no_crash(self):
        result = parse_report(b"")
        self.assertIn("transport", result)
        # All numeric fields should be 0 (fallback)
        for key in ("lx", "ly", "rx", "ry", "l2", "r2",
                    "gyro_x", "gyro_y", "gyro_z",
                    "accel_x", "accel_y", "accel_z"):
            self.assertEqual(result[key], 0, f"Expected 0 for {key} on empty report")

    def test_truncated_usb_report_no_crash(self):
        raw = bytes(20)  # too short, unknown transport
        result = parse_report(raw)
        self.assertIn("transport", result)

    def test_unknown_transport_uses_usb_offsets(self):
        # UNKNOWN falls back to USB offsets; just check it doesn't crash
        raw = bytearray(64)
        raw[0] = 0xAA  # unknown ID
        result = parse_report(bytes(raw))
        self.assertEqual(result["transport"], "unknown")

    def test_explicit_unknown_transport_no_crash(self):
        raw = bytes(64)
        result = parse_report(raw, transport=TransportType.UNKNOWN)
        self.assertEqual(result["transport"], "unknown")


# ---------------------------------------------------------------------------
# Group 6: IMU int16 little-endian sign handling
# ---------------------------------------------------------------------------

class TestIMUSignHandling(unittest.TestCase):

    def test_positive_imu_values(self):
        raw = _make_usb_report(gyro_x=1000, gyro_y=2000, gyro_z=3000,
                               accel_x=4000, accel_y=5000, accel_z=6000)
        result = parse_report(raw)
        self.assertEqual(result["gyro_x"], 1000)
        self.assertEqual(result["gyro_y"], 2000)
        self.assertEqual(result["gyro_z"], 3000)
        self.assertEqual(result["accel_x"], 4000)
        self.assertEqual(result["accel_y"], 5000)
        self.assertEqual(result["accel_z"], 6000)

    def test_negative_imu_values(self):
        raw = _make_usb_report(gyro_x=-1000, gyro_y=-2000, gyro_z=-3000,
                               accel_x=-4000, accel_y=-5000, accel_z=-6000)
        result = parse_report(raw)
        self.assertEqual(result["gyro_x"], -1000)
        self.assertEqual(result["gyro_y"], -2000)
        self.assertEqual(result["gyro_z"], -3000)
        self.assertEqual(result["accel_x"], -4000)
        self.assertEqual(result["accel_y"], -5000)
        self.assertEqual(result["accel_z"], -6000)

    def test_int16_min_max_values(self):
        raw = _make_usb_report(gyro_x=-32768, accel_z=32767)
        result = parse_report(raw)
        self.assertEqual(result["gyro_x"], -32768)
        self.assertEqual(result["accel_z"], 32767)


# ---------------------------------------------------------------------------
# Group 7: Explicit transport override skips auto-detection
# ---------------------------------------------------------------------------

class TestTransportOverride(unittest.TestCase):

    def test_force_usb_transport_on_usb_report(self):
        raw = _make_usb_report(lx=42)
        result = parse_report(raw, transport=TransportType.USB)
        self.assertEqual(result["transport"], "usb")
        self.assertEqual(result["lx"], 42)

    def test_force_bt_transport_on_bt_report(self):
        raw = _make_bt_report(rx=99)
        result = parse_report(raw, transport=TransportType.BLUETOOTH)
        self.assertEqual(result["transport"], "bt")
        self.assertEqual(result["rx"], 99)

    def test_override_avoids_misparse(self):
        # Build a BT report but parse it as BT explicitly — should give correct values
        raw = _make_bt_report(lx=77)
        result_auto     = parse_report(raw)
        result_explicit = parse_report(raw, transport=TransportType.BLUETOOTH)
        self.assertEqual(result_auto["lx"], result_explicit["lx"])


if __name__ == "__main__":
    unittest.main()
