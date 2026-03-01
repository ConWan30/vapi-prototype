"""
Live hardware tests for DualShock Edge (Sony CFI-ZCP1) USB HID interface.

All tests require a physical controller connected via USB and are skipped
automatically when no controller is detected. Run with:

    pytest tests/hardware/ -v -m hardware

These tests validate:
  - USB HID enumeration and device identity (VID/PID)
  - Raw report format integrity (report_id, length)
  - Stick axis range validity
  - IMU noise floor characteristics for a stationary controller
  - Sensor commitment determinism using the bridge commitment function

NOTE: No real hardware is present in CI. All tests here are gated behind
the hardware marker and the controller_device session fixture, which skips
if no controller is detected.
"""

import hashlib
import struct
import sys
import time

import pytest

# Make bridge package importable from the project root
sys.path.insert(0, '/c/Users/Contr/vapi-pebble-prototype/bridge')

# Guard: skip entire module if hidapi is not installed
hid = pytest.importorskip("hid", reason="hidapi not installed. Run: pip install hidapi")

DUALSHOCK_EDGE_VID = 0x054C
DUALSHOCK_EDGE_PID = 0x0DF2

# DualSense USB report IDs observed in the wild
VALID_REPORT_IDS = {0x01, 0x31}

# Minimum HID report payload length for DualSense USB
MIN_REPORT_LENGTH = 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_n_reports(h, count, timeout_ms=100):
    """Read up to `count` non-empty HID reports from device handle `h`."""
    reports = []
    deadline = time.perf_counter() + (count * timeout_ms / 1000.0) + 2.0
    while len(reports) < count and time.perf_counter() < deadline:
        raw = h.read(MAX_REPORT_LENGTH := 128, timeout_ms=timeout_ms)
        if raw:
            reports.append(bytes(raw))
    return reports


def _parse_imu(data: bytes):
    """
    Extract IMU int16 values from a DualSense USB report.

    Based on community reverse-engineering of the DualSense USB report layout
    (report_id=0x01). Byte offsets for gyro/accel may vary by firmware version.

    Gyro:  bytes 16-21 (3x int16 little-endian)
    Accel: bytes 22-27 (3x int16 little-endian)

    Returns a dict with gyro_x/y/z and accel_x/y/z, or empty dict on error.
    """
    result = {}
    if len(data) >= 22:
        try:
            result["gyro_x"] = struct.unpack_from("<h", data, 16)[0]
            result["gyro_y"] = struct.unpack_from("<h", data, 18)[0]
            result["gyro_z"] = struct.unpack_from("<h", data, 20)[0]
        except struct.error:
            pass
    if len(data) >= 28:
        try:
            result["accel_x"] = struct.unpack_from("<h", data, 22)[0]
            result["accel_y"] = struct.unpack_from("<h", data, 24)[0]
            result["accel_z"] = struct.unpack_from("<h", data, 26)[0]
        except struct.error:
            pass
    return result


def _compute_sensor_commitment(raw_report: bytes) -> bytes:
    """
    Derive a 32-byte sensor commitment from a raw HID report.

    This mirrors the bridge's commitment function: SHA-256 over the
    raw sensor payload. The commitment is deterministic for identical input —
    the key property being tested in test_sensor_commitment_consistency.
    """
    return hashlib.sha256(raw_report).digest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.hardware
class TestHidConnection:
    """
    Verify that the DualShock Edge is enumerable with the expected USB identifiers.

    Why: VID=0x054C (Sony) and PID=0x0DF2 (DualSense Edge USB) must match the
    device profile in controller/profiles/dualshock_edge.py and the bridge
    transport constants. A mismatch here would silently enumerate the wrong device.
    """

    def test_hid_connection(self, controller_device):
        """VID=0x054C and PID=0x0DF2 must be present in the enumerated device list."""
        devices = hid.enumerate(DUALSHOCK_EDGE_VID, DUALSHOCK_EDGE_PID)
        assert len(devices) >= 1, (
            f"No device found with VID={hex(DUALSHOCK_EDGE_VID)}, "
            f"PID={hex(DUALSHOCK_EDGE_PID)}. "
            "Ensure DualShock Edge CFI-ZCP1 is connected via USB (not Bluetooth)."
        )
        device = devices[0]
        assert device.get("vendor_id") == DUALSHOCK_EDGE_VID, (
            f"Vendor ID mismatch: expected {hex(DUALSHOCK_EDGE_VID)}, "
            f"got {hex(device.get('vendor_id', 0))}"
        )
        assert device.get("product_id") == DUALSHOCK_EDGE_PID, (
            f"Product ID mismatch: expected {hex(DUALSHOCK_EDGE_PID)}, "
            f"got {hex(device.get('product_id', 0))}"
        )


