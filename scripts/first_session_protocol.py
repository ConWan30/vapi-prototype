"""
first_session_protocol.py — Guided first hardware test session.

Walks through the first real DualShock Edge test session step by step:
  1. Verify HID connection
  2. 30s free play (biometric baseline)
  3. 30s structured motions (calibration exercises)
  4. 10s stationary (noise floor)
  5. Generate 50 PoAC-like records
  6. Verify chain integrity
  7. Print full feature summary
  8. Save everything to sessions/first_session/

The output of this script is the calibration baseline used by
scripts/threshold_calibrator.py to derive real-world PITL thresholds.

Usage: python scripts/first_session_protocol.py
"""

import datetime
import hashlib
import json
import math
import os
import struct
import sys
import time

try:
    import hid as _hid_lib
    _HID_AVAILABLE = True
except ImportError:
    _HID_AVAILABLE = False

DEVICE_VID  = 0x054C
DEVICE_PID  = 0x0DF2
DEVICE_NAME = "DualShock Edge CFI-ZCP1"

_READ_BUFFER = 128
_READ_TIMEOUT = 10  # ms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mean(v): return sum(v) / len(v) if v else 0.0
def _std(v):
    if len(v) < 2: return 0.0
    m = _mean(v)
    return math.sqrt(sum((x - m)**2 for x in v) / (len(v) - 1))
def _cv(v): m = _mean(v); return _std(v) / m if m else 0.0

