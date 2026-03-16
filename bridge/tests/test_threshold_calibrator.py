"""
Phase 61 — test_threshold_calibrator.py

Tests for scripts/threshold_calibrator.py statistical functions and
compute_thresholds() pipeline.

Groups:
  1. _mean — basic statistics
  2. _std — sample standard deviation
  3. _cv — coefficient of variation
  4. _percentile — interpolation correctness
  5. _ci95 — confidence interval formula
  6. _entropy_bits — histogram entropy
  7. _extract_press_intervals — R2 digital and analog paths
  8. _extract_cross_intervals — Cross button detection
  9. compute_thresholds — calibration profile structure and confidence levels
 10. compute_thresholds — L4 anomaly > continuity invariant
"""

import sys
import unittest
from pathlib import Path

# Add scripts/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from threshold_calibrator import (
    _ci95,
    _cv,
    _entropy_bits,
    _extract_cross_intervals,
    _extract_press_intervals,
    _mean,
    _percentile,
    _std,
    compute_thresholds,
)


# ---------------------------------------------------------------------------
# Synthetic session builder
# ---------------------------------------------------------------------------

def _make_session(
    n_reports=50,
    polling_rate_hz=1000,
    r2_pattern=None,     # list of r2_trigger values (cycles through)
    buttons_1_pattern=None,
    buttons_0_pattern=None,
    timestamp_step_ms=1.0,
    l2_vals=None,
    lx_vals=None,
    ly_vals=None,
    gyro_vals=None,
    accel_vals=None,
) -> dict:
    """Build a minimal synthetic session dict for calibration testing."""
    reports = []
    for i in range(n_reports):
        feat = {
            "left_stick_x":  (lx_vals[i] if lx_vals and i < len(lx_vals) else 128),
            "left_stick_y":  (ly_vals[i] if ly_vals and i < len(ly_vals) else 128),
            "right_stick_x": 128,
            "right_stick_y": 128,
        }
        if r2_pattern is not None:
            feat["r2_trigger"] = r2_pattern[i % len(r2_pattern)]
        if l2_vals is not None:
            feat["l2_trigger"] = l2_vals[i % len(l2_vals)]
        if buttons_1_pattern is not None:
            feat["buttons_1"] = buttons_1_pattern[i % len(buttons_1_pattern)]
        if buttons_0_pattern is not None:
            feat["buttons_0"] = buttons_0_pattern[i % len(buttons_0_pattern)]
        if gyro_vals is not None:
            g = gyro_vals[i % len(gyro_vals)]
            feat["gyro_x"] = g; feat["gyro_y"] = g; feat["gyro_z"] = g
        if accel_vals is not None:
            a = accel_vals[i % len(accel_vals)]
            feat["accel_x"] = a; feat["accel_y"] = a; feat["accel_z"] = a
        reports.append({
            "timestamp_ms": i * timestamp_step_ms,
            "features": feat,
        })
    return {
        "metadata": {"polling_rate_hz": polling_rate_hz, "report_count": n_reports},
        "reports": reports,
    }


def _make_r2_press_session(n_presses=30, ipi_ms=400, n_reports=None) -> dict:
    """
    Session where R2 is pressed n_presses times with ipi_ms inter-press interval.
    Uses digital buttons_1 bit 3 path.
    """
    # Each press: 10 frames up (bit3=1), 10 frames down (bit3=0)
    period = 20
    n_rep = n_reports or (n_presses * period + 10)
    b1_pattern = [(0b00001000 if (i % period < 10) else 0) for i in range(n_rep)]
    # One step per 1 ms at 1000 Hz
    return _make_session(
        n_reports=n_rep,
        buttons_1_pattern=b1_pattern,
        timestamp_step_ms=1.0,
    )


# ---------------------------------------------------------------------------
# Group 1: _mean
# ---------------------------------------------------------------------------

class TestMean(unittest.TestCase):

    def test_mean_basic(self):
        self.assertAlmostEqual(_mean([1.0, 2.0, 3.0]), 2.0)

    def test_mean_single(self):
        self.assertEqual(_mean([5.0]), 5.0)

    def test_mean_empty_returns_zero(self):
        self.assertEqual(_mean([]), 0.0)

    def test_mean_negative_values(self):
        self.assertAlmostEqual(_mean([-2.0, 2.0]), 0.0)

    def test_mean_floats(self):
        self.assertAlmostEqual(_mean([0.1, 0.2, 0.3]), 0.2, places=10)


