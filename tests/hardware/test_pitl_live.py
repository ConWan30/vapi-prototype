"""
test_pitl_live.py — Live PITL pipeline smoke tests for DualShock Edge.

All tests require a physical DualShock Edge (Sony CFI-ZCP1) connected via USB
and are skipped automatically when no controller is detected. Run with:

    pytest tests/hardware/test_pitl_live.py -v -m hardware

These tests validate the PITL transport layer before the full bridge pipeline:
  - Nominal human play: sufficient HID report volume for inference
  - Stationary controller: low stick/trigger variance, IMU noise floor measurement
  - PoAC hash chain: deterministic chain construction from live HID reports
  - Feature extraction: all 6 primary features in valid ranges from live data
  - Biometric fingerprint: two short sessions from same device stay within L2 threshold

NOTE: These are TRANSPORT smoke tests. Actual PITL L4/L5 classification requires
the full bridge pipeline running (main.py + BiometricFusionClassifier + TemporalRhythmOracle).
Inference results are NOT tested here — only HID plumbing and feature extraction.
"""

import hashlib
import math
import struct
import sys
import time

import pytest

sys.path.insert(0, "/c/Users/Contr/vapi-pebble-prototype/bridge")

hid = pytest.importorskip("hid", reason="hidapi not installed. Run: pip install hidapi")

DUALSHOCK_EDGE_VID = 0x054C
DUALSHOCK_EDGE_PID = 0x0DF2

# Stationary stick noise tolerance: 5 LSB std on USB HID (conservative pre-calibration)
# TODO: update after running scripts/threshold_calibrator.py on real hardware
_STICK_STATIONARY_STD = 5.0

# Trigger must read near-zero when fully released
_TRIGGER_STATIONARY_MAX = 10

# Maximum L2-norm distance between two 30-sample fingerprints from the same device/state
# Conservative pre-calibration estimate in 6D stick/trigger mean space
_FINGERPRINT_L2_THRESHOLD = 50.0

