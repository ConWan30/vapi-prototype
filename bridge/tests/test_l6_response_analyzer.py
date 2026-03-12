"""
Phase C — L6 Response Curve Analyzer Tests

TestL6ResponseAnalyzer (8):
1. compute_metrics() returns L6ResponseMetrics instance
2. onset_ms correctly computed from synthesized pre/post buffers
3. classify() returns 0.5 for valid=False metrics (null signal)
4. classify() returns 0.0 when grip_variance == 0.0 (zeroed accel = injection)
5. classify() returns < 0.4 for onset_ms < 5 (too fast = software injection)
6. classify() returns > 0.6 for realistic human response parameters
7. peak_delta < 5 (never pressed) -> classify() <= 0.3
8. classify() always returns value in [0.0, 1.0]
"""

import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from bridge.controller.l6_challenge_profiles import CHALLENGE_PROFILES
from vapi_bridge.l6_response_analyzer import L6ResponseAnalyzer, L6ResponseMetrics


def _make_reports(n: int, r2: float = 0.0, l2: float = 0.0,
                  accel_x: float = 0.0, accel_y: float = 0.0, accel_z: float = 2048.0):
    """Synthesize n HID report dicts with constant feature values."""
    return [
        {"features": {"r2": r2, "l2": l2,
                      "accel_x": accel_x, "accel_y": accel_y, "accel_z": accel_z}}
        for _ in range(n)
    ]


def _make_press_reports(pre_r2: float, post_r2: float, n_pre: int = 20, n_post: int = 100,
                        accel_z: float = 2048.0):
    """Simulate: pre-challenge baseline at pre_r2, then pressed to post_r2."""
    pre = _make_reports(n_pre, r2=pre_r2, accel_z=accel_z)
    # First 10 post-reports at pre_r2 (no press yet), then press
    idle  = _make_reports(10, r2=pre_r2, accel_z=accel_z)
    press = _make_reports(n_post - 10, r2=post_r2, accel_z=accel_z)
    post  = idle + press
    return pre, post


class TestL6ResponseAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = L6ResponseAnalyzer()
        self.profile = CHALLENGE_PROFILES[1]  # RIGID_LIGHT

    def test_1_compute_metrics_returns_correct_type(self):
        """compute_metrics() must return L6ResponseMetrics."""
        pre  = _make_reports(20)
        post = _make_reports(50)
        import time
        metrics = self.analyzer.compute_metrics(pre, post, self.profile, time.monotonic())
        self.assertIsInstance(metrics, L6ResponseMetrics)

    def test_2_onset_ms_computed_correctly(self):
        """onset_ms reflects the frame index where trigger delta > ONSET_DELTA_LSB."""
        pre, post = _make_press_reports(pre_r2=0.0, post_r2=100.0)
        import time
        metrics = self.analyzer.compute_metrics(pre, post, self.profile, time.monotonic())
        # First 10 post-reports have r2=0 (baseline), onset at report index 10
        self.assertAlmostEqual(metrics.onset_ms, 10.0, delta=2.0)

    def test_3_classify_returns_05_when_invalid(self):
        """classify() must return 0.5 (null) when metrics.valid is False."""
        metrics = L6ResponseMetrics(
            onset_ms=0.0, peak_delta=0.0, settle_ms=0.0,
            grip_variance=100.0, profile_id=1, nonce_bytes=b"\x00"*4, valid=False,
        )
        result = self.analyzer.classify(metrics)
        self.assertAlmostEqual(result, 0.5)

    def test_4_classify_returns_00_when_zeroed_accel(self):
        """classify() must return 0.0 when grip_variance == 0.0 (injected hardware)."""
        metrics = L6ResponseMetrics(
            onset_ms=50.0, peak_delta=100.0, settle_ms=200.0,
            grip_variance=0.0, profile_id=1, nonce_bytes=b"\x00"*4, valid=True,
        )
        result = self.analyzer.classify(metrics)
        self.assertAlmostEqual(result, 0.0)

    def test_5_classify_returns_low_for_sub_5ms_onset(self):
        """classify() must return < 0.4 for onset_ms < 5 (software injection signal)."""
        metrics = L6ResponseMetrics(
            onset_ms=1.0, peak_delta=80.0, settle_ms=100.0,
            grip_variance=5000.0, profile_id=1, nonce_bytes=b"\x00"*4, valid=True,
        )
        result = self.analyzer.classify(metrics)
        self.assertLess(result, 0.4)

    def test_6_classify_returns_high_for_human_response(self):
        """classify() must return > 0.6 for realistic human motor response."""
        metrics = L6ResponseMetrics(
            onset_ms=80.0,    # realistic human reaction (80ms)
            peak_delta=90.0,  # definite press detected
            settle_ms=300.0,  # settled within 300ms
            grip_variance=12000.0,  # real hand movement
            profile_id=1, nonce_bytes=b"\x00"*4, valid=True,
        )
        result = self.analyzer.classify(metrics)
        self.assertGreater(result, 0.6)

    def test_7_never_pressed_returns_low(self):
        """peak_delta < 5 (never pressed) must yield classify() <= 0.3."""
        metrics = L6ResponseMetrics(
            onset_ms=0.0, peak_delta=2.0, settle_ms=0.0,
            grip_variance=8000.0, profile_id=1, nonce_bytes=b"\x00"*4, valid=True,
        )
        result = self.analyzer.classify(metrics)
        self.assertLessEqual(result, 0.3)

    def test_8_classify_always_in_01_range(self):
        """classify() must always return a value in [0.0, 1.0]."""
        test_cases = [
            L6ResponseMetrics(0.0, 0.0, 0.0, 0.0, 1, b"\x00"*4, False),
            L6ResponseMetrics(0.0, 0.0, 0.0, 0.0, 1, b"\x00"*4, True),
            L6ResponseMetrics(1.0, 200.0, 50.0, 50000.0, 1, b"\x00"*4, True),
            L6ResponseMetrics(500.0, 5.0, 3000.0, 1.0, 1, b"\x00"*4, True),
        ]
        for metrics in test_cases:
            result = self.analyzer.classify(metrics)
            self.assertGreaterEqual(result, 0.0, f"Got {result} < 0.0 for {metrics}")
            self.assertLessEqual(result, 1.0, f"Got {result} > 1.0 for {metrics}")