# ---------------------------------------------------------------------------
# Group 2: _std
# ---------------------------------------------------------------------------

class TestStd(unittest.TestCase):

    def test_std_known_values(self):
        # _std uses sample variance (N-1): std([1,3]) = sqrt(((1-2)^2+(3-2)^2)/1) = sqrt(2)
        import math
        self.assertAlmostEqual(_std([1.0, 3.0]), math.sqrt(2), places=10)

    def test_std_single_returns_zero(self):
        self.assertEqual(_std([42.0]), 0.0)

    def test_std_empty_returns_zero(self):
        self.assertEqual(_std([]), 0.0)

    def test_std_identical_values_zero(self):
        self.assertEqual(_std([3.0, 3.0, 3.0]), 0.0)

    def test_std_sample_not_population(self):
        # Uses N-1 (sample): std([1,3]) = sqrt(2) ~= 1.414
        self.assertAlmostEqual(_std([1.0, 3.0]), 1.4142, places=3)


# ---------------------------------------------------------------------------
# Group 3: _cv
# ---------------------------------------------------------------------------

class TestCV(unittest.TestCase):

    def test_cv_basic(self):
        # mean=2, std=1 → cv=0.5
        self.assertAlmostEqual(_cv([1.0, 2.0, 3.0]), _std([1.0, 2.0, 3.0]) / _mean([1.0, 2.0, 3.0]))

    def test_cv_zero_mean_returns_zero(self):
        self.assertEqual(_cv([0.0, 0.0, 0.0]), 0.0)

    def test_cv_empty_returns_zero(self):
        self.assertEqual(_cv([]), 0.0)

    def test_cv_uniform_intervals_is_zero(self):
        self.assertAlmostEqual(_cv([100.0] * 20), 0.0, places=10)

    def test_cv_high_variance(self):
        vals = [1.0, 100.0, 1.0, 100.0]
        self.assertGreater(_cv(vals), 0.5)


# ---------------------------------------------------------------------------
# Group 4: _percentile
# ---------------------------------------------------------------------------

class TestPercentile(unittest.TestCase):

    def test_percentile_median_odd(self):
        self.assertEqual(_percentile([1, 2, 3, 4, 5], 50), 3.0)

    def test_percentile_min(self):
        self.assertEqual(_percentile([10, 20, 30], 0), 10.0)

    def test_percentile_max(self):
        self.assertEqual(_percentile([10, 20, 30], 100), 30.0)

    def test_percentile_interpolates(self):
        # p50 of [0, 100] = 50.0
        self.assertAlmostEqual(_percentile([0, 100], 50), 50.0)

    def test_percentile_empty_returns_zero(self):
        self.assertEqual(_percentile([], 50), 0.0)

    def test_percentile_10th_of_uniform(self):
        vals = list(range(100))
        p10 = _percentile(vals, 10)
        self.assertGreaterEqual(p10, 9.0)
        self.assertLessEqual(p10, 10.0)


# ---------------------------------------------------------------------------
# Group 5: _ci95
# ---------------------------------------------------------------------------

