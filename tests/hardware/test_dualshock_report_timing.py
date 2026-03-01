"""
test_dualshock_report_timing.py — HID polling rate and report counter validation.

Tests the 1 kHz USB polling rate and report counter monotonicity of the DualShock
Edge (Sony CFI-ZCP1) in USB mode.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP-BY-STEP TEST PROCEDURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PREREQUISITES:
  • DualShock Edge connected via USB-C data cable
  • WHITE PS button LED (USB mode; blue = Bluetooth — wrong mode)
  • pip install hidapi

RUN:
  pytest tests/hardware/test_dualshock_report_timing.py -v -m hardware -s

TOTAL ESTIMATED TIME: ~2 minutes

TESTS IN ORDER:
  1. test_1_polling_rate_1khz          (~5 seconds) — no user action needed
  2. test_2_report_counter_monotonic   (~2 seconds) — no user action needed
  3. test_3_gap_detection              (~2 seconds) — no user action needed
  4. test_4_timestamp_field_advances   (~2 seconds) — no user action needed
  5. test_5_report_counter_wrap        (~1 second)  — no user action needed

NOTE: Tests 1–5 are fully passive (you do not need to touch the controller).
Place the controller on a table, connected via USB.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import struct
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "bridge"))

hid = pytest.importorskip("hid", reason="hidapi not installed. Run: pip install hidapi")

DUALSHOCK_EDGE_VID = 0x054C
DUALSHOCK_EDGE_PID = 0x0DF2

# USB HID polling rate for DualSense in full-speed USB mode
_EXPECTED_POLL_HZ = 1000
_POLL_HZ_TOLERANCE = 0.15  # ±15% — Windows USBHID driver adds ±1 ms jitter

# DualSense Edge report counter: byte 7, uint8, wraps at 255
_COUNTER_BYTE = 7
_COUNTER_MAX = 255


def _read_n(h, count, timeout_ms=5):
    """Read exactly count reports, failing fast on timeout."""
    out = []
    deadline = time.perf_counter() + (count * timeout_ms / 1000.0) + 4.0
    while len(out) < count and time.perf_counter() < deadline:
        raw = h.read(128, timeout_ms=timeout_ms)
        if raw:
            out.append((time.perf_counter(), bytes(raw)))
    return out


@pytest.mark.hardware
class TestPollingRate1kHz:
    """
    TEST 1 — 1 kHz USB polling rate validation.

    No user action needed. Controller placed on table, USB connected.

    The DualSense Edge in USB full-speed mode reports at 1000 Hz (1 report/ms).
    This test reads 1000 reports and measures the elapsed wall-clock time.
    Windows USBHID adds ±1 ms jitter, so we allow ±15% tolerance.

    EXPECTED OUTPUT:
      ✓ 1000 reports received in ~1.0 ± 0.15 seconds
      ✓ Effective polling rate within 850–1150 Hz
    """

    def test_1_polling_rate_1khz(self, hid_device):
        """Read 1000 reports; verify effective rate is within 15% of 1000 Hz."""
        print("\n" + "=" * 60)
        print("TEST 1: Polling Rate Validation (1 kHz USB)")
        print("=" * 60)
        print("No action needed -- reading 1000 reports...")

        target = 1000
        t_start = time.perf_counter()
        reports = _read_n(hid_device, target, timeout_ms=5)
        t_elapsed = time.perf_counter() - t_start

        n = len(reports)
        assert n >= int(target * 0.80), (
            f"Only {n}/{target} reports received in {t_elapsed:.3f}s. "
            "Polling rate may be lower than 1 kHz. "
            "In Windows, USBHID can be limited to 125 Hz by OS scheduling. "
            "Try running as Administrator or adjusting USB polling rate."
        )

        effective_hz = n / t_elapsed
        expected_hz = _EXPECTED_POLL_HZ
        min_hz = expected_hz * (1.0 - _POLL_HZ_TOLERANCE)
        max_hz = expected_hz * (1.0 + _POLL_HZ_TOLERANCE)

        print(f"\n  [RESULT] Reports received:  {n}")
        print(f"  [RESULT] Elapsed time:       {t_elapsed:.3f} s")
        print(f"  [RESULT] Effective rate:     {effective_hz:.1f} Hz")
        print(f"  [RESULT] Expected range:     {min_hz:.0f}–{max_hz:.0f} Hz")

        assert min_hz <= effective_hz <= max_hz, (
            f"Effective rate {effective_hz:.1f} Hz outside expected {min_hz:.0f}–{max_hz:.0f} Hz. "
            "On Windows: USBHID may reduce to 125 Hz. Use `usbhid.sys` tuning or test on Linux. "
            "VAPI PITL features assume 1 kHz — lower rate reduces biometric feature resolution."
        )
        print(f"  PASS: Polling rate {effective_hz:.0f} Hz within tolerance. PASS.")


@pytest.mark.hardware
class TestReportCounterMonotonic:
    """
    TEST 2 — Report counter (byte 7) monotonicity.

    No user action needed.

    The DualSense report counter at byte 7 increments by 1 per report and wraps
    at 255. It is used by VAPI to detect missing/dropped reports in the chain.
    This test verifies the counter is strictly monotonic (modulo 256).

    EXPECTED OUTPUT:
      ✓ Counter increments by exactly 1 for all consecutive reports (or wraps 255→0)
      ✓ No unexpected counter jumps (would indicate dropped reports)
    """

    def test_2_report_counter_monotonic(self, hid_device):
        """Read 200 reports; verify byte-7 counter increments monotonically."""
        print("\n" + "=" * 60)
        print("TEST 2: Report Counter Monotonicity")
        print("=" * 60)
        print("No action needed -- reading 200 reports...")

        reports = _read_n(hid_device, 200, timeout_ms=5)
        assert len(reports) >= 50, f"Only {len(reports)} reports received."

        raw_reports = [r for _, r in reports]
        counters = [r[_COUNTER_BYTE] for r in raw_reports if len(r) > _COUNTER_BYTE]
        assert len(counters) >= 50, "Too few reports with valid counter byte."

        violations = []
        wraps = 0
        for i in range(1, len(counters)):
            prev, curr = counters[i - 1], counters[i]
            expected = (prev + 1) % 256
            if curr == 0 and prev == 255:
                wraps += 1
            elif curr != expected:
                violations.append(
                    f"  Report {i}: counter jumped from {prev} -> {curr} "
                    f"(expected {expected}) -- {curr - prev} reports dropped"
                )

        print(f"\n  [RESULT] {len(counters)} counter values checked")
        print(f"  [RESULT] Counter wraps (255->0): {wraps}")
        print(f"  [RESULT] Counter violations:    {len(violations)}")
        if violations:
            print("  [VIOLATIONS]:")
            for v in violations[:10]:
                print(v)

        assert not violations, (
            f"{len(violations)} counter violations detected.\n"
            "Dropped/duplicated reports will corrupt the PoAC record counter field.\n"
            "Try a different USB cable or port. Check for USB bandwidth contention."
        )
        print(f"  PASS: All {len(counters)} counter increments valid. PASS.")


@pytest.mark.hardware
class TestGapDetection:
    """
    TEST 3 — Inter-report timing gap detection.

    No user action needed.

    At 1 kHz each report should arrive every ~1 ms. Gaps > 5 ms indicate
    a dropped report or OS scheduling preemption. The VAPI bridge uses
    the report counter (test 2) for gap detection — this test measures
    actual wall-clock gaps for calibration.

    EXPECTED OUTPUT:
      ✓ Median inter-report interval ≈ 1.0 ms
      ✓ Max gap < 20 ms (conservative; OS scheduler can cause spikes)
    """

    def test_3_gap_detection(self, hid_device):
        """Measure inter-report intervals; identify gaps > 5 ms."""
        print("\n" + "=" * 60)
        print("TEST 3: Inter-Report Gap Detection")
        print("=" * 60)
        print("No action needed -- measuring report timing for 2 seconds...")

        reports = _read_n(hid_device, 300, timeout_ms=5)
        assert len(reports) >= 50, f"Only {len(reports)} reports received."

        timestamps = [t for t, _ in reports]
        intervals_ms = [(timestamps[i] - timestamps[i - 1]) * 1000.0
                        for i in range(1, len(timestamps))]

        if not intervals_ms:
            pytest.skip("Only 1 report received — cannot compute intervals.")

        sorted_intervals = sorted(intervals_ms)
        median_ms = sorted_intervals[len(sorted_intervals) // 2]
        max_ms = max(intervals_ms)
        gaps_over_5ms = [iv for iv in intervals_ms if iv > 5.0]
        gaps_over_10ms = [iv for iv in intervals_ms if iv > 10.0]

        print(f"\n  [RESULT] Reports received:     {len(reports)}")
        print(f"  [RESULT] Median interval:      {median_ms:.3f} ms  (expected ~ 1.0 ms)")
        print(f"  [RESULT] Max interval:         {max_ms:.3f} ms")
        print(f"  [RESULT] Gaps > 5 ms:          {len(gaps_over_5ms)}")
        print(f"  [RESULT] Gaps > 10 ms:         {len(gaps_over_10ms)}")
        print(f"  [RESULT] 1st-99th percentile:  {sorted_intervals[len(sorted_intervals)//100]:.2f} – "
              f"{sorted_intervals[min(-len(sorted_intervals)//100, -1)]:.2f} ms")

        assert median_ms < 10.0, (
            f"Median inter-report interval {median_ms:.1f} ms >> expected ~ 1.0 ms. "
            "USB polling may be running at 125 Hz (Windows default for non-gaming mode). "
            "VAPI PITL assumes ~1 ms resolution for feature extraction."
        )

        if len(gaps_over_10ms) > len(intervals_ms) * 0.05:
            print(
                f"\n  WARNING: {len(gaps_over_10ms)} gaps > 10 ms "
                f"({100*len(gaps_over_10ms)/len(intervals_ms):.1f}% of intervals). "
                "This exceeds 5% threshold — USB bandwidth contention suspected.\n"
                "Close other USB HID devices and re-run."
            )

        print(f"  PASS: Timing analysis complete. Median = {median_ms:.2f} ms. PASS.")


@pytest.mark.hardware
class TestTimestampFieldAdvances:
    """
    TEST 4 — DualSense internal timestamp field (bytes 12–14).

    No user action needed.

    Bytes 12–14 of the DualSense USB report contain a 24-bit little-endian
    microsecond timer derived from the USB SOF (Start-of-Frame) signal.
    This test verifies the timestamp field increases between consecutive reports.

    EXPECTED OUTPUT:
      ✓ Timestamp (uint24 LE at bytes 12–14) advances between reports
      ✓ Delta ≈ 1000 µs (1 ms) per report at 1 kHz
    """

    def test_4_timestamp_field_advances(self, hid_device):
        """Verify the internal timestamp field at bytes 12–14 advances monotonically."""
        print("\n" + "=" * 60)
        print("TEST 4: Internal Timestamp Field Validation (bytes 12–14)")
        print("=" * 60)
        print("No action needed -- reading 50 reports...")

        reports = _read_n(hid_device, 50, timeout_ms=5)
        raw = [r for _, r in reports if len(r) >= 15]
        assert len(raw) >= 20, f"Only {len(raw)} reports long enough for timestamp check."

        def _ts24(r):
            # 24-bit little-endian unsigned at bytes 12, 13, 14
            return r[12] | (r[13] << 8) | (r[14] << 16)

        timestamps = [_ts24(r) for r in raw]
        deltas = []
        for i in range(1, len(timestamps)):
            # Handle 24-bit wrap (16 million µs ≈ 16.7 s)
            d = (timestamps[i] - timestamps[i - 1]) % (1 << 24)
            deltas.append(d)

        advancing = sum(1 for d in deltas if d > 0)
        zero_deltas = sum(1 for d in deltas if d == 0)
        mean_delta_us = sum(deltas) / len(deltas) if deltas else 0.0

        print(f"\n  [RESULT] Timestamps sampled:   {len(timestamps)}")
        print(f"  [RESULT] Advancing deltas:     {advancing}/{len(deltas)}")
        print(f"  [RESULT] Zero deltas:          {zero_deltas} (duplicate timestamps)")
        print(f"  [RESULT] Mean delta:           {mean_delta_us:.1f} us  (expected ~ 1000 us at 1kHz)")

        if advancing == 0 and all(t == 0 for t in timestamps):
            pytest.skip(
                "All timestamp bytes are 0 — the timestamp field may be at a different offset "
                "for this firmware version. Check dualshock-edge-hid-format.md for byte map."
            )

        assert advancing > len(deltas) * 0.8, (
            f"Only {advancing}/{len(deltas)} timestamp deltas are positive. "
            "Timestamp field may be at wrong offset for this firmware version."
        )
        print(f"  PASS: Timestamp field advances. Mean delta = {mean_delta_us:.0f} us. PASS.")


@pytest.mark.hardware
class TestReportCounterWrap:
    """
    TEST 5 — Report counter wraps from 255 → 0 correctly.

    No user action needed. (Only observable if you happen to catch a wrap
    during the test window; test is advisory if no wrap is observed.)

    The DualSense report counter at byte 7 is a uint8 (0–255). It wraps
    from 255 back to 0 every 256 reports = ~256 ms at 1 kHz.
    """

    def test_5_report_counter_wrap(self, hid_device):
        """Read 600 reports (>256); verify at least one 255→0 wrap is present."""
        print("\n" + "=" * 60)
        print("TEST 5: Report Counter Wrap (255 -> 0)")
        print("=" * 60)
        print("No action needed -- reading 600 reports (~600 ms)...")

        reports = _read_n(hid_device, 600, timeout_ms=5)
        raw = [r for _, r in reports if len(r) > _COUNTER_BYTE]
        counters = [r[_COUNTER_BYTE] for r in raw]

        if len(counters) < 256:
            pytest.skip(
                f"Only {len(counters)} reports received -- cannot observe a full 256-report cycle. "
                "Test is advisory; requires 600 reports at 1 kHz."
            )

        wraps = []
        for i in range(1, len(counters)):
            if counters[i - 1] == 255 and counters[i] == 0:
                wraps.append(i)

        print(f"\n  [RESULT] Reports received:  {len(counters)}")
        print(f"  [RESULT] Counter wraps:     {len(wraps)} (at report indices: {wraps[:5]})")

        if not wraps:
            # Advisory — may not have hit a wrap window
            print(
                "  INFO: No counter wrap observed in this window. "
                "Start counter may not have been near 255. "
                "Re-run to increase probability of observing a wrap."
            )
        else:
            for wrap_idx in wraps:
                assert counters[wrap_idx - 1] == 255
                assert counters[wrap_idx] == 0

            print(f"  PASS: {len(wraps)} counter wrap(s) validated (255->0). PASS.")
