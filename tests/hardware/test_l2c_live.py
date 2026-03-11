"""
test_l2c_live.py — Stick-IMU correlation oracle (L2C) live hardware smoke tests.

Gap filled:
  - Provides first real human baseline for right-stick use and StickImuCorrelationOracle.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _read_n(h, count: int, timeout_ms: int = 10) -> list[bytes]:
    out: list[bytes] = []
    deadline = time.perf_counter() + (count * timeout_ms / 1000.0) + 5.0
    while len(out) < count and time.perf_counter() < deadline:
        raw = h.read(128, timeout_ms=timeout_ms)
        if raw:
            out.append(bytes(raw))
    return out


def _rx_int16(report: bytes) -> int:
    """USB report byte 3 (0..255) -> int16 scale used in pipeline: (v-128)*256."""
    if len(report) < 4:
        return 0
    return int(report[3] - 128) * 256


def _gyro_z_i16(report: bytes) -> int:
    """USB report gyro_z at offset 20 (int16 little-endian)."""
    if len(report) < 22:
        return 0
    import struct
    return int(struct.unpack_from("<h", report, 20)[0])


def _feed_oracle(oracle, reports: list[bytes]) -> None:
    t0 = time.monotonic() * 1000.0
    for i, r in enumerate(reports):
        snap = SimpleNamespace(
            right_stick_x=_rx_int16(r),
            gyro_z=_gyro_z_i16(r),
            timestamp_ms=t0 + i,  # assume ~1kHz; 1 ms step is fine for oracle
        )
        oracle.push_snapshot(snap)

def _std_int(vals: list[int]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


@pytest.mark.hardware
def test_l2c_returns_none_for_static_stick(hid_device):
    """When right stick is static, oracle should return None (neutral)."""
    import sys, os
    ctrl_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "controller"))
    if ctrl_dir not in sys.path:
        sys.path.insert(0, ctrl_dir)
    from l2c_stick_imu_correlation import StickImuCorrelationOracle

    print("\n[L2C] ACTION: Do NOT touch the right stick for ~3 seconds.")
    time.sleep(0.5)
    reports = _read_n(hid_device, 300, timeout_ms=10)
    assert len(reports) >= 120, f"Only {len(reports)} reports received; need >=120."

    oracle = StickImuCorrelationOracle()
    _feed_oracle(oracle, reports)
    res = oracle.classify()
    assert res is None, f"Expected None for static stick, got {res}."
    print("[L2C] PASS: static stick -> oracle returned None (dead-zone gate works).")


@pytest.mark.hardware
def test_l2c_returns_result_for_active_stick(hid_device):
    """Active right-stick motion should produce features (non-None) and a valid corr."""
    import sys, os
    ctrl_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "controller"))
    if ctrl_dir not in sys.path:
        sys.path.insert(0, ctrl_dir)
    from l2c_stick_imu_correlation import StickImuCorrelationOracle

    print("\n[L2C] ACTION: Rotate the right stick continuously in circles for ~10 seconds.")
    time.sleep(0.5)
    reports = _read_n(hid_device, 1400, timeout_ms=10)
    assert len(reports) >= 900, f"Only {len(reports)} reports received; need >=900."

    rx_vals = [_rx_int16(r) for r in reports]
    if _std_int(rx_vals) < 800.0:
        pytest.skip("Right stick movement too small (rx std low). Rotate stick more aggressively.")

    oracle = StickImuCorrelationOracle()
    _feed_oracle(oracle, reports)
    feat = oracle.extract_features()
    if feat is None:
        pytest.skip("Oracle features still None (stick velocity std below MIN). Rotate stick faster/longer.")
    lag_ms = feat.lag_at_max  # ~1 ms per frame
    print(f"[L2C] max_causal_corr={feat.max_causal_corr:.3f} lag_ms~{lag_ms} frames n={feat.frame_count}")
    assert -1.0 <= feat.max_causal_corr <= 1.0


@pytest.mark.hardware
def test_l2c_abs_corr_above_threshold_for_human(hid_device):
    """Active stick use: abs(max_causal_corr) should exceed 0.15 human threshold."""
    import sys, os
    ctrl_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "controller"))
    if ctrl_dir not in sys.path:
        sys.path.insert(0, ctrl_dir)
    from l2c_stick_imu_correlation import StickImuCorrelationOracle

    print("\n[L2C] ACTION: Rotate the right stick continuously for ~10 seconds (threshold check).")
    time.sleep(0.5)
    reports = _read_n(hid_device, 1400, timeout_ms=10)
    assert len(reports) >= 900, f"Only {len(reports)} reports received; need >=900."

    rx_vals = [_rx_int16(r) for r in reports]
    if _std_int(rx_vals) < 800.0:
        pytest.skip("Right stick movement too small (rx std low). Rotate stick more aggressively.")

    oracle = StickImuCorrelationOracle()
    _feed_oracle(oracle, reports)
    feat = oracle.extract_features()
    if feat is None:
        pytest.skip("Oracle features None (stick velocity std below MIN). Rotate stick faster/longer.")
    corr = abs(float(feat.max_causal_corr))
    print(f"[L2C] abs(max_causal_corr)={corr:.3f} (threshold=0.15) lag_frames={feat.lag_at_max}")
    assert corr >= 0.15, (
        f"abs(max_causal_corr)={corr:.3f} below threshold 0.15. "
        "If this repeats, either right-stick motion did not induce grip twist, "
        "or gyro_z offsets differ for this firmware/transport."
    )