class TestCI95(unittest.TestCase):

    def test_ci95_single_returns_zero_zero(self):
        self.assertEqual(_ci95([5.0]), (0.0, 0.0))

    def test_ci95_empty_returns_zero_zero(self):
        self.assertEqual(_ci95([]), (0.0, 0.0))

    def test_ci95_lo_le_mean_le_hi(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        lo, hi = _ci95(vals)
        mean = _mean(vals)
        self.assertLessEqual(lo, mean)
        self.assertGreaterEqual(hi, mean)

    def test_ci95_symmetric_around_mean(self):
        # Symmetric distribution → symmetric CI
        vals = list(range(1, 11))
        lo, hi = _ci95(vals)
        mean = _mean(vals)
        self.assertAlmostEqual(mean - lo, hi - mean, places=5)

    def test_ci95_large_n_uses_1_96(self):
        # N=30 should use z=1.96 (not 2.0)
        vals = [float(i) for i in range(30)]
        lo, hi = _ci95(vals)
        mean = _mean(vals)
        s = _std(vals)
        expected_margin = 1.96 * s / (30 ** 0.5)
        self.assertAlmostEqual(hi - mean, expected_margin, places=5)

    def test_ci95_small_n_uses_2_0(self):
        # N=5 should use z=2.0
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        lo, hi = _ci95(vals)
        mean = _mean(vals)
        s = _std(vals)
        expected_margin = 2.0 * s / (5 ** 0.5)
        self.assertAlmostEqual(hi - mean, expected_margin, places=5)


# ---------------------------------------------------------------------------
# Group 6: _entropy_bits
# ---------------------------------------------------------------------------

class TestEntropyBits(unittest.TestCase):

    def test_empty_returns_zero(self):
        self.assertEqual(_entropy_bits([]), 0.0)

    def test_constant_returns_zero(self):
        self.assertEqual(_entropy_bits([5.0] * 100), 0.0)

    def test_uniform_distribution_high_entropy(self):
        # Uniform over 10 bins → entropy = log2(10) ≈ 3.32 bits
        vals = list(range(100))  # equally spread → uniform histogram
        entropy = _entropy_bits(vals, bins=10)
        self.assertGreater(entropy, 2.0)

    def test_entropy_positive(self):
        vals = [1.0, 2.0, 5.0, 10.0, 15.0, 20.0]
        self.assertGreater(_entropy_bits(vals), 0.0)

    def test_entropy_bounded_by_log2_bins(self):
        vals = list(range(100))
        bins = 8
        entropy = _entropy_bits(vals, bins=bins)
        import math
        self.assertLessEqual(entropy, math.log2(bins) + 1e-9)


# ---------------------------------------------------------------------------
# Group 7: _extract_press_intervals (R2)
# ---------------------------------------------------------------------------

class TestExtractPressIntervals(unittest.TestCase):

    def test_no_presses_returns_empty(self):
        session = _make_session(n_reports=50)
        result = _extract_press_intervals(session)
        self.assertEqual(result, [])

    def test_digital_path_detects_presses(self):
        session = _make_r2_press_session(n_presses=25)
        result = _extract_press_intervals(session)
        # Each press cycle is 20 ms → ~24 intervals (presses after first)
        self.assertGreaterEqual(len(result), 20)

    def test_intervals_are_positive(self):
        session = _make_r2_press_session(n_presses=25)
        result = _extract_press_intervals(session)
        self.assertTrue(all(v > 0 for v in result))

    def test_analog_fallback_detects_presses(self):
        # No buttons_1 field — use analog hysteresis path
        # Build session with r2 crossing 64 threshold then dropping below 30
        n = 200
        r2_pattern = []
        for i in range(n):
            cycle = i % 20
            r2_pattern.append(100 if cycle < 10 else 0)  # 10 up, 10 down
        session = _make_session(n_reports=n, r2_pattern=r2_pattern)
        result = _extract_press_intervals(session)
        self.assertGreaterEqual(len(result), 5)

    def test_fewer_than_20_reports_still_works(self):
        session = _make_session(n_reports=5)
        # Should not crash, even with no presses detected
        result = _extract_press_intervals(session)
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Group 8: _extract_cross_intervals
# ---------------------------------------------------------------------------

class TestExtractCrossIntervals(unittest.TestCase):

    def test_no_cross_presses_returns_empty(self):
        session = _make_session(n_reports=50)
        result = _extract_cross_intervals(session)
        self.assertEqual(result, [])

    def test_cross_bit5_detected(self):
        # Cross = bit 5 of buttons_0 = 0b00100000 = 0x20
        n = 200
        b0_pattern = [(0x20 if (i % 20 < 10) else 0) for i in range(n)]
        session = _make_session(n_reports=n, buttons_0_pattern=b0_pattern)
        result = _extract_cross_intervals(session)
        self.assertGreaterEqual(len(result), 5)

    def test_intervals_are_positive(self):
        n = 200
        b0_pattern = [(0x20 if (i % 20 < 10) else 0) for i in range(n)]
        session = _make_session(n_reports=n, buttons_0_pattern=b0_pattern)
        result = _extract_cross_intervals(session)
        if result:
            self.assertTrue(all(v > 0 for v in result))

    def test_missing_buttons_0_field_skipped(self):
        # Session with no buttons_0 field
        session = _make_session(n_reports=50)
        # Remove buttons_0 if present
        for r in session["reports"]:
            r["features"].pop("buttons_0", None)
        result = _extract_cross_intervals(session)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Group 9: compute_thresholds — profile structure and confidence levels
# ---------------------------------------------------------------------------

class TestComputeThresholds(unittest.TestCase):

    def _make_sessions(self, n):
        return [_make_r2_press_session(n_presses=25) for _ in range(n)]

    def test_output_has_required_keys(self):
        sessions = self._make_sessions(3)
        profile = compute_thresholds(sessions)
        self.assertIn("calibration_version", profile)
        self.assertIn("n_sessions", profile)
        self.assertIn("confidence_level", profile)
        self.assertIn("thresholds", profile)
        self.assertIn("session_stats", profile)

    def test_threshold_keys_present(self):
        sessions = self._make_sessions(3)
        profile = compute_thresholds(sessions)
        thresholds = profile["thresholds"]
        expected = {
            "l4_mahalanobis_anomaly",
            "l4_mahalanobis_continuity",
            "l5_cv_threshold",
            "l5_entropy_threshold",
            "stick_noise_floor_lsb",
            "imu_gyro_noise_floor_lsb",
        }
        self.assertEqual(set(thresholds.keys()), expected)

    def test_n_sessions_matches_input(self):
        sessions = self._make_sessions(5)
        profile = compute_thresholds(sessions)
        self.assertEqual(profile["n_sessions"], 5)

    def test_confidence_very_low_below_10(self):
        sessions = self._make_sessions(3)
        profile = compute_thresholds(sessions)
        self.assertEqual(profile["confidence_level"], "very_low")

    def test_confidence_low_10_to_24(self):
        sessions = self._make_sessions(15)
        profile = compute_thresholds(sessions)
        self.assertEqual(profile["confidence_level"], "low")

    def test_confidence_medium_25_to_49(self):
        sessions = self._make_sessions(30)
        profile = compute_thresholds(sessions)
        self.assertEqual(profile["confidence_level"], "medium")

    def test_confidence_high_at_50(self):
        sessions = self._make_sessions(50)
        profile = compute_thresholds(sessions)
        self.assertEqual(profile["confidence_level"], "high")

    def test_recommended_thresholds_are_floats(self):
        sessions = self._make_sessions(3)
        profile = compute_thresholds(sessions)
        for name, t in profile["thresholds"].items():
            self.assertIsInstance(t["recommended"], float, name)


# ---------------------------------------------------------------------------
# Group 10: L4 anomaly > continuity invariant
# ---------------------------------------------------------------------------

class TestL4ThresholdInvariant(unittest.TestCase):

    def test_anomaly_threshold_gte_continuity_threshold(self):
        """L4 anomaly threshold must always be >= continuity threshold."""
        sessions = [_make_r2_press_session(n_presses=25) for _ in range(5)]
        profile = compute_thresholds(sessions)
        anomaly = profile["thresholds"]["l4_mahalanobis_anomaly"]["recommended"]
        continuity = profile["thresholds"]["l4_mahalanobis_continuity"]["recommended"]
        self.assertGreaterEqual(
            anomaly, continuity,
            f"anomaly ({anomaly}) must be >= continuity ({continuity})"
        )

    def test_anomaly_is_mean_plus_3std(self):
        """Verify anomaly = mean + 3*std formula holds when dist_std > 0."""
        sessions = [_make_r2_press_session(n_presses=30) for _ in range(10)]
        profile = compute_thresholds(sessions)
        t = profile["thresholds"]["l4_mahalanobis_anomaly"]
        dist_std = t.get("dist_std")
        if dist_std is not None and dist_std > 0:
            expected = t["dist_mean"] + 3.0 * dist_std
            self.assertAlmostEqual(t["recommended"], round(expected, 3), places=2)
        else:
            # Fallback path: anomaly = max(dist_mean * 1.5, 3.0)
            self.assertGreaterEqual(t["recommended"], 3.0)

    def test_continuity_is_mean_plus_2std(self):
        """Verify continuity = mean + 2*std formula holds when dist_std > 0."""
        sessions = [_make_r2_press_session(n_presses=30) for _ in range(10)]
        profile = compute_thresholds(sessions)
        t_a = profile["thresholds"]["l4_mahalanobis_anomaly"]
        t_c = profile["thresholds"]["l4_mahalanobis_continuity"]
        dist_std = t_a.get("dist_std")
        if dist_std is not None and dist_std > 0:
            mean = t_a["dist_mean"]
            expected_c = mean + 2.0 * dist_std
            self.assertAlmostEqual(t_c["recommended"], round(expected_c, 3), places=2)
        else:
            # Fallback path: continuity = max(dist_mean * 1.0, 2.0)
            self.assertGreaterEqual(t_c["recommended"], 2.0)


if __name__ == "__main__":
    unittest.main()
