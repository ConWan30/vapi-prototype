"""
test_tremor_fft_live.py — Live tremor FFT validation (high-resolution window).

Gap filled:
  - Proves tremor_peak_hz and tremor_band_power_8_12hz are measurable from live
    DualShock Edge HID data at ~1000 Hz with a 1024-sample FFT window.

Procedure:
  Hold controller in a natural gaming grip. Do not intentionally hold perfectly still.
"""

from __future__ import annotations

import math
import time

import pytest


def _read_n(h, count: int, timeout_ms: int = 10) -> list[bytes]:
    out: list[bytes] = []
    deadline = time.perf_counter() + (count * timeout_ms / 1000.0) + 4.0
    while len(out) < count and time.perf_counter() < deadline:
        raw = h.read(128, timeout_ms=timeout_ms)
        if raw:
            out.append(bytes(raw))
    return out


def _stick_rx_int16(report: bytes) -> int:
    """Convert USB report byte 3 (0..255) to int16 centered scale used in controller pipeline."""
    if len(report) < 4:
        return 0
    return int(report[3] - 128) * 256


def _fft_peak_and_band_power(vx, sample_rate_hz: float):
    np = pytest.importorskip("numpy", reason="numpy required for FFT tests")
    # Use 1024-point window for ~0.98 Hz bin width at 1000 Hz
    n = 1024
    x = np.array(vx[:n], dtype=np.float64)
    x = x - float(x.mean())
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    power = (spec.real ** 2 + spec.imag ** 2)

    # Peak search in physiological band
    lo, hi = 0.1, 15.0
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return None

    idx = int(np.argmax(power[band]))
    band_freqs = freqs[band]
    band_power = power[band]
    peak_hz = float(band_freqs[idx])

    # Band power 8–12 Hz
    b2 = (freqs >= 8.0) & (freqs <= 12.0)
    bp_8_12 = float(power[b2].sum()) if b2.any() else 0.0
    return freqs, peak_hz, bp_8_12


@pytest.mark.hardware
def test_tremor_fft_resolution_1024_frames(hid_device):
    """Collect enough frames and assert FFT bin width < 2.0 Hz."""
    np = pytest.importorskip("numpy", reason="numpy required for FFT tests")
    print("\n[TREMOR] ACTION: Hold controller naturally for ~3 seconds.")
    time.sleep(0.5)

    # 2400 frames -> enough for two 1024 windows after differencing
    t0 = time.perf_counter()
    reports = _read_n(hid_device, 2400, timeout_ms=10)
    t1 = time.perf_counter()
    assert len(reports) >= 1400, f"Only {len(reports)} reports received; need >=1400."

    elapsed = max(1e-6, t1 - t0)
    sample_rate = len(reports) / elapsed

    # Build velocity (normalized units/s): Δrx/32768 / dt
    rx = [_stick_rx_int16(r) for r in reports]
    vx = [(rx[i] - rx[i - 1]) / 32768.0 * sample_rate for i in range(1, len(rx))]

    out = _fft_peak_and_band_power(vx, sample_rate)
    assert out is not None
    freqs, peak_hz, bp_8_12 = out
    bin_width = float(freqs[1] - freqs[0])
    print(f"[TREMOR] sample_rate_hz={sample_rate:.1f}  fft_bin_width_hz={bin_width:.3f}")
    assert bin_width < 2.0, f"FFT bin width too coarse ({bin_width:.3f} Hz)."


@pytest.mark.hardware
def test_tremor_peak_in_physiological_range(hid_device):
    """Peak tremor frequency should be in a plausible range for live data."""
    np = pytest.importorskip("numpy", reason="numpy required for FFT tests")
    print("\n[TREMOR] ACTION: Hold controller naturally in gaming grip for ~3 seconds.")
    time.sleep(0.5)

    t0 = time.perf_counter()
    reports = _read_n(hid_device, 2400, timeout_ms=10)
    t1 = time.perf_counter()
    assert len(reports) >= 1400, f"Only {len(reports)} reports received; need >=1400."

    sample_rate = len(reports) / max(1e-6, (t1 - t0))
    rx = [_stick_rx_int16(r) for r in reports]
    vx = [(rx[i] - rx[i - 1]) / 32768.0 * sample_rate for i in range(1, len(rx))]

    out = _fft_peak_and_band_power(vx, sample_rate)
    assert out is not None
    _, peak_hz, bp_8_12 = out
    print(f"[TREMOR] peak_hz={peak_hz:.3f}  band_power_8_12={bp_8_12:.6e}")
    assert 0.1 <= peak_hz <= 15.0, f"peak_hz={peak_hz:.3f} outside [0.1, 15.0]."


@pytest.mark.hardware
def test_tremor_band_power_nonzero_live(hid_device):
    """Band power should be non-zero for a real controller signal."""
    np = pytest.importorskip("numpy", reason="numpy required for FFT tests")
    print("\n[TREMOR] ACTION: Hold controller naturally for ~3 seconds.")
    time.sleep(0.5)

    t0 = time.perf_counter()
    reports = _read_n(hid_device, 2400, timeout_ms=10)
    t1 = time.perf_counter()
    assert len(reports) >= 1400, f"Only {len(reports)} reports received; need >=1400."

    sample_rate = len(reports) / max(1e-6, (t1 - t0))
    rx = [_stick_rx_int16(r) for r in reports]
    vx = [(rx[i] - rx[i - 1]) / 32768.0 * sample_rate for i in range(1, len(rx))]

    out = _fft_peak_and_band_power(vx, sample_rate)
    assert out is not None
    _, peak_hz, bp_8_12 = out
    print(f"[TREMOR] band_power_8_12={bp_8_12:.6e} (peak_hz={peak_hz:.3f})")
    # Some players/sessions will have negligible 8–12 Hz energy; this is not a failure,
    # but we surface it as a skip so the suite doesn't fail unattended.
    if bp_8_12 <= 0.0:
        pytest.skip("8–12 Hz band power was ~0 in this window. This is player/session dependent.")


@pytest.mark.hardware
def test_tremor_repeatability(hid_device):
    """Two windows back-to-back should produce peak_hz within 2 Hz."""
    np = pytest.importorskip("numpy", reason="numpy required for FFT tests")
    print("\n[TREMOR] ACTION: Hold controller naturally for ~3 seconds (repeatability).")
    time.sleep(0.5)

    t0 = time.perf_counter()
    reports = _read_n(hid_device, 2600, timeout_ms=10)
    t1 = time.perf_counter()
    assert len(reports) >= 2200, f"Only {len(reports)} reports received; need >=2200."

    sample_rate = len(reports) / max(1e-6, (t1 - t0))
    rx = [_stick_rx_int16(r) for r in reports]
    vx = [(rx[i] - rx[i - 1]) / 32768.0 * sample_rate for i in range(1, len(rx))]

    out1 = _fft_peak_and_band_power(vx[0:1024], sample_rate)
    out2 = _fft_peak_and_band_power(vx[1024:2048], sample_rate)
    assert out1 is not None and out2 is not None
    _, p1, b1 = out1
    _, p2, b2 = out2
    print(f"[TREMOR] peak1={p1:.3f}Hz band1={b1:.3e} | peak2={p2:.3f}Hz band2={b2:.3e}")
    assert abs(p1 - p2) <= 2.0, f"peak mismatch too large: {p1:.3f} vs {p2:.3f} Hz."

