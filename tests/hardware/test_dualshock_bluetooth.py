"""
test_dualshock_bluetooth.py — Bluetooth transport hardware tests for DualShock Edge.

All tests require the DualShock Edge paired via Bluetooth with the USB cable
DISCONNECTED.  Run with:

    pytest tests/hardware/test_dualshock_bluetooth.py -v -m bluetooth -s

These tests are excluded from CI by default (addopts = -m "not bluetooth").
"""

import os
import sys
import statistics
import struct
import time
import pytest

# Make controller/ importable
_CTRL_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "controller")
)
if _CTRL_DIR not in sys.path:
    sys.path.insert(0, _CTRL_DIR)

DUALSHOCK_EDGE_VID = 0x054C
DUALSHOCK_EDGE_PID = 0x0DF2


# ---------------------------------------------------------------------------
# Test 1 — BT enumeration + transport detection
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_hid_enumeration():
    """VID=0x054C PID=0x0DF2 found; detect_transport() identifies BT."""
    import hid
    from hid_report_parser import detect_transport, TransportType

    devices = hid.enumerate(DUALSHOCK_EDGE_VID, DUALSHOCK_EDGE_PID)
    assert devices, "No DualShock Edge found — connect via Bluetooth (USB disconnected)"

    h = hid.device()
    h.open(DUALSHOCK_EDGE_VID, DUALSHOCK_EDGE_PID)
    h.set_nonblocking(False)
    try:
        raw = bytes(h.read(128, timeout_ms=2000))
        transport = detect_transport(raw)
        print(f"\n  report length = {len(raw)}, report_id = 0x{raw[0]:02X}")
        print(f"  transport = {transport.value}")
        assert transport == TransportType.BLUETOOTH, (
            f"Expected BT transport but got {transport.value} "
            f"(len={len(raw)}, id=0x{raw[0]:02X}). "
            "Is USB cable disconnected?"
        )
    finally:
        h.close()


# ---------------------------------------------------------------------------
# Test 2 — BT report format
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_report_format(bt_device):
    """100 reports: len=78, report_id=0x31, sequence byte monotonically increments."""
    reports = []
    for _ in range(100):
        raw = bytes(bt_device.read(128, timeout_ms=50))
        if raw:
            reports.append(raw)

    assert len(reports) >= 50, f"Too few reports: {len(reports)}"

    for i, raw in enumerate(reports):
        assert len(raw) == 78, f"Report {i}: expected 78 bytes, got {len(raw)}"
        assert raw[0] == 0x31, f"Report {i}: expected report_id 0x31, got 0x{raw[0]:02X}"

    # Sequence byte is raw[1] in a BT DualSense report
    seq_vals = [r[1] for r in reports]
    gaps = sum(
        1 for i in range(1, len(seq_vals))
        if seq_vals[i] != (seq_vals[i - 1] + 1) & 0xFF
    )
    gap_rate = gaps / len(seq_vals)
    print(f"\n  {len(reports)} reports, seq gaps = {gaps} ({gap_rate:.1%})")
    assert gap_rate < 0.05, f"Too many sequence gaps: {gaps}/{len(seq_vals)}"


# ---------------------------------------------------------------------------
# Test 3 — BT canonical parsing
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_canonical_parsing(bt_device):
    """parse_report() gives sticks ~128, gyro/accel non-zero on held controller."""
    from hid_report_parser import parse_report, TransportType

    reports = []
    for _ in range(20):
        raw = bytes(bt_device.read(128, timeout_ms=50))
        if raw:
            reports.append(raw)

    assert reports, "No BT reports received"

    parsed = parse_report(reports[-1], TransportType.BLUETOOTH)
    print(f"\n  parsed = lx={parsed['lx']}, ly={parsed['ly']}, "
          f"rx={parsed['rx']}, ry={parsed['ry']}")
    print(f"  accel = ({parsed['accel_x']}, {parsed['accel_y']}, {parsed['accel_z']})")
    print(f"  gyro  = ({parsed['gyro_x']}, {parsed['gyro_y']}, {parsed['gyro_z']})")

    assert parsed["transport"] == "bt"
    # Sticks center ~ 128 when at rest (±50 LSB tolerance)
    for axis in ("lx", "ly", "rx", "ry"):
        assert 50 <= parsed[axis] <= 220, f"{axis} = {parsed[axis]} — unexpected for resting stick"
    # Accel magnitude should reflect gravity (~8192 LSB/g for at-rest unit)
    mag = (parsed["accel_x"]**2 + parsed["accel_y"]**2 + parsed["accel_z"]**2) ** 0.5
    assert mag > 500, f"Accel magnitude {mag:.0f} too low — possible BT IMU parse bug"