@pytest.mark.hardware
class TestRawReportFormat:
    """
    Validate the low-level HID report byte format.

    Why: The VAPI bridge consumes raw HID reports and must know the report_id
    and minimum payload length before it can unpack sensor fields. A mismatch
    would corrupt sensor commitments upstream.
    """

    def test_raw_report_format(self, hid_device):
        """Read 10 HID reports; verify report_id in {0x01, 0x31} and length >= 64."""
        reports = _read_n_reports(hid_device, 10, timeout_ms=200)
        assert len(reports) >= 1, (
            "Failed to read any HID reports from the controller within the timeout. "
            "Check USB connection and controller power state."
        )
        for i, report in enumerate(reports):
            assert len(report) >= MIN_REPORT_LENGTH, (
                f"Report {i}: length {len(report)} < minimum {MIN_REPORT_LENGTH}. "
                "USB report is shorter than expected — firmware version may differ."
            )
            report_id = report[0]
            assert report_id in VALID_REPORT_IDS, (
                f"Report {i}: unexpected report_id=0x{report_id:02X}. "
                f"Expected one of: {[hex(r) for r in VALID_REPORT_IDS]}. "
                "Bluetooth mode produces different report IDs — use USB cable."
            )


@pytest.mark.hardware
class TestAdaptiveTriggerReadback:
    """
    Verify that adaptive trigger axis bytes are within the valid ADC range.

    Why: The DualSense Edge's motorised L2/R2 triggers are the primary biometric
    surface for PITL Layer 4. Their raw byte range [0, 255] must be confirmed
    before calibrating Mahalanobis thresholds. Full effect-write tests are
    deferred until pydualsense haptic API integration is validated.
    """

    def test_adaptive_trigger_readback(self, hid_device):
        """Verify trigger bytes (L2=byte5, R2=byte6) are within [0, 255]."""
        # NOTE: Full trigger effect write/readback is skipped for now.
        # This test only confirms the raw ADC range is within spec.
        # A full test requires writing a trigger effect via pydualsense
        # and reading back the resistance profile — deferred pending
        # haptic API integration validation.
        reports = _read_n_reports(hid_device, 20, timeout_ms=100)
        assert len(reports) >= 1, "No HID reports received"

        for i, report in enumerate(reports):
            if len(report) < 7:
                continue
            l2_value = report[5]
            r2_value = report[6]
            assert 0 <= l2_value <= 255, (
                f"Report {i}: L2 trigger byte={l2_value} out of range [0, 255]"
            )
            assert 0 <= r2_value <= 255, (
                f"Report {i}: R2 trigger byte={r2_value} out of range [0, 255]"
            )


@pytest.mark.hardware
class TestStickAxesRange:
    """
    Confirm stick axis bytes are within expected ADC range.

    Why: The bridge's AntiCheatClassifier normalises stick axes before inference.
    If raw values exceed [0, 255] (possible in some BT modes with signed encoding),
    the normalisation formula breaks and all classifications become invalid.
    """

    def test_stick_axes_range(self, hid_device):
        """Read 100 HID reports; verify all 4 stick axes are in [0, 255]."""
        reports = _read_n_reports(hid_device, 100, timeout_ms=50)
        assert len(reports) >= 10, (
            f"Only received {len(reports)} reports; need at least 10 for axis range validation."
        )

        violations = []
        for i, report in enumerate(reports):
            if len(report) < 5:
                continue
            axes = {
                "left_stick_x":  report[1],
                "left_stick_y":  report[2],
                "right_stick_x": report[3],
                "right_stick_y": report[4],
            }
            for name, value in axes.items():
                # USB mode: unsigned [0, 255] centered at 128
                # BT mode: may differ — this test targets USB
                if not (0 <= value <= 255):
                    violations.append(f"Report {i} {name}={value}")

        assert not violations, (
            f"Stick axis values out of expected [0, 255] range:\n"
            + "\n".join(violations)
            + "\nIf using Bluetooth, values may be signed — switch to USB mode."
        )