def _banner(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")

def _prompt(msg):
    print(f"\n>>> {msg}")
    print("    Press ENTER when ready...", end="")
    input()


def _extract_features(raw: bytes) -> dict:
    def u8(o): return raw[o] if len(raw) > o else None
    def i16(o):
        if len(raw) >= o + 2:
            try: return struct.unpack_from("<h", raw, o)[0]
            except struct.error: pass
        return None
    return {
        "report_id": u8(0), "report_length": len(raw),
        "left_stick_x": u8(1), "left_stick_y": u8(2),
        "right_stick_x": u8(3), "right_stick_y": u8(4),
        "l2_trigger": u8(5), "r2_trigger": u8(6),
        "gyro_x": i16(16), "gyro_y": i16(18), "gyro_z": i16(20),
        "accel_x": i16(22), "accel_y": i16(24), "accel_z": i16(26),
    }


def _capture_phase(h, label: str, duration_s: int) -> list:
    """Capture HID reports for duration_s seconds. Returns list of timestamped records."""
    captured = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration_s:
        raw = h.read(_READ_BUFFER, timeout_ms=_READ_TIMEOUT)
        if raw:
            ts_ms = int((time.perf_counter() - t0) * 1000)
            raw_bytes = bytes(raw)
            captured.append({
                "timestamp_ms": ts_ms,
                "phase": label,
                "features": _extract_features(raw_bytes),
                "sensor_commitment": hashlib.sha256(raw_bytes).hexdigest(),
            })
    return captured


def _build_chain(reports: list) -> list:
    """Build 50 synthetic PoAC-like records from the captured reports."""
    step = max(1, len(reports) // 50)
    selected = reports[::step][:50]

    chain = []
    prev_hash = b"\x00" * 32
    for ctr, r in enumerate(selected):
        sc = bytes.fromhex(r["sensor_commitment"])
        body = prev_hash + sc + struct.pack(">I", ctr)
        record_hash = hashlib.sha256(body).digest()
        chain.append({
            "counter": ctr,
            "prev_hash": prev_hash.hex(),
            "sensor_commitment": r["sensor_commitment"],
            "record_hash": record_hash.hex(),
            "phase": r.get("phase", ""),
        })
        prev_hash = record_hash
    return chain


def _verify_chain(chain: list) -> bool:
    for i in range(1, len(chain)):
        if chain[i]["prev_hash"] != chain[i-1]["record_hash"]:
            return False
    return True


def _print_feature_summary(label: str, reports: list):
    """Extract and print feature statistics for a capture phase."""
    lx, ly, rx, ry, l2s, r2s = [], [], [], [], [], []
    gx_v, gy_v, gz_v = [], [], []
    iet = []
    prev_ts = None
    for r in reports:
        f = r.get("features", {})
        ts = r.get("timestamp_ms", 0)
        if prev_ts is not None and ts > prev_ts:
            iet.append(ts - prev_ts)
        prev_ts = ts
        if f.get("left_stick_x") is not None: lx.append(f["left_stick_x"])
        if f.get("left_stick_y") is not None: ly.append(f["left_stick_y"])
        if f.get("right_stick_x") is not None: rx.append(f["right_stick_x"])
        if f.get("right_stick_y") is not None: ry.append(f["right_stick_y"])
        if f.get("l2_trigger") is not None: l2s.append(f["l2_trigger"])
        if f.get("r2_trigger") is not None: r2s.append(f["r2_trigger"])
        if f.get("gyro_x") is not None:
            gx_v.append(f["gyro_x"]); gy_v.append(f["gyro_y"]); gz_v.append(f["gyro_z"])

    print(f"\n  --- {label} ({len(reports)} reports) ---")
    print(f"  left_stick_x  : mean={_mean(lx):.1f}  std={_std(lx):.2f}")
    print(f"  left_stick_y  : mean={_mean(ly):.1f}  std={_std(ly):.2f}")
    print(f"  right_stick_x : mean={_mean(rx):.1f}  std={_std(rx):.2f}")
    print(f"  right_stick_y : mean={_mean(ry):.1f}  std={_std(ry):.2f}")
    print(f"  l2_trigger    : mean={_mean(l2s):.1f}  std={_std(l2s):.2f}")
    print(f"  r2_trigger    : mean={_mean(r2s):.1f}  std={_std(r2s):.2f}")
    if gx_v:
        print(f"  gyro_x        : mean={_mean(gx_v):.1f}  std={_std(gx_v):.2f} LSB")
        print(f"  gyro_y        : mean={_mean(gy_v):.1f}  std={_std(gy_v):.2f} LSB")
        print(f"  gyro_z        : mean={_mean(gz_v):.1f}  std={_std(gz_v):.2f} LSB")
    if iet:
        print(f"  inter_event   : mean={_mean(iet):.1f}ms  std={_std(iet):.2f}ms  CV={_cv(iet):.4f}")
        print(f"  effective_hz  : {1000/_mean(iet):.1f} Hz")

    # L5 hint
    if iet:
        cv = _cv(iet)
        if cv < 0.08:
            print(f"  [L5 WARNING] CV={cv:.4f} < 0.08 threshold — would trigger L5 flag!")
        else:
            print(f"  [L5 OK] CV={cv:.4f} ≥ 0.08 — human-range timing variance")

    return {
        "report_count": len(reports),
        "lx_mean": _mean(lx), "lx_std": _std(lx),
        "ly_mean": _mean(ly), "ly_std": _std(ly),
        "rx_mean": _mean(rx), "rx_std": _std(rx),
        "ry_mean": _mean(ry), "ry_std": _std(ry),
        "l2_mean": _mean(l2s), "gyro_x_std": _std(gx_v) if gx_v else None,
        "inter_event_cv": _cv(iet) if iet else None,
    }


# ---------------------------------------------------------------------------
# Main protocol
# ---------------------------------------------------------------------------

def run():
    _banner("VAPI First Hardware Session Protocol")
    print("""
This script guides you through the first physical DualShock Edge test session.
It captures 3 phases of data, generates PoAC records, verifies chain integrity,
and saves calibration data to sessions/first_session/.

The output becomes the biometric baseline for threshold calibration.
""")

    if not _HID_AVAILABLE:
        print("ERROR: hidapi not installed. Run: pip install hidapi")
        return 1

    # ---  1. Device detection ---
    _banner("Step 1/7: Device Detection")
    devices = _hid_lib.enumerate(DEVICE_VID, DEVICE_PID)
    if not devices:
        print("ERROR: DualShock Edge not found.")
        print("  - Connect via USB-C data cable (not charge-only)")
        print("  - Press PS button (should glow solid white in USB mode)")
        print("  - Linux: run scripts/hardware_setup.sh for udev rules")
        return 1

    d = devices[0]
    print(f"  FOUND: {d.get('product_string', DEVICE_NAME)}")
    print(f"  VID=0x{d['vendor_id']:04X} PID=0x{d['product_id']:04X}")
    print(f"  Path: {d.get('path', 'N/A')}")

    h = _hid_lib.device()
    try:
        h.open(DEVICE_VID, DEVICE_PID)
        h.set_nonblocking(False)
    except OSError as e:
        print(f"ERROR: Cannot open device: {e}")
        return 1

    session_ts = datetime.datetime.utcnow().isoformat() + "Z"
    all_reports = []

    try:
        # --- 2. Free play baseline ---
        _banner("Step 2/7: Free Play Baseline (30 seconds)")
        print("  Play naturally — move sticks, press triggers, use buttons.")
        print("  This establishes your biometric fingerprint baseline.")
        _prompt("Ready to begin 30s free play")
        print("  Capturing... (30 seconds)")
        free_play = _capture_phase(h, "free_play", 30)
        all_reports.extend(free_play)
        stats_free = _print_feature_summary("Free Play Baseline", free_play)

        # --- 3. Structured motions ---
        _banner("Step 3/7: Structured Motions (30 seconds)")
        print("  Perform these motions in sequence:")
        print("  - Rotate LEFT stick in full circles (5 rotations)")
        print("  - Rotate RIGHT stick in full circles (5 rotations)")
        print("  - Pull L2 from 0% to 100% slowly 5 times")
        print("  - Pull R2 from 0% to 100% slowly 5 times")
        print("  - Tilt controller left, right, forward, back slowly")
        _prompt("Ready to begin 30s structured motions")
        print("  Capturing... (30 seconds)")
        structured = _capture_phase(h, "structured", 30)
        all_reports.extend(structured)
        stats_structured = _print_feature_summary("Structured Motions", structured)

        # --- 4. Stationary noise floor ---
        _banner("Step 4/7: Stationary Noise Floor (10 seconds)")
        print("  Place the controller on a flat, stable surface.")
        print("  Do NOT touch it during this phase.")
        _prompt("Controller placed on flat surface, ready for 10s capture")
        print("  Capturing... (10 seconds)")
        stationary = _capture_phase(h, "stationary", 10)
        all_reports.extend(stationary)
        stats_stationary = _print_feature_summary("Stationary Noise Floor", stationary)

    finally:
        try:
            h.close()
        except Exception:
            pass

    # --- 5. Generate PoAC chain ---
    _banner("Step 5/7: PoAC Chain Generation")
    print(f"  Selecting 50 records from {len(all_reports)} total reports...")
    chain = _build_chain(all_reports)
    print(f"  Chain head: {chain[-1]['record_hash'][:16]}...")

    # --- 6. Verify chain ---
    _banner("Step 6/7: Chain Integrity Verification")
    if _verify_chain(chain):
        print(f"  PASS: All {len(chain)} records correctly linked.")
        print(f"  Genesis: {chain[0]['record_hash'][:32]}...")
        print(f"  Head:    {chain[-1]['record_hash'][:32]}...")
    else:
        print("  FAIL: Chain integrity check failed. This is a bug — report it.")
        return 1

    # --- 7. Save output ---
    _banner("Step 7/7: Saving Results")
    out_dir = os.path.join("sessions", "first_session")
    os.makedirs(out_dir, exist_ok=True)

    session_data = {
        "metadata": {
            "protocol": "first_session_protocol",
            "session_timestamp": session_ts,
            "device_vid": f"0x{DEVICE_VID:04X}",
            "device_pid": f"0x{DEVICE_PID:04X}",
            "device_name": DEVICE_NAME,
            "total_reports": len(all_reports),
            "phase_counts": {
                "free_play": len(free_play),
                "structured": len(structured),
                "stationary": len(stationary),
            },
            "calibration_note": (
                "Pass this file to scripts/threshold_calibrator.py to derive "
                "empirical PITL L4 thresholds. This is session 1 — collect "
                "at least 10 sessions before using derived thresholds."
            ),
        },
        "phase_stats": {
            "free_play": stats_free,
            "structured": stats_structured,
            "stationary": stats_stationary,
        },
        "chain": chain,
        "reports": all_reports,
    }

    session_file = os.path.join(out_dir, "session.json")
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)

    print(f"  Saved: {session_file}")
    print(f"  Total reports: {len(all_reports)}")
    print(f"  Chain records: {len(chain)}")

    _banner("Session Complete")
    print(f"""
  Results saved to: {out_dir}/

  Next steps:
    1. Run threshold calibrator:
       python scripts/threshold_calibrator.py {session_file}

    2. Collect more sessions (target N=10 minimum):
       python scripts/capture_session.py --duration 60

    3. Run full hardware test suite:
       pytest tests/hardware/ -v -m hardware

    4. Compare computed thresholds vs. current magic numbers:
       - L4 anomaly: currently 3.0 Mahalanobis units
       - L4 continuity: currently 2.0 Mahalanobis units
       - L5 CV: currently 0.08

  See docs/detection-benchmarks.md for calibration targets.
""")
    return 0


if __name__ == "__main__":
    sys.exit(run())