_MAX_REPORT_LENGTH = 128


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_n_reports(h, count, timeout_ms=100):
    reports = []
    deadline = time.perf_counter() + (count * timeout_ms / 1000.0) + 3.0
    while len(reports) < count and time.perf_counter() < deadline:
        raw = h.read(_MAX_REPORT_LENGTH, timeout_ms=timeout_ms)
        if raw:
            reports.append(bytes(raw))
    return reports


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals):
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _l2_norm(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _extract_features(reports):
    """Extract 6 primary stick/trigger features from HID reports."""
    lx, ly, rx, ry, l2s, r2s = [], [], [], [], [], []
    for r in reports:
        if len(r) < 7:
            continue
        lx.append(r[1]); ly.append(r[2])
        rx.append(r[3]); ry.append(r[4])
        l2s.append(r[5]); r2s.append(r[6])
    if not lx:
        return None
    return {
        "lx_mean": _mean(lx), "ly_mean": _mean(ly),
        "rx_mean": _mean(rx), "ry_mean": _mean(ry),
        "l2_mean": _mean(l2s), "r2_mean": _mean(r2s),
        "_lx": lx, "_ly": ly, "_rx": rx, "_ry": ry,
        "_l2": l2s, "_r2": r2s,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.hardware
class TestNominalHumanPlay:
    """Verify active controller produces sufficient HID report volume for PITL inference."""

    def test_nominal_human_play(self, hid_device):
        """250 reports requested; at least 200 must be received; at least 1 must be non-zero."""
        print("\n[PITL] Please interact with the controller for 5 seconds...")
        reports = _read_n_reports(hid_device, count=250, timeout_ms=30)

        assert len(reports) >= 200, (
            f"Received only {len(reports)}/250 reports. "
            "PITL L4 needs ≥30 samples for Mahalanobis baseline; "
            "L5 needs ≥20 samples for rhythm analysis."
        )

        non_zero = [r for r in reports if len(r) >= 4 and any(b != 0 for b in r[1:4])]
        assert non_zero, (
            "All reports have zero payload bytes 1–3. "
            "Possible: charge-only cable, controller asleep, driver issue."
        )
        print(f"[PITL] {len(reports)} reports received; {len(non_zero)} with non-zero data. PASS.")


@pytest.mark.hardware
class TestStationaryController:
    """Characterise stick/trigger noise floor with controller stationary."""

    def test_stationary_controller(self, hid_device):
        """Leave controller untouched 5s; verify stick std < 5 LSB, triggers near 0."""
        print("\n[PITL] Leave controller completely untouched for 5 seconds...")
        reports = _read_n_reports(hid_device, count=250, timeout_ms=30)
        assert len(reports) >= 20, f"Only {len(reports)} reports received."

        features = _extract_features(reports)
        assert features is not None, "No valid 7-byte reports received."

        # Stick variance
        for axis_name, vals in [("lx", features["_lx"]), ("ly", features["_ly"]),
                                  ("rx", features["_rx"]), ("ry", features["_ry"])]:
            s = _std(vals)
            assert s < _STICK_STATIONARY_STD, (
                f"{axis_name} std={s:.2f} LSB > threshold {_STICK_STATIONARY_STD}. "
                "Indicates stick drift or controller was touched. "
                "This threshold is pre-calibration — run threshold_calibrator.py after testing."
            )

        # Trigger near-zero
        for name, vals in [("L2", features["_l2"]), ("R2", features["_r2"])]:
            mx = max(vals)
            assert mx < _TRIGGER_STATIONARY_MAX, (
                f"{name} max={mx} > {_TRIGGER_STATIONARY_MAX}. Trigger not fully released."
            )

        # IMU noise floor — informational only (printed, not asserted)
        gyro_x_vals = []
        for r in reports:
            if len(r) >= 22:
                try:
                    gyro_x_vals.append(struct.unpack_from("<h", r, 16)[0])
                except struct.error:
                    pass
        if gyro_x_vals:
            print(f"[PITL] Gyro X noise std = {_std(gyro_x_vals):.2f} LSB (informational)")

        print("[PITL] Stationary stick variance within threshold. PASS.")


@pytest.mark.hardware
class TestPoACChainGeneration:
    """Verify hash chain construction from live HID data."""

    def test_poac_chain_generation(self, hid_device):
        """Read 10 reports; build synthetic chain; verify linkage, uniqueness, counter."""
        reports = _read_n_reports(hid_device, count=10, timeout_ms=200)
        assert len(reports) >= 10, f"Only {len(reports)} reports received."
        reports = reports[:10]

        records = []
        prev_hash = b"\x00" * 32

        for ctr, report in enumerate(reports):
            sensor_commit = hashlib.sha256(report).digest()
            body = prev_hash + sensor_commit + struct.pack(">I", ctr)
            record_hash = hashlib.sha256(body).digest()
            records.append({"ctr": ctr, "prev": prev_hash, "hash": record_hash})
            prev_hash = record_hash

        # Chain integrity
        for n in range(1, len(records)):
            assert records[n]["prev"] == records[n - 1]["hash"], (
                f"Chain break at record {n}."
            )

        # Uniqueness
        all_hashes = {r["hash"] for r in records}
        assert len(all_hashes) == len(records), "Duplicate record_hashes detected."

        # Monotonic counter
        for i in range(1, len(records)):
            assert records[i]["ctr"] > records[i - 1]["ctr"]

        print(f"[PITL] {len(records)} chained records. Head: {records[-1]['hash'].hex()[:16]}... PASS.")


@pytest.mark.hardware
class TestFeatureExtractionLive:
    """Verify live feature extraction produces valid ranges and print for calibration."""

    def test_feature_extraction_live(self, hid_device):
        """Extract 6 features from 50 reports; verify all in [0, 255] with no NaN/Inf."""
        print("\n[PITL] Reading 50 reports for feature extraction...")
        reports = _read_n_reports(hid_device, count=50, timeout_ms=100)
        assert len(reports) >= 10

        features = _extract_features(reports)
        assert features is not None, "Feature extraction failed — all reports < 7 bytes."

        for name in ("lx_mean", "ly_mean", "rx_mean", "ry_mean", "l2_mean", "r2_mean"):
            v = features[name]
            assert not (v != v), f"{name} is NaN"
            assert not math.isinf(v), f"{name} is Inf"
            assert 0.0 <= v <= 255.0, f"{name}={v:.3f} out of [0, 255]"

        print(
            f"[PITL] Features:\n"
            f"  left_stick_x_mean={features['lx_mean']:.2f} "
            f"left_stick_y_mean={features['ly_mean']:.2f}\n"
            f"  right_stick_x_mean={features['rx_mean']:.2f} "
            f"right_stick_y_mean={features['ry_mean']:.2f}\n"
            f"  l2_mean={features['l2_mean']:.2f} r2_mean={features['r2_mean']:.2f}\n"
            "  (Use these values with scripts/threshold_calibrator.py for L4 calibration)"
        )
        print("[PITL] All features in [0, 255]. PASS.")


@pytest.mark.hardware
class TestBiometricFingerprintConsistency:
    """Two consecutive 30-report sessions must produce fingerprints within L2 threshold."""

    def test_biometric_fingerprint_consistency(self, hid_device):
        """Read two 30-report windows; verify fingerprints differ but L2 < 50."""
        print("\n[PITL] Reading session A (keep controller in same state)...")
        reports_a = _read_n_reports(hid_device, count=30, timeout_ms=100)
        assert len(reports_a) >= 10, f"Session A: only {len(reports_a)} reports."

        f_a = _extract_features(reports_a)
        assert f_a is not None, "Session A feature extraction failed."

        fp_a = (f_a["lx_mean"], f_a["ly_mean"], f_a["rx_mean"],
                f_a["ry_mean"], f_a["l2_mean"], f_a["r2_mean"])

        time.sleep(0.1)

        print("[PITL] Reading session B...")
        reports_b = _read_n_reports(hid_device, count=30, timeout_ms=100)
        assert len(reports_b) >= 10, f"Session B: only {len(reports_b)} reports."

        f_b = _extract_features(reports_b)
        assert f_b is not None, "Session B feature extraction failed."

        fp_b = (f_b["lx_mean"], f_b["ly_mean"], f_b["rx_mean"],
                f_b["ry_mean"], f_b["l2_mean"], f_b["r2_mean"])

        assert fp_a != fp_b, (
            "Fingerprints A and B are identical — HID buffer may be frozen. "
            "Check USB connection and controller state."
        )

        distance = _l2_norm(fp_a, fp_b)
        assert distance < _FINGERPRINT_L2_THRESHOLD, (
            f"Fingerprint L2-distance={distance:.2f} > threshold {_FINGERPRINT_L2_THRESHOLD}. "
            "This threshold is pre-calibration; run scripts/threshold_calibrator.py after testing."
        )

        print(
            f"[PITL] Fingerprint A: {tuple(f'{v:.2f}' for v in fp_a)}\n"
            f"  Fingerprint B: {tuple(f'{v:.2f}' for v in fp_b)}\n"
            f"  L2 distance: {distance:.3f} (threshold: {_FINGERPRINT_L2_THRESHOLD}). PASS."
        )
