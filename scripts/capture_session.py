"""
capture_session.py — DualShock Edge HID session capture tool.

Captures raw HID reports from a connected DualShock Edge (Sony CFI-ZCP1) over
a configurable duration and saves them to a structured JSON calibration file.
Output is consumed by scripts/threshold_calibrator.py to derive empirical
PITL Layer 4 Mahalanobis thresholds.

Each captured session contains:
  - Device metadata (VID/PID, capture timestamp, effective polling rate)
  - Per-report records: timestamp_ms, extracted features, sensor_commitment (SHA-256)

The sensor_commitment mirrors the bridge's commitment function, so captured sessions
can be replayed against the bridge pipeline without a live controller.

Usage:
    python scripts/capture_session.py --duration 60 --output sessions/session_001.json
    python scripts/capture_session.py --duration 300 --notes "competitive match"
    python scripts/capture_session.py --help
"""

import argparse
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

# Transport-aware HID parser from controller/
_controller_dir = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "controller")
)
if _controller_dir not in sys.path:
    sys.path.insert(0, _controller_dir)
try:
    from hid_report_parser import detect_transport as _detect_transport
    from hid_report_parser import parse_report as _parse_report
    from hid_report_parser import TransportType as _TransportType
    _HID_PARSER_AVAILABLE = True
except ImportError:
    _HID_PARSER_AVAILABLE = False

DEVICE_VID  = 0x054C   # Sony
DEVICE_PID  = 0x0DF2   # DualSense Edge CFI-ZCP1
DEVICE_NAME = "DualShock Edge CFI-ZCP1"

_READ_BUFFER_SIZE   = 128   # bytes
_READ_TIMEOUT_MS    = 10    # ms — keeps polling loop responsive
_PROGRESS_INTERVAL  = 10    # seconds between progress prints


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _extract_features(raw: bytes, transport=None) -> dict:
    """
    Extract named features from a raw DualSense HID report (USB or Bluetooth).

    Delegates to hid_report_parser.parse_report() when available so that BT
    byte offsets (+1 shift) are applied automatically.  Falls back to the
    original USB-only implementation when hid_report_parser is not importable.

    NOTE: _sensor_commitment still hashes the raw report bytes for backward
    compatibility with threshold_calibrator.py.  The bridge uses a separate
    canonical struct-packed commitment computed from InputSnapshot fields.
    """
    # Always include envelope fields independent of transport
    envelope = {
        "report_id":     raw[0] if raw else None,
        "report_length": len(raw),
    }

    # Touchpad extraction (DualSense USB report 0x01: bytes 33-36)
    # touch_active: bit 7 of byte 33 is 0 when finger is present (inverted flag)
    # touch0_x: 12-bit value, range 0-1919
    # touch0_y: 12-bit value, range 0-1079
    def _touch(raw_bytes):
        if len(raw_bytes) >= 37:
            active = bool((raw_bytes[33] & 0x80) == 0)
            x = raw_bytes[34] | ((raw_bytes[35] & 0x0F) << 8)
            y = (raw_bytes[35] >> 4) | (raw_bytes[36] << 4)
        else:
            active, x, y = False, 0, 0
        return active, x, y

    if _HID_PARSER_AVAILABLE and transport is not None:
        parsed = _parse_report(raw, transport)
        touch_active, touch0_x, touch0_y = _touch(raw)
        return {
            **envelope,
            "left_stick_x":  parsed["lx"],
            "left_stick_y":  parsed["ly"],
            "right_stick_x": parsed["rx"],
            "right_stick_y": parsed["ry"],
            "l2_trigger":    parsed["l2"],
            "r2_trigger":    parsed["r2"],
            "buttons_0":     parsed["buttons_0"],
            "buttons_1":     parsed["buttons_1"],
            "gyro_x":        parsed["gyro_x"],
            "gyro_y":        parsed["gyro_y"],
            "gyro_z":        parsed["gyro_z"],
            "accel_x":       parsed["accel_x"],
            "accel_y":       parsed["accel_y"],
            "accel_z":       parsed["accel_z"],
            "touch_active":  touch_active,
            "touch0_x":      touch0_x,
            "touch0_y":      touch0_y,
        }

    # Fallback: USB-only offsets (original implementation)
    def _u8(offset): return raw[offset] if len(raw) > offset else None
    def _i16(offset):
        if len(raw) >= offset + 2:
            try:
                return struct.unpack_from("<h", raw, offset)[0]
            except struct.error:
                pass
        return None

    touch_active, touch0_x, touch0_y = _touch(raw)
    return {
        **envelope,
        "left_stick_x":  _u8(1),
        "left_stick_y":  _u8(2),
        "right_stick_x": _u8(3),
        "right_stick_y": _u8(4),
        "l2_trigger":    _u8(5),
        "r2_trigger":    _u8(6),
        "buttons_0":     _u8(8),
        "buttons_1":     _u8(9),
        "gyro_x":        _i16(16),
        "gyro_y":        _i16(18),
        "gyro_z":        _i16(20),
        "accel_x":       _i16(22),
        "accel_y":       _i16(24),
        "accel_z":       _i16(26),
        "touch_active":  touch_active,
        "touch0_x":      touch0_x,
        "touch0_y":      touch0_y,
    }


