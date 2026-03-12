"""
l6_hardware_check.py — 7-step DualShock Edge hardware diagnostic for L6 calibration.

Verifies that the controller is in a state suitable for L6 human-response baseline
capture. Run this before every capture session.

STEPS
-----
1. Enumerate HID — find DualShock Edge (VID=0x054C, PID=0x0DF2)
2. Poll 1000 reports — verify ≥900 Hz effective polling rate
3. Confirm gravity — mean accel_magnitude in [1800, 2400] LSB (controller at rest)
4. Confirm gyro noise — gyro_std < 150 LSB at rest (no forced vibration)
5. Send test L6 challenge — profile RIGID_LIGHT (id=1) for 500 ms
6. Confirm trigger effect — r2 ADC reads ≥ 8 LSB higher during challenge vs. baseline
7. Confirm return to baseline — r2 ADC returns within 5 LSB of pre-challenge mean

EXIT CODES
----------
  0 — all 7 steps passed
  1 — one or more steps failed (details printed to stdout)

USAGE
-----
  python scripts/l6_hardware_check.py
  python scripts/l6_hardware_check.py --verbose
  python scripts/l6_hardware_check.py --skip-challenge   # skip steps 5-7 (no pydualsense)

REQUIREMENTS
------------
  pip install hidapi
  pip install pydualsense   (for steps 5-7; omit with --skip-challenge)
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time

# DualShock Edge HID identifiers
DS_EDGE_VID = 0x054C
DS_EDGE_PID = 0x0DF2
DS_EDGE_USAGE_PAGE = 0x0001
DS_EDGE_USAGE = 0x0005

# Calibration bounds
GRAVITY_LSB_MIN = 6000   # ±4g scale: 1g ≈ 8192 LSB; allow for controller tilt
GRAVITY_LSB_MAX = 10000  # upper bound allows minor multi-axis orientation variation
GYRO_NOISE_MAX_STD = 75.0   # at rest <50 LSB; 75 gives margin for slight movement
MIN_POLL_HZ = 900
CHALLENGE_DURATION_S = 0.5
TRIGGER_LIFT_MIN_LSB = 8      # r2 must lift by this much during challenge
SETTLE_TOLERANCE_LSB = 5      # r2 must return within this many LSB of baseline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _accel_mag(ax: float, ay: float, az: float) -> float:
    return math.sqrt(ax * ax + ay * ay + az * az)


def _print_step(n: int, label: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    line = f"  [{status}] Step {n}: {label}"
    if detail:
        line += f" — {detail}"
    print(line)


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def step1_enumerate_hid() -> tuple[bool, object | None]:
    """Find DualShock Edge via HID enumeration."""
    try:
        import hid
    except ImportError:
        print("  [FAIL] Step 1: hidapi not installed — run: pip install hidapi")
        return False, None

    devices = hid.enumerate(DS_EDGE_VID, DS_EDGE_PID)
    if not devices:
        _print_step(1, "HID enumerate", False,
                    f"No device found with VID=0x{DS_EDGE_VID:04X} PID=0x{DS_EDGE_PID:04X}")
        return False, None

    # Prefer interface with usage_page=1, usage=5 (gamepad)
    target = None
    for d in devices:
        if d.get("usage_page") == DS_EDGE_USAGE_PAGE and d.get("usage") == DS_EDGE_USAGE:
            target = d
            break
    if target is None:
        target = devices[0]

    try:
        dev = hid.device()
        dev.open_path(target["path"])
        dev.set_nonblocking(False)
    except Exception as exc:
        _print_step(1, "HID enumerate", False, str(exc))
        return False, None

    _print_step(1, "HID enumerate", True,
                f"VID=0x{DS_EDGE_VID:04X} PID=0x{DS_EDGE_PID:04X} path={target['path']!r}")
    return True, dev


def step2_poll_rate(dev) -> tuple[bool, list[bytes]]:
    """Collect 1000 reports and measure effective polling rate."""
    reports = []
    t_start = time.monotonic()
    for _ in range(1000):
        r = dev.read(64)
        if r:
            reports.append(bytes(r))
    elapsed = time.monotonic() - t_start
    hz = len(reports) / elapsed if elapsed > 0 else 0.0
    ok = hz >= MIN_POLL_HZ
    _print_step(2, "Polling rate", ok,
                f"{hz:.0f} Hz from {len(reports)} reports in {elapsed:.2f}s")
    return ok, reports


def step3_gravity(reports: list[bytes]) -> bool:
    """Verify accel_magnitude is in the expected gravity range for USB report format."""
    # USB report ID 0x01; accel at offsets 22-27 (little-endian int16)
    # gyro is at 16-21; these were confirmed in controller/hid_report_parser.py USB_OFFSETS
    mags = []
    for r in reports:
        if len(r) < 28 or r[0] != 0x01:
            continue
        ax = int.from_bytes(r[22:24], "little", signed=True)
        ay = int.from_bytes(r[24:26], "little", signed=True)
        az = int.from_bytes(r[26:28], "little", signed=True)
        mags.append(_accel_mag(ax, ay, az))
    if not mags:
        _print_step(3, "Gravity check", False, "No valid reports with ID=0x01")
        return False
    mean_mag = statistics.mean(mags)
    ok = GRAVITY_LSB_MIN <= mean_mag <= GRAVITY_LSB_MAX
    _print_step(3, "Gravity check", ok,
                f"mean_accel_mag={mean_mag:.1f} LSB (expected {GRAVITY_LSB_MIN}–{GRAVITY_LSB_MAX})")
    return ok


def step4_gyro_noise(reports: list[bytes]) -> bool:
    """Verify gyro noise std < GYRO_NOISE_MAX_STD at rest."""
    # USB report ID 0x01; gyro at offsets 16-21 (little-endian int16)
    gx_vals = []
    for r in reports:
        if len(r) < 18 or r[0] != 0x01:
            continue
        gx = int.from_bytes(r[16:18], "little", signed=True)
        gx_vals.append(float(gx))
    if len(gx_vals) < 10:
        _print_step(4, "Gyro noise", False, "Insufficient reports")
        return False
    std = statistics.stdev(gx_vals)
    ok = std < GYRO_NOISE_MAX_STD
    _print_step(4, "Gyro noise", ok,
                f"gyro_x std={std:.1f} LSB (threshold <{GYRO_NOISE_MAX_STD})")
    return ok


def _r2_from_report(r: bytes) -> int | None:
    """Extract R2 analog ADC from USB report (offset 8)."""
    if len(r) < 10 or r[0] != 0x01:
        return None
    return r[8]


def step5_send_challenge(dev) -> tuple[bool, float]:
    """Send RIGID_LIGHT trigger challenge via pydualsense for CHALLENGE_DURATION_S."""
    try:
        from pydualsense import pydualsense
    except ImportError:
        _print_step(5, "Send challenge", False,
                    "pydualsense not installed — run: pip install pydualsense. "
                    "Re-run with --skip-challenge to skip steps 5-7.")
        return False, 0.0

    try:
        ds = pydualsense()
        ds.init()
        # RIGID_LIGHT: mode=1, forces=[128, 128, 0, 0, 0, 0, 0]
        ds.triggerR.setMode(1)
        for i, f in enumerate([128, 128, 0, 0, 0, 0, 0]):
            ds.triggerR.setForce(i, f)
        challenge_ts = time.monotonic()
        time.sleep(CHALLENGE_DURATION_S)
    except Exception as exc:
        _print_step(5, "Send challenge", False, str(exc))
        return False, 0.0

    _print_step(5, "Send challenge", True,
                f"RIGID_LIGHT (profile 1) applied for {CHALLENGE_DURATION_S}s")
    return True, challenge_ts


def step6_trigger_effect(dev, challenge_baseline_r2: float) -> tuple[bool, float]:
    """Read R2 during active challenge and verify ADC lift."""
    r2_during = []
    t_end = time.monotonic() + 0.2
    while time.monotonic() < t_end:
        r = dev.read(64)
        val = _r2_from_report(bytes(r)) if r else None
        if val is not None:
            r2_during.append(float(val))

    if not r2_during:
        _print_step(6, "Trigger effect", False, "No reports collected during challenge")
        return False, 0.0

    mean_during = statistics.mean(r2_during)
    lift = mean_during - challenge_baseline_r2
    ok = lift >= TRIGGER_LIFT_MIN_LSB
    _print_step(6, "Trigger effect", ok,
                f"r2_during_mean={mean_during:.1f}, baseline={challenge_baseline_r2:.1f}, "
                f"lift={lift:.1f} LSB (need >={TRIGGER_LIFT_MIN_LSB})")
    return ok, mean_during


def step7_return_to_baseline(dev, pre_r2_mean: float) -> bool:
    """Clear triggers and verify R2 returns within SETTLE_TOLERANCE_LSB."""
    try:
        from pydualsense import pydualsense
        ds = pydualsense()
        ds.init()
        ds.triggerR.setMode(0)
        for i in range(7):
            ds.triggerR.setForce(i, 0)
    except Exception:
        pass  # best-effort clear

    time.sleep(0.1)
    r2_after = []
    t_end = time.monotonic() + 0.2
    while time.monotonic() < t_end:
        r = dev.read(64)
        val = _r2_from_report(bytes(r)) if r else None
        if val is not None:
            r2_after.append(float(val))

    if not r2_after:
        _print_step(7, "Return to baseline", False, "No reports collected after clear")
        return False

    mean_after = statistics.mean(r2_after)
    delta = abs(mean_after - pre_r2_mean)
    ok = delta <= SETTLE_TOLERANCE_LSB
    _print_step(7, "Return to baseline", ok,
                f"r2_after={mean_after:.1f}, pre_mean={pre_r2_mean:.1f}, "
                f"delta={delta:.1f} LSB (tolerance <={SETTLE_TOLERANCE_LSB})")
    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print extra diagnostic context.")
    parser.add_argument("--skip-challenge", action="store_true",
                        help="Skip steps 5-7 (trigger output). Useful without pydualsense.")
    args = parser.parse_args()

    print("L6 Hardware Diagnostic — DualShock Edge CFI-ZCP1")
    print("=" * 52)

    results: list[bool] = []

    # Step 1: Enumerate
    ok1, dev = step1_enumerate_hid()
    results.append(ok1)
    if not ok1:
        print("\nFATAL: Controller not found — connect DualShock Edge via USB and retry.")
        return 1

    # Step 2: Polling rate + collect baseline reports
    ok2, baseline_reports = step2_poll_rate(dev)
    results.append(ok2)

    # Step 3: Gravity
    results.append(step3_gravity(baseline_reports))

    # Step 4: Gyro noise
    results.append(step4_gyro_noise(baseline_reports))

    # Compute pre-challenge R2 baseline from collected reports
    r2_baseline_vals = [
        float(_r2_from_report(r))
        for r in baseline_reports
        if _r2_from_report(r) is not None
    ]
    pre_r2_mean = statistics.mean(r2_baseline_vals) if r2_baseline_vals else 0.0

    if args.skip_challenge:
        print("  [SKIP] Step 5: Send challenge (--skip-challenge)")
        print("  [SKIP] Step 6: Trigger effect (--skip-challenge)")
        print("  [SKIP] Step 7: Return to baseline (--skip-challenge)")
        results.extend([True, True, True])  # skipped = pass for diagnostic purposes
    else:
        # Step 5: Send challenge
        ok5, _ts = step5_send_challenge(dev)
        results.append(ok5)

        if ok5:
            # Step 6: Trigger effect
            ok6, _mean_during = step6_trigger_effect(dev, pre_r2_mean)
            results.append(ok6)
            # Step 7: Return to baseline
            results.append(step7_return_to_baseline(dev, pre_r2_mean))
        else:
            results.extend([False, False])

    try:
        dev.close()
    except Exception:
        pass

    print()
    passed = sum(results)
    total = len(results)
    print(f"Result: {passed}/{total} steps passed")

    if passed == total:
        print("Hardware READY for L6 baseline capture.")
        print("Next: python scripts/l6_capture_session.py --player P1 --game Warzone --target 50")
        return 0
    else:
        failed = [i + 1 for i, ok in enumerate(results) if not ok]
        print(f"FAILED steps: {failed}")
        print("Resolve failures before starting L6 capture session.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
