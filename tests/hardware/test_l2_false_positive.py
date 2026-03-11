"""
test_l2_false_positive.py — Empirical L2 false-positive floor (live human play).

Gap filled:
  - Provides direct hardware evidence that normal human play does not
    accidentally satisfy a "near-zero gyro" injection signature.

This test does NOT use HidXInputOracle (HID vs XInput discrepancy). It targets the
physical IMU-noise floor claim: during real grip + active trigger presses, gyro
variance is non-zero and typically far above any injection-like threshold.
"""

from __future__ import annotations

import math
import time

import pytest


def _read_n(h, count: int, timeout_ms: int = 10) -> list[bytes]:
    out: list[bytes] = []
    deadline = time.perf_counter() + (count * timeout_ms / 1000.0) + 8.0
    while len(out) < count and time.perf_counter() < deadline:
        raw = h.read(128, timeout_ms=timeout_ms)
        if raw:
            out.append(bytes(raw))
    return out


def _gyro_mag(report: bytes) -> float:
    if len(report) < 22:
        return 0.0
    import struct
    gx = struct.unpack_from("<h", report, 16)[0]
    gy = struct.unpack_from("<h", report, 18)[0]
    gz = struct.unpack_from("<h", report, 20)[0]
    return math.sqrt(gx * gx + gy * gy + gz * gz)


def _trigger_r2(report: bytes) -> int:
    return int(report[6]) if len(report) > 6 else 0


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / (len(vals) - 1))


@pytest.mark.hardware
def test_l2_oracle_no_false_positive_active_play(hid_device):
    """
    Play normally for ~30 seconds (press buttons, move sticks, use triggers).

    Computes gyro std over rolling 100-frame windows centered on "active press"
    frames (R2 > 30). Counts how many windows have gyro_std < 20 LSB (candidate FP).
    """
    print("\n[L2] ACTION: For the next ~30 seconds, play normally:")
    print("[L2] - move sticks")
    print("[L2] - press buttons")
    print("[L2] - press R2 repeatedly")
    time.sleep(1.0)

    reports = _read_n(hid_device, 30000, timeout_ms=10)  # ~30s at 1kHz
    assert len(reports) >= 5000, f"Only {len(reports)} reports received; need >=5000."

    active_idxs = [i for i, r in enumerate(reports) if _trigger_r2(r) > 30]
    if len(active_idxs) < 30:
        pytest.skip(f"Not enough active trigger frames (found {len(active_idxs)}). Press R2 more during the test.")

    window = 100
    thresh = 20.0
    checked = 0
    low = 0

    for idx in active_idxs[::50]:  # sample to keep runtime reasonable
        a = max(0, idx - window // 2)
        b = min(len(reports), idx + window // 2)
        seg = reports[a:b]
        mags = [_gyro_mag(r) for r in seg if len(r) >= 22]
        if len(mags) < 20:
            continue
        s = _std(mags)
        checked += 1
        if s < thresh:
            low += 1

    rate = low / max(1, checked)
    print(f"[L2] windows_checked={checked} low_std_windows={low} rate={rate:.4f} (threshold std<{thresh} LSB)")
    assert rate < 0.02, (
        f"False-positive candidate rate too high: {rate:.4f}. "
        "If reproducible, threshold needs adjustment or gyro offsets are wrong."
    )


@pytest.mark.hardware
def test_l2_oracle_gyro_floor_natural_grip(hid_device):
    """
    Hold controller naturally and press R2 repeatedly 20 times.
    Assert mean gyro std during press windows exceeds 20 LSB.
    """
    print("\n[L2] ACTION: Hold controller naturally and press R2 20 times over ~10 seconds.")
    time.sleep(1.0)
    reports = _read_n(hid_device, 12000, timeout_ms=10)  # ~12s
    assert len(reports) >= 2000, f"Only {len(reports)} reports received; need >=2000."

    # Find rising edges of R2
    r2 = [_trigger_r2(r) for r in reports]
    edges = []
    for i in range(1, len(r2)):
        if r2[i - 1] <= 10 and r2[i] > 30:
            edges.append(i)

    if len(edges) < 5:
        pytest.skip(f"Only {len(edges)} R2 rising edges found; press R2 more distinctly.")

    window = 100
    stds: list[float] = []
    for idx in edges[:30]:
        a = max(0, idx - window // 2)
        b = min(len(reports), idx + window // 2)
        mags = [_gyro_mag(r) for r in reports[a:b] if len(r) >= 22]
        if len(mags) >= 20:
            stds.append(_std(mags))

    assert stds, "No gyro std windows computed."
    mean_std = sum(stds) / len(stds)
    p10 = sorted(stds)[max(0, int(0.10 * len(stds)) - 1)]
    p50 = sorted(stds)[len(stds) // 2]
    print(f"[L2] gyro_std during presses: n={len(stds)} mean={mean_std:.1f} p10={p10:.1f} p50={p50:.1f} (LSB)")
    assert mean_std > 20.0, (
        f"Mean gyro std too low ({mean_std:.1f} LSB). "
        "If reproducible, your IMU offsets may be wrong or the controller is being held unnaturally still."
    )