# ---------------------------------------------------------------------------
# Test 4 — BT polling rate measurement
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_polling_rate(bt_device):
    """
    10 000 reports over BT; measures actual polling rate.
    Result printed for documentation — not a pass/fail threshold
    (BT gaming mode: 125–250 Hz expected).
    """
    N = 10_000
    t_start = time.perf_counter()
    count = 0
    for _ in range(N):
        raw = bt_device.read(128, timeout_ms=20)
        if raw:
            count += 1
    elapsed = time.perf_counter() - t_start
    rate = count / elapsed if elapsed > 0 else 0.0
    print(f"\n  BT polling rate: {rate:.1f} Hz ({count} reports in {elapsed:.2f}s)")
    assert count >= N * 0.7, f"Received too few reports ({count}/{N})"
    # BT gaming mode should be >= 100 Hz
    assert rate >= 100.0, f"BT polling rate {rate:.1f} Hz below 100 Hz minimum"


# ---------------------------------------------------------------------------
# Test 5 — BT IMU noise floor
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_imu_noise_floor(bt_device):
    """
    Stationary controller: collect 200 gyro samples via BT parse_report().
    Gyro std should be < 200 LSB (USB baseline: <50 LSB at rest).
    Accel magnitude should reflect gravity (>500 LSB) — confirms IMU offsets correct.
    """
    from hid_report_parser import parse_report, TransportType

    gyros_x, accels = [], []
    for _ in range(250):
        raw = bytes(bt_device.read(128, timeout_ms=20))
        if raw and len(raw) == 78:
            p = parse_report(raw, TransportType.BLUETOOTH)
            gyros_x.append(p["gyro_x"])
            mag = (p["accel_x"]**2 + p["accel_y"]**2 + p["accel_z"]**2) ** 0.5
            accels.append(mag)

    assert len(gyros_x) >= 100, f"Too few IMU samples: {len(gyros_x)}"

    gyro_std = statistics.stdev(gyros_x)
    mean_accel = statistics.mean(accels)
    print(f"\n  BT gyro_x std = {gyro_std:.1f} LSB (USB baseline <50 at rest)")
    print(f"  BT accel mag mean = {mean_accel:.0f} LSB (gravity ~8192)")

    assert gyro_std < 500, (
        f"BT gyro_x std {gyro_std:.1f} LSB unexpectedly high "
        "(possible BT offset bug — should use ds.states, not inReport)"
    )
    assert mean_accel > 500, (
        f"BT accel mag {mean_accel:.0f} LSB too low — "
        "confirms IMU parse offset is wrong (gravity missing)"
    )


# ---------------------------------------------------------------------------
# Test 6 — BT IMU fix applied in DualSenseReader
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_imu_fix_applied():
    """
    DualSenseReader.poll() returns non-zero accel on held controller over BT.
    Confirms the ds.states-based IMU fix is active (not ds.state.accelerometer).
    """
    try:
        from pydualsense import pydualsense
    except ImportError:
        pytest.skip("pydualsense not installed")

    sys.path.insert(0, _CTRL_DIR)
    from dualshock_emulator import DualSenseReader

    reader = DualSenseReader()
    if not reader.connect():
        pytest.skip("DualSense not found via pydualsense — check BT pairing")

    # Verify BT transport is active
    con_type = getattr(reader.ds, "conType", None)
    print(f"\n  conType = {con_type}")
    if con_type is None or "BT" not in str(con_type).upper():
        pytest.skip(f"Controller connected via USB (conType={con_type}), not BT")

    snaps = []
    for _ in range(30):
        snap = reader.poll()
        snaps.append(snap)
        time.sleep(0.008)

    accel_mags = [
        (s.accel_x**2 + s.accel_y**2 + s.accel_z**2) ** 0.5
        for s in snaps
    ]
    mean_mag = statistics.mean(accel_mags)
    print(f"  BT accel mag (DualSenseReader) = {mean_mag:.4f} g (expect ~1.0)")
    # Scale is 8192 LSB/g, so accel_x etc. are in g units after /8192
    # Gravity should produce ~1.0 g magnitude
    assert mean_mag > 0.05, (
        f"BT accel magnitude {mean_mag:.4f} g — IMU fix may not be applied "
        "(ds.state.accelerometer instead of ds.states used)"
    )