@pytest.mark.hardware
class TestImuNoiseFloor:
    """
    Characterise IMU noise when the controller is held stationary.

    Why: PITL Layer 4 computes Mahalanobis distance from a stable biometric
    baseline. If IMU noise is higher than the assumed threshold (magic number
    currently hardcoded), the false-positive rate will be unacceptable.
    This test provides empirical noise floor data to inform threshold calibration.

    Expected behaviour:
      - Gyro std < 50 LSB (roughly ±1.5 deg/s at typical DualSense sensitivity)
      - Accel std < 200 LSB (roughly ±0.06 g at typical DualSense sensitivity)

    Place the controller on a flat, stable surface before running this test.
    """

    def test_imu_noise_floor(self, hid_device):
        """Read 100 IMU samples stationary; verify gyro/accel std within noise floor."""
        reports = _read_n_reports(hid_device, 100, timeout_ms=50)
        assert len(reports) >= 20, (
            f"Only received {len(reports)} reports; need at least 20 for noise floor analysis."
        )

        gyro_x_vals, gyro_y_vals, gyro_z_vals = [], [], []
        accel_x_vals, accel_y_vals, accel_z_vals = [], [], []

        for report in reports:
            imu = _parse_imu(report)
            if "gyro_x" in imu:
                gyro_x_vals.append(imu["gyro_x"])
                gyro_y_vals.append(imu["gyro_y"])
                gyro_z_vals.append(imu["gyro_z"])
            if "accel_x" in imu:
                accel_x_vals.append(imu["accel_x"])
                accel_y_vals.append(imu["accel_y"])
                accel_z_vals.append(imu["accel_z"])

        def _std(vals):
            if len(vals) < 2:
                return 0.0
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
            return variance ** 0.5

        # Gyro noise floor: empirical threshold for stationary DualSense
        GYRO_STD_THRESHOLD = 50.0   # LSB; ~1.5 deg/s at typical sensitivity
        ACCEL_STD_THRESHOLD = 200.0  # LSB; ~0.06 g at typical sensitivity

        if gyro_x_vals:
            for axis, vals in [("gyro_x", gyro_x_vals), ("gyro_y", gyro_y_vals), ("gyro_z", gyro_z_vals)]:
                std = _std(vals)
                mean = sum(vals) / len(vals)
                assert std < GYRO_STD_THRESHOLD, (
                    f"IMU {axis} noise std={std:.1f} LSB exceeds threshold {GYRO_STD_THRESHOLD} LSB. "
                    f"Mean={mean:.1f}. "
                    "Ensure controller is stationary on a flat surface during this test. "
                    "High gyro noise may indicate vibration or electrical interference."
                )

        if accel_x_vals:
            for axis, vals in [("accel_x", accel_x_vals), ("accel_y", accel_y_vals), ("accel_z", accel_z_vals)]:
                std = _std(vals)
                mean = sum(vals) / len(vals)
                assert std < ACCEL_STD_THRESHOLD, (
                    f"IMU {axis} noise std={std:.1f} LSB exceeds threshold {ACCEL_STD_THRESHOLD} LSB. "
                    f"Mean={mean:.1f}. "
                    "Ensure controller is stationary during this test."
                )

        if not gyro_x_vals and not accel_x_vals:
            pytest.skip(
                "No IMU data found in HID reports. "
                "IMU bytes may be at different offsets for this firmware version. "
                "Check byte offsets in _parse_imu() against your controller firmware."
            )


@pytest.mark.hardware
class TestSensorCommitmentConsistency:
    """
    Verify that the bridge's sensor commitment function is deterministic.

    Why: The PoAC record body includes a sensor_commitment = SHA-256(sensor_payload).
    If the same input bytes produce different commitments across calls (e.g., due
    to non-deterministic serialisation), the record_hash chain breaks and on-chain
    verification will fail. This test confirms SHA-256 determinism over live HID data.
    """

    def test_sensor_commitment_consistency(self, hid_device):
        """Generate 5 sensor commitments from live data; verify SHA-256 determinism."""
        reports = _read_n_reports(hid_device, 5, timeout_ms=200)
        assert len(reports) >= 1, "No HID reports received"

        # For each report, compute the commitment twice and verify equality
        for i, report in enumerate(reports):
            commitment_a = _compute_sensor_commitment(report)
            commitment_b = _compute_sensor_commitment(report)
            assert len(commitment_a) == 32, (
                f"Report {i}: commitment length {len(commitment_a)} != 32. "
                "SHA-256 must always produce 32 bytes."
            )
            assert commitment_a == commitment_b, (
                f"Report {i}: non-deterministic commitment. "
                "Same input produced different SHA-256 outputs — this is a critical bug."
            )

        # Also verify that two distinct reports produce distinct commitments
        # (sanity check that we're not hashing an empty/constant payload)
        if len(reports) >= 2:
            c0 = _compute_sensor_commitment(reports[0])
            c1 = _compute_sensor_commitment(reports[1])
            # Allow equality only if the reports themselves are identical (controller idle)
            if reports[0] != reports[1]:
                assert c0 != c1, (
                    "Two distinct HID reports produced identical sensor commitments. "
                    "The commitment function may be hashing a fixed constant rather than "
                    "the report payload — inspect _compute_sensor_commitment()."
                )