class TestL6Phase42(unittest.TestCase):
    """Phase 42 — capture schema + attack-G + null response tests."""

    def setUp(self):
        self.analyzer = L6ResponseAnalyzer()

    def test_attack_g_injection_signature(self):
        """Attack G: grip_variance==0 -> p_human=0.0; onset_ms<5 -> p_human<=0.2."""
        # Zeroed accelerometer (injected hardware) — grip_variance exactly 0.0
        zeroed = L6ResponseMetrics(
            onset_ms=50.0, peak_delta=100.0, settle_ms=200.0,
            grip_variance=0.0, profile_id=1, nonce_bytes=b"\x00" * 4, valid=True,
        )
        self.assertAlmostEqual(self.analyzer.classify(zeroed), 0.0,
                               msg="grip_variance==0.0 must yield p_human=0.0")

        # Sub-neurological onset (<5 ms) — software injection latency
        fast = L6ResponseMetrics(
            onset_ms=2.0, peak_delta=80.0, settle_ms=150.0,
            grip_variance=5000.0, profile_id=1, nonce_bytes=b"\x00" * 4, valid=True,
        )
        self.assertLessEqual(self.analyzer.classify(fast), 0.2,
                             msg="onset_ms<5 must yield p_human<=0.2")

    def test_null_response_conservative(self):
        """valid=False (window expired, no press) -> p_human_L6 == 0.5 exactly."""
        null_metrics = L6ResponseMetrics(
            onset_ms=0.0, peak_delta=0.0, settle_ms=0.0,
            grip_variance=0.0, profile_id=3, nonce_bytes=b"\xde\xad\xbe\xef", valid=False,
        )
        result = self.analyzer.classify(null_metrics)
        self.assertAlmostEqual(result, 0.5,
                               msg="Null response must return conservative 0.5, not penalise")

    def test_capture_session_schema(self):
        """l6_capture_sessions table creates correctly and a synthetic record round-trips."""
        import sqlite3
        import tempfile
        import os
        import sys
        # Import store via bridge path
        sys.path.insert(0, str(Path(__file__).parents[1]))
        from vapi_bridge.store import Store

        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "test_l6_capture.db")
        store = Store(db_path)

        store.store_l6_capture(
            session_id="test-uuid-001",
            profile_id=2,
            profile_name="RIGID_HEAVY",
            challenge_sent_ts=1234567890.0,
            onset_ms=82.5,
            settle_ms=310.0,
            peak_delta=95.0,
            grip_variance=14000.0,
            r2_pre_mean=3.2,
            accel_variance=14000.0,
            player_id="P1",
            game_title="Warzone",
            hw_session_ref="hw_075.json",
            notes="Phase 42 unit test",
        )

        rows = store.query_l6_captures(player_id="P1", profile_id=2)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["session_id"], "test-uuid-001")
        self.assertEqual(r["profile_id"], 2)
        self.assertAlmostEqual(r["onset_ms"], 82.5)
        self.assertEqual(r["player_id"], "P1")
        self.assertEqual(r["game_title"], "Warzone")

        counts = store.count_l6_captures_by_profile(player_id="P1")
        self.assertEqual(counts.get(2), 1)


if __name__ == "__main__":
    unittest.main()