# ---------------------------------------------------------------------------
# Test 7 — L0 BT presence verification
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_l0_presence_verification(bt_device):
    """
    1000 BT reports → BTPresenceResult.overall_score > 0.7 (real device).
    """
    from hid_report_parser import parse_report, TransportType
    from l0_bluetooth_presence import BluetoothPresenceVerifier

    verifier = BluetoothPresenceVerifier("bt")

    class _FakeSnap:
        def __init__(self, inter_frame_us):
            self.inter_frame_us = inter_frame_us

    snaps = []
    seq_bytes = []
    t_prev = time.perf_counter()

    for _ in range(1050):
        raw = bytes(bt_device.read(128, timeout_ms=20))
        if not raw or len(raw) != 78:
            continue
        t_now = time.perf_counter()
        dt_us = int((t_now - t_prev) * 1_000_000)
        t_prev = t_now
        snaps.append(_FakeSnap(dt_us))
        seq_bytes.append(raw[1])  # BT sequence counter
        if len(snaps) >= 1000:
            break

    assert len(snaps) >= 500, f"Too few BT reports: {len(snaps)}"

    result = verifier.verify_presence(snaps, seq_bytes)
    print(f"\n  overall_score   = {result.overall_score:.3f}")
    print(f"  latency_score   = {result.latency_score:.3f} (mean {result.mean_interval_ms:.1f} ms)")
    print(f"  sequence_score  = {result.sequence_score:.3f} (gaps={result.sequence_gap_count})")
    print(f"  rssi_score      = {result.rssi_score:.3f} (unavailable on Windows/hidapi)")
    print(f"  n_reports       = {result.n_reports}")

    assert result.is_bluetooth
    assert result.overall_score > 0.5, (
        f"BT presence score {result.overall_score:.3f} below 0.5 — "
        "latency or sequence issues detected"
    )


# ---------------------------------------------------------------------------
# Test 8 — Full BT PITL pipeline smoke test
# ---------------------------------------------------------------------------

@pytest.mark.bluetooth
def test_bt_pitl_full_pipeline():
    """
    15-second BT session via DualSenseReader + AntiCheatClassifier.
    Expects PITL classification = NOMINAL (0x20), no false-positive cheat codes.
    """
    pytest.importorskip("pydualsense", reason="pydualsense not installed")

    from dualshock_emulator import DualSenseReader
    try:
        from tinyml_anticheat import AntiCheatClassifier
    except ImportError:
        # controller/ path
        _controller_path = _CTRL_DIR
        if _controller_path not in sys.path:
            sys.path.insert(0, _controller_path)
        from tinyml_anticheat import AntiCheatClassifier

    INFER_NOMINAL = 0x20

    reader = DualSenseReader()
    if not reader.connect():
        pytest.skip("DualSense not found via pydualsense — check BT pairing")

    con_type = getattr(reader.ds, "conType", None)
    if con_type is None or "BT" not in str(con_type).upper():
        pytest.skip(f"Controller connected via USB (conType={con_type}), not BT")

    classifier = AntiCheatClassifier()
    t_end = time.monotonic() + 15.0
    inferences = []

    while time.monotonic() < t_end:
        snap = reader.poll()
        classifier.extract_features(snap, 8.0)
        inf, conf = classifier.classify()
        inferences.append(inf)
        time.sleep(0.008)

    total = len(inferences)
    nominal_count = inferences.count(INFER_NOMINAL)
    cheat_codes = {c for c in inferences if c not in (INFER_NOMINAL, 0x21)}
    nominal_rate = nominal_count / total if total > 0 else 0.0

    print(f"\n  15s BT session: {total} frames, {nominal_count} NOMINAL "
          f"({nominal_rate:.1%}), cheat codes = {cheat_codes}")

    assert nominal_rate >= 0.80, (
        f"NOMINAL rate {nominal_rate:.1%} < 80% on BT — "
        f"cheat codes fired: {cheat_codes}"
    )
    assert not cheat_codes, (
        f"False-positive cheat codes on BT: {cheat_codes!r}. "
        "Check BT IMU offsets (ds.states fix)."
    )