def _sensor_commitment(raw: bytes) -> str:
    """SHA-256 of the full HID report payload. Mirrors bridge commitment function."""
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="capture_session.py",
        description=(
            "Capture DualShock Edge HID reports to a JSON calibration dataset. "
            "Output consumed by scripts/threshold_calibrator.py for PITL L4 threshold derivation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/capture_session.py --duration 60\n"
            "  python scripts/capture_session.py --duration 300 "
            "--output sessions/match_001.json --notes 'competitive match'\n"
        ),
    )
    p.add_argument("--duration", type=int, default=60,
                   help="Capture duration in seconds (default: 60)")
    p.add_argument("--output", type=str, default=None,
                   help="Output JSON path (default: sessions/session_<timestamp>.json)")
    p.add_argument("--notes", type=str, default="",
                   help="User notes embedded in session metadata")
    p.add_argument("--transport", choices=["usb", "bt", "auto"], default="auto",
                   help="HID transport override: usb (64B), bt (78B), auto=detect (default: auto)")
    return p.parse_args()


def _default_output() -> str:
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return os.path.join("sessions", f"session_{ts}.json")


# ---------------------------------------------------------------------------
# Capture loop
# ---------------------------------------------------------------------------

def run(duration_s: int, output_path: str, notes: str, transport_arg: str = "auto") -> int:
    if not _HID_AVAILABLE:
        print("ERROR: 'hid' package not installed. Run: pip install hidapi", file=sys.stderr)
        return 1

    devices = _hid_lib.enumerate(DEVICE_VID, DEVICE_PID)
    if not devices:
        print(
            f"ERROR: No device found (VID=0x{DEVICE_VID:04X}, PID=0x{DEVICE_PID:04X}).\n"
            "Ensure DualShock Edge is connected via USB or Bluetooth.\n"
            "Linux: run scripts/hardware_setup.sh for udev rules.",
            file=sys.stderr,
        )
        return 1

    product_str = devices[0].get("product_string", DEVICE_NAME)
    capture_ts  = datetime.datetime.utcnow().isoformat() + "Z"

    print(f"Device   : {product_str}")
    print(f"Duration : {duration_s}s")
    print(f"Output   : {output_path}")
    if notes:
        print(f"Notes    : {notes}")
    print("Starting capture — press Ctrl+C to stop early.")

    h = _hid_lib.device()
    try:
        h.open(DEVICE_VID, DEVICE_PID)
        h.set_nonblocking(False)
    except OSError as exc:
        print(f"ERROR: Cannot open HID device: {exc}", file=sys.stderr)
        return 1

    # --- Transport detection ---
    _transport = None
    _first_raw = None
    if transport_arg != "auto" and _HID_PARSER_AVAILABLE:
        _transport = _TransportType.USB if transport_arg == "usb" else _TransportType.BLUETOOTH
        print(f"[TRANSPORT] Override: {_transport.value}")
    else:
        # Auto-detect from first report
        _raw0 = h.read(_READ_BUFFER_SIZE, timeout_ms=2000)
        if _raw0:
            _first_raw = bytes(_raw0)
            if _HID_PARSER_AVAILABLE:
                _transport = _detect_transport(_first_raw)
                print(f"[TRANSPORT] Detected: {_transport.value} (report length {len(_first_raw)})")
            else:
                print("[TRANSPORT] hid_report_parser unavailable — USB offsets assumed")

    transport_str = _transport.value if _transport is not None else "usb"

    captured = []
    t_start  = time.perf_counter()
    t_last   = t_start

    # Include first report captured during transport detection
    if _first_raw:
        captured.append({
            "timestamp_ms":      0,
            "features":          _extract_features(_first_raw, _transport),
            "sensor_commitment": _sensor_commitment(_first_raw),
        })

    try:
        while True:
            now = time.perf_counter()
            elapsed = now - t_start
            if elapsed >= duration_s:
                break

            if now - t_last >= _PROGRESS_INTERVAL:
                rate = len(captured) / elapsed if elapsed > 0 else 0.0
                print(f"  {elapsed:.0f}s / {duration_s}s — {len(captured)} reports ({rate:.1f} Hz)")
                t_last = now

            raw = h.read(_READ_BUFFER_SIZE, timeout_ms=_READ_TIMEOUT_MS)
            if not raw:
                continue

            raw_bytes    = bytes(raw)
            ts_ms        = int((now - t_start) * 1000)
            captured.append({
                "timestamp_ms":      ts_ms,
                "features":          _extract_features(raw_bytes, _transport),
                "sensor_commitment": _sensor_commitment(raw_bytes),
            })

    except KeyboardInterrupt:
        print("\nStopped early by user.")
    finally:
        try:
            h.close()
        except Exception:
            pass

    t_actual    = time.perf_counter() - t_start
    rate_actual = len(captured) / t_actual if t_actual > 0 else 0.0

    metadata = {
        "device_vid":           f"0x{DEVICE_VID:04X}",
        "device_pid":           f"0x{DEVICE_PID:04X}",
        "device_name":          DEVICE_NAME,
        "product_string":       product_str,
        "transport":            transport_str,
        "capture_timestamp":    capture_ts,
        "duration_requested_s": duration_s,
        "duration_actual_s":    round(t_actual, 3),
        "report_count":         len(captured),
        "polling_rate_hz":      round(rate_actual, 2),
        "user_notes":           notes,
        "calibration_note":     (
            "Pass this file to scripts/threshold_calibrator.py to derive "
            "empirical PITL L4 Mahalanobis thresholds. Minimum N=10 sessions "
            "recommended; N=50 for production thresholds."
        ),
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": metadata, "reports": captured}, f, indent=2)

    print(f"\nSaved {len(captured)} reports -> {output_path}")
    print(f"Effective polling rate: {rate_actual:.1f} Hz over {t_actual:.1f}s")
    print(f"Next step: python scripts/threshold_calibrator.py {output_path}")
    return 0


def main() -> int:
    args = _parse_args()
    output = args.output if args.output else _default_output()
    return run(args.duration, output, args.notes, transport_arg=args.transport)


if __name__ == "__main__":
    sys.exit(main())
