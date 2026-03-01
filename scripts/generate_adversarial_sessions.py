"""
generate_adversarial_sessions.py — VAPI adversarial session generator.

Produces session files in the same JSON format as scripts/capture_session.py
for use with scripts/validate_detection.py and scripts/threshold_calibrator.py.

Attack types
------------
macro            Constant 50ms button-press intervals, zero IMU.
                 L5 target: CV ~0.01, entropy ~0 bits, quant ~1.0 (3/3 signals).

tick_quantized   Button presses on 60Hz tick multiples {33.3ms, 50ms} (50/50).
                 L5 target: entropy ~1.0 bit, quant ~1.0 (2/3 signals).

injection        Human-like timing, smooth sinusoidal sticks, ZERO IMU.
                 L5: no detection (human timing). L4: zero-gyro injection signal.

human_baseline   Synthetic human sessions using lognormal reaction-time model.
                 L5: CV~0.35, entropy~3+ bits, quant~0.05 → no detection (FP baseline).

Usage
-----
    python scripts/generate_adversarial_sessions.py
    python scripts/generate_adversarial_sessions.py --count 10 --duration 30
    python scripts/generate_adversarial_sessions.py --type macro --count 5
    python scripts/generate_adversarial_sessions.py --seed 42
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import struct
import sys


# ---------------------------------------------------------------------------
# Constants mirroring capture_session.py
# ---------------------------------------------------------------------------

DEVICE_VID  = 0x054C
DEVICE_PID  = 0x0DF2
DEVICE_NAME = "DualShock Edge CFI-ZCP1 [SYNTHETIC]"

_POLL_HZ     = 100        # Simulated polling rate (100 Hz = 10ms/report)
_POLL_MS     = 1000 // _POLL_HZ
_TICK_MS     = 16.6667    # 60Hz game-loop tick


# ---------------------------------------------------------------------------
# Report construction helpers
# ---------------------------------------------------------------------------

def _make_report(
    ts_ms: int,
    lx: int = 128, ly: int = 128,
    rx: int = 128, ry: int = 128,
    l2: int = 0,   r2: int = 0,
    gx: int = 0,   gy: int = 0, gz: int = 0,
    ax: int = 0,   ay: int = 0, az: int = 1000,
) -> dict:
    """Build a single report record matching capture_session.py output format."""
    # Build a fake 27-byte raw payload for the sensor_commitment hash
    raw = struct.pack(
        "<BBBBBBhhhhhhh",
        0x01, lx, ly, rx, ry, l2, r2, gx, gy, gz, ax, ay, az
    )
    commitment = hashlib.sha256(raw).hexdigest()
    return {
        "timestamp_ms": ts_ms,
        "features": {
            "report_id":     1,
            "report_length": 27,
            "left_stick_x":  lx,
            "left_stick_y":  ly,
            "right_stick_x": rx,
            "right_stick_y": ry,
            "l2_trigger":    l2,
            "r2_trigger":    r2,
            "gyro_x":        gx,
            "gyro_y":        gy,
            "gyro_z":        gz,
            "accel_x":       ax,
            "accel_y":       ay,
            "accel_z":       az,
        },
        "sensor_commitment": commitment,
    }


def _save_session(
    reports: list,
    output_path: str,
    attack_type: str,
    duration_s: int,
    extra_meta: dict | None = None,
) -> None:
    """Save a session to JSON."""
    n = len(reports)
    actual_s = reports[-1]["timestamp_ms"] / 1000.0 if reports else 0.0
    rate_hz  = n / actual_s if actual_s > 0 else 0.0

    metadata = {
        "device_vid":           f"0x{DEVICE_VID:04X}",
        "device_pid":           f"0x{DEVICE_PID:04X}",
        "device_name":          DEVICE_NAME,
        "product_string":       DEVICE_NAME,
        "capture_timestamp":    "SYNTHETIC",
        "duration_requested_s": duration_s,
        "duration_actual_s":    round(actual_s, 3),
        "report_count":         n,
        "polling_rate_hz":      round(rate_hz, 2),
        "attack_type":          attack_type,
        "generator":            "scripts/generate_adversarial_sessions.py",
        "calibration_note":     (
            "Synthetic adversarial session. Use with scripts/validate_detection.py "
            "to measure PITL detection rates. Replace with real hardware captures for "
            "production threshold calibration."
        ),
    }
    if extra_meta:
        metadata.update(extra_meta)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": metadata, "reports": reports}, f)


# ---------------------------------------------------------------------------
# Attack generators
# ---------------------------------------------------------------------------

def gen_macro(duration_s: int, rng: random.Random) -> list:
    """
    Macro bot: constant 50ms button presses, zero IMU.
    L5 signals expected: CV ~0.01, entropy ~0 bits, quant ~1.0 → 3/3 signals.
    """
    reports = []
    ts = 0
    press_interval_ms = 50         # perfectly constant 50ms
    next_press_ts = press_interval_ms

    while ts < duration_s * 1000:
        is_press = abs(ts - next_press_ts) < _POLL_MS
        r2 = 255 if is_press else 0
        if is_press:
            next_press_ts += press_interval_ms
        reports.append(_make_report(ts, r2=r2, gx=0, gy=0, gz=0))
        ts += _POLL_MS

    return reports


def gen_tick_quantized(duration_s: int, rng: random.Random) -> list:
    """
    Tick-quantized macro: presses on 60Hz multiples {33.3ms, 50.0ms}, zero IMU.
    L5 signals expected: entropy ~1.0 bit (2 values), quant ~1.0 → 2/3 signals.
    """
    reports = []
    ts = 0
    tick_choices = [
        round(_TICK_MS * 2),   # ~33ms (2 ticks)
        round(_TICK_MS * 3),   # ~50ms (3 ticks)
    ]
    next_press_ts = rng.choice(tick_choices)

    while ts < duration_s * 1000:
        is_press = abs(ts - next_press_ts) < _POLL_MS
        r2 = 255 if is_press else 0
        if is_press:
            next_press_ts += rng.choice(tick_choices)
        reports.append(_make_report(ts, r2=r2, gx=0, gy=0, gz=0))
        ts += _POLL_MS

    return reports


def gen_injection(duration_s: int, rng: random.Random) -> list:
    """
    HID injection: human-like timing, smooth sinusoidal sticks, ZERO IMU.
    L5: no detection (human-like CV/entropy).
    L4: injection detection via max_gyro_std < threshold during active trigger use.
    """
    reports = []
    ts = 0
    # Generate human-like inter-press intervals (lognormal, mean ~300ms, sigma=0.35)
    mu_ms, sigma = math.log(300), 0.35
    press_times: list[int] = []
    t = 0
    while t < duration_s * 1000:
        interval = int(rng.lognormvariate(mu_ms, sigma))
        interval = max(100, min(800, interval))
        t += interval
        press_times.append(t)

    press_set = set(press_times)
    angle = 0.0

    while ts < duration_s * 1000:
        # Smooth sinusoidal sticks (software-generated)
        angle += 0.05
        rx = int(128 + 120 * math.sin(angle))
        ry = int(128 + 120 * math.cos(angle * 0.7))

        is_press = any(abs(ts - pt) < _POLL_MS for pt in press_set)
        r2 = 200 if is_press else 0

        # ZERO IMU — the injection fingerprint
        reports.append(_make_report(ts, rx=rx, ry=ry, r2=r2, gx=0, gy=0, gz=0, ax=0, ay=0, az=0))
        ts += _POLL_MS

    return reports


def gen_human_baseline(duration_s: int, rng: random.Random, skill_tier: str = "gold") -> list:
    """
    Synthetic human session using lognormal reaction-time model.

    Skill tiers:
      bronze: mean ~450ms, sigma=0.40 (slower, more variable)
      gold:   mean ~300ms, sigma=0.35 (competitive)
      diamond:mean ~180ms, sigma=0.25 (elite — approaches L5 threshold margin)

    L5 expected: CV > 0.30, entropy > 3.0 bits, quant < 0.10 → 0/3 signals (no detection).
    L4 expected: non-zero IMU → no injection detection.
    """
    tier_params = {
        "bronze":  (450, 0.40),
        "gold":    (300, 0.35),
        "diamond": (180, 0.25),
    }
    mean_ms, sigma = tier_params.get(skill_tier, tier_params["gold"])
    mu_ms = math.log(mean_ms)

    reports = []
    ts = 0
    press_times: list[int] = []
    t = 0
    while t < duration_s * 1000:
        interval = int(rng.lognormvariate(mu_ms, sigma))
        interval = max(60, min(1200, interval))
        t += interval
        press_times.append(t)

    press_set = set(press_times)
    # Controller orientation evolves as a slow random walk
    gx_base = rng.gauss(0, 300)
    gy_base = rng.gauss(0, 300)
    gz_base = rng.gauss(0, 100)

    while ts < duration_s * 1000:
        is_press = any(abs(ts - pt) < _POLL_MS for pt in press_set)
        r2 = int(200 + rng.gauss(0, 20)) if is_press else 0
        r2 = max(0, min(255, r2))

        # Realistic IMU: baseline orientation + movement-correlated noise
        gx = int(gx_base + rng.gauss(0, 50) + (120 if is_press else 0) * rng.gauss(0, 1))
        gy = int(gy_base + rng.gauss(0, 50))
        gz = int(gz_base + rng.gauss(0, 30))
        ax = int(rng.gauss(-200, 80))
        ay = int(rng.gauss(50, 40))
        az = int(rng.gauss(9800, 150))  # ~1g downward

        # Stick: slow random walk toward target
        rx = int(128 + rng.gauss(0, 40))
        ry = int(128 + rng.gauss(0, 40))
        rx = max(0, min(255, rx))
        ry = max(0, min(255, ry))

        reports.append(_make_report(ts, rx=rx, ry=ry, r2=r2, gx=gx, gy=gy, gz=gz, ax=ax, ay=ay, az=az))
        ts += _POLL_MS

        # Drift the baseline slowly
        gx_base = max(-2000, min(2000, gx_base + rng.gauss(0, 5)))
        gy_base = max(-2000, min(2000, gy_base + rng.gauss(0, 5)))
        gz_base = max(-1000, min(1000, gz_base + rng.gauss(0, 2)))

    return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_ALL_TYPES = ("macro", "tick_quantized", "injection", "human_baseline")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="generate_adversarial_sessions.py",
        description=(
            "Generate adversarial and human-baseline sessions for PITL detection validation. "
            "Output consumed by scripts/validate_detection.py."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/generate_adversarial_sessions.py\n"
            "  python scripts/generate_adversarial_sessions.py --type macro --count 10\n"
            "  python scripts/generate_adversarial_sessions.py --count 5 --duration 30 --seed 42\n"
        ),
    )
    p.add_argument(
        "--type", choices=list(_ALL_TYPES) + ["all"], default="all",
        help="Attack type to generate (default: all)",
    )
    p.add_argument("--count", type=int, default=10,
                   help="Sessions per attack type (default: 10)")
    p.add_argument("--duration", type=int, default=30,
                   help="Session duration in seconds (default: 30)")
    p.add_argument("--seed", type=int, default=1337,
                   help="Random seed for reproducibility (default: 1337)")
    p.add_argument("--output-dir", default="sessions",
                   help="Root output directory (default: sessions)")
    return p.parse_args()


def main() -> int:
    args  = _parse_args()
    types = list(_ALL_TYPES) if args.type == "all" else [args.type]

    type_dirs = {
        "macro":          os.path.join(args.output_dir, "adversarial"),
        "tick_quantized": os.path.join(args.output_dir, "adversarial"),
        "injection":      os.path.join(args.output_dir, "adversarial"),
        "human_baseline": os.path.join(args.output_dir, "human"),
    }

    total = 0
    for attack_type in types:
        out_dir = type_dirs[attack_type]
        print(f"\nGenerating {args.count} × {attack_type} sessions ({args.duration}s each)...")
        for i in range(args.count):
            rng = random.Random(args.seed + i * 31337)

            if attack_type == "macro":
                reports = gen_macro(args.duration, rng)
                extra = {"press_interval_ms": 50, "expected_l5_signals": 3}
            elif attack_type == "tick_quantized":
                reports = gen_tick_quantized(args.duration, rng)
                extra = {"press_intervals_ms": [33, 50], "expected_l5_signals": 2}
            elif attack_type == "injection":
                reports = gen_injection(args.duration, rng)
                extra = {"imu_zeroed": True, "expected_l4_detection": True}
            elif attack_type == "human_baseline":
                tier = ["bronze", "gold", "gold", "gold", "diamond"][i % 5]
                reports = gen_human_baseline(args.duration, rng, tier)
                extra = {"skill_tier": tier, "expected_l5_detection": False}
            else:
                print(f"Unknown type: {attack_type}", file=sys.stderr)
                return 1

            filename = f"{attack_type}_{i+1:03d}.json"
            path = os.path.join(out_dir, filename)
            _save_session(reports, path, attack_type, args.duration, extra)

            press_count = sum(
                1 for r in reports
                if r["features"].get("r2_trigger", 0) > 128
            )
            print(f"  [{i+1}/{args.count}] {filename}: "
                  f"{len(reports)} reports, {press_count} press events")
            total += 1

    print(f"\nGenerated {total} session file(s).")
    print(f"  Adversarial: {os.path.join(args.output_dir, 'adversarial')}/")
    print(f"  Human:       {os.path.join(args.output_dir, 'human')}/")
    print(f"\nNext: python scripts/validate_detection.py --sessions-dir {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
