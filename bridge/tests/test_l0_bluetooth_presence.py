"""
Phase 61 — test_l0_bluetooth_presence.py

Tests for controller/l0_bluetooth_presence.py (L0 BT Physical Presence Verifier).

Groups:
  1. Non-BT transport — always returns neutral score
  2. Empty snapshot list — neutral score
  3. Sequence counter scoring — gap detection and score formula
  4. Latency scoring — thresholds at LOW_MS / HIGH_MS / midpoint
  5. Weighted composite overall_score
  6. RSSI always 0.5 (unavailable on Windows/hidapi)
  7. BTPresenceResult fields populated correctly
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "controller"))

from l0_bluetooth_presence import BTPresenceResult, BluetoothPresenceVerifier


# ---------------------------------------------------------------------------
# Mock snapshot
# ---------------------------------------------------------------------------

class _Snap:
    """Minimal InputSnapshot mock with inter_frame_us attribute."""
    def __init__(self, inter_frame_us=None):
        self.inter_frame_us = inter_frame_us


def _snaps(n, inter_frame_us=4000):
    """Create n mock snapshots with given inter_frame_us."""
    return [_Snap(inter_frame_us=inter_frame_us) for _ in range(n)]


def _perfect_counter(n, start=0):
    """Generate n sequential BT counter bytes (no gaps)."""
    return [(start + i) & 0xFF for i in range(n)]


def _counter_with_gaps(n, gap_positions):
    """Generate counter bytes with deliberate gaps at given positions."""
    counters = _perfect_counter(n)
    for pos in gap_positions:
        # Skip a value — create a gap
        if pos < n:
            counters[pos] = (counters[pos] + 2) & 0xFF
    return counters


# ---------------------------------------------------------------------------
# Group 1: Non-BT transport
# ---------------------------------------------------------------------------

class TestNonBTTransport(unittest.TestCase):

    def test_usb_transport_returns_neutral(self):
        v = BluetoothPresenceVerifier("usb")
        result = v.verify_presence(_snaps(50), _perfect_counter(50))
        self.assertEqual(result.overall_score, 0.5)
        self.assertFalse(result.is_bluetooth)
        self.assertEqual(result.transport, "usb")

    def test_usb_transport_sequence_score_neutral(self):
        v = BluetoothPresenceVerifier("usb")
        result = v.verify_presence(_snaps(50), _perfect_counter(50))
        self.assertEqual(result.sequence_score, 0.5)

    def test_usb_transport_latency_score_neutral(self):
        v = BluetoothPresenceVerifier("usb")
        result = v.verify_presence(_snaps(50))
        self.assertEqual(result.latency_score, 0.5)

    def test_transport_case_insensitive(self):
        v = BluetoothPresenceVerifier("USB")
        result = v.verify_presence(_snaps(10))
        self.assertFalse(result.is_bluetooth)


# ---------------------------------------------------------------------------
# Group 2: Empty snapshot list
# ---------------------------------------------------------------------------

class TestEmptySnaps(unittest.TestCase):

    def test_bt_empty_snaps_returns_neutral(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence([], _perfect_counter(0))
        self.assertEqual(result.overall_score, 0.5)
        self.assertEqual(result.n_reports, 0)

    def test_bt_empty_no_crash(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence([])
        self.assertIsInstance(result, BTPresenceResult)


# ---------------------------------------------------------------------------
# Group 3: Sequence counter scoring
# ---------------------------------------------------------------------------

class TestSequenceScore(unittest.TestCase):

    def test_perfect_counter_sequence_score_1(self):
        v = BluetoothPresenceVerifier("bt")
        counters = _perfect_counter(50)
        result = v.verify_presence(_snaps(50), counters)
        self.assertEqual(result.sequence_score, 1.0)
        self.assertEqual(result.sequence_gap_count, 0)

    def test_all_gaps_sequence_score_near_0(self):
        v = BluetoothPresenceVerifier("bt")
        n = 50
        # Every byte is the same — every step is a gap
        counters = [0] * n
        result = v.verify_presence(_snaps(n), counters)
        self.assertEqual(result.sequence_gap_count, n - 1)
        self.assertLess(result.sequence_score, 0.1)

    def test_single_gap_reduces_score(self):
        v = BluetoothPresenceVerifier("bt")
        n = 50
        counters = _counter_with_gaps(n, [25])
        result = v.verify_presence(_snaps(n), counters)
        # Injecting a skip at position 25 creates at least one gap
        self.assertGreaterEqual(result.sequence_gap_count, 1)
        self.assertLess(result.sequence_score, 1.0)
        self.assertGreater(result.sequence_score, 0.0)

    def test_counter_wraps_at_256(self):
        # Counter: 254, 255, 0, 1 — no gap expected
        v = BluetoothPresenceVerifier("bt")
        counters = [254, 255, 0, 1]
        snaps = _snaps(4)
        result = v.verify_presence(snaps, counters)
        self.assertEqual(result.sequence_gap_count, 0)
        self.assertEqual(result.sequence_score, 1.0)

    def test_no_counter_bytes_returns_neutral_sequence(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(30), bt_counter_bytes=None)
        self.assertEqual(result.sequence_score, 0.5)

    def test_empty_counter_list_returns_neutral_sequence(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(30), bt_counter_bytes=[])
        self.assertEqual(result.sequence_score, 0.5)


# ---------------------------------------------------------------------------
# Group 4: Latency scoring
# ---------------------------------------------------------------------------

class TestLatencyScore(unittest.TestCase):

    def test_fast_latency_below_low_ms_score_1(self):
        # 4 ms inter-report = 4000 us — well below 10 ms threshold
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(50, inter_frame_us=4000))
        self.assertEqual(result.latency_score, 1.0)

    def test_exact_low_ms_score_1(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(50, inter_frame_us=10_000))
        self.assertEqual(result.latency_score, 1.0)

    def test_slow_latency_above_high_ms_score_0(self):
        # 70 ms = 70000 us — above 60 ms HIGH threshold
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(50, inter_frame_us=70_000))
        self.assertEqual(result.latency_score, 0.0)

    def test_midpoint_latency_score_half(self):
        # Midpoint = (10 + 60) / 2 = 35 ms — score should be 0.5
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(50, inter_frame_us=35_000))
        self.assertAlmostEqual(result.latency_score, 0.5, places=3)

    def test_no_inter_frame_us_returns_neutral_latency(self):
        v = BluetoothPresenceVerifier("bt")
        snaps = [_Snap(inter_frame_us=None) for _ in range(30)]
        result = v.verify_presence(snaps)
        self.assertEqual(result.latency_score, 0.5)

    def test_zero_inter_frame_us_excluded(self):
        v = BluetoothPresenceVerifier("bt")
        snaps = [_Snap(inter_frame_us=0) for _ in range(30)]
        result = v.verify_presence(snaps)
        self.assertEqual(result.latency_score, 0.5)

    def test_mean_interval_ms_reported_correctly(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(20, inter_frame_us=5000))
        self.assertAlmostEqual(result.mean_interval_ms, 5.0, places=2)


# ---------------------------------------------------------------------------
# Group 5: Weighted composite overall_score
# ---------------------------------------------------------------------------

class TestOverallScore(unittest.TestCase):

    def test_all_perfect_signals_score_near_1(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(
            _snaps(50, inter_frame_us=4000),
            _perfect_counter(50),
        )
        # sequence=1.0 * 0.5 + latency=1.0 * 0.4 + rssi=0.5 * 0.1 = 0.95
        self.assertAlmostEqual(result.overall_score, 0.95, places=3)

    def test_weights_sum_to_1(self):
        w_seq = BluetoothPresenceVerifier._W_SEQUENCE
        w_lat = BluetoothPresenceVerifier._W_LATENCY
        w_rssi = BluetoothPresenceVerifier._W_RSSI
        self.assertAlmostEqual(w_seq + w_lat + w_rssi, 1.0, places=6)

    def test_overall_score_in_0_1_range(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(
            _snaps(50, inter_frame_us=100_000),  # very slow
            [0] * 50,  # all gaps
        )
        self.assertGreaterEqual(result.overall_score, 0.0)
        self.assertLessEqual(result.overall_score, 1.0)

    def test_formula_manual_calculation(self):
        v = BluetoothPresenceVerifier("bt")
        # Design: perfect counter (seq=1.0), fast latency (lat=1.0)
        result = v.verify_presence(
            _snaps(40, inter_frame_us=4000),
            _perfect_counter(40),
        )
        expected = 0.5 * 1.0 + 0.4 * 1.0 + 0.1 * 0.5
        self.assertAlmostEqual(result.overall_score, expected, places=3)


# ---------------------------------------------------------------------------
# Group 6: RSSI always neutral
# ---------------------------------------------------------------------------

class TestRSSI(unittest.TestCase):

    def test_rssi_score_always_0_5(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(50), _perfect_counter(50))
        self.assertEqual(result.rssi_score, 0.5)

    def test_rssi_neutral_even_with_no_data(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(50))
        self.assertEqual(result.rssi_score, 0.5)


# ---------------------------------------------------------------------------
# Group 7: BTPresenceResult fields
# ---------------------------------------------------------------------------

class TestBTPresenceResultFields(unittest.TestCase):

    def test_is_bluetooth_true_for_bt_transport(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(40))
        self.assertTrue(result.is_bluetooth)

    def test_is_bluetooth_false_for_usb(self):
        v = BluetoothPresenceVerifier("usb")
        result = v.verify_presence(_snaps(40))
        self.assertFalse(result.is_bluetooth)

    def test_n_reports_matches_snap_count(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(37))
        self.assertEqual(result.n_reports, 37)

    def test_all_score_fields_are_float(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(40), _perfect_counter(40))
        self.assertIsInstance(result.rssi_score, float)
        self.assertIsInstance(result.latency_score, float)
        self.assertIsInstance(result.sequence_score, float)
        self.assertIsInstance(result.overall_score, float)
        self.assertIsInstance(result.mean_interval_ms, float)

    def test_gap_count_is_int(self):
        v = BluetoothPresenceVerifier("bt")
        result = v.verify_presence(_snaps(40), _perfect_counter(40))
        self.assertIsInstance(result.sequence_gap_count, int)


if __name__ == "__main__":
    unittest.main()
