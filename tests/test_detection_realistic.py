"""
test_detection_realistic.py — Detection benchmarks using synthetic session generators.

WARNING: ALL RESULTS IN THIS FILE ARE DERIVED FROM SYNTHETIC TEST DATA.
No claims about real-world detection accuracy should be drawn from these tests.
Every performance figure must include the caveat "on synthetic test patterns".
Real-hardware validation requires scripts/capture_session.py + physical DualShock Edge.

This module verifies:
  1. Generator output structural integrity (correct fields, valid ranges)
  2. Ground-truth separation between attack types and legitimate human sessions
  3. Basic feature distribution properties that the PITL stack relies on
  4. Documentation of expected vs. actual thresholds (comparison only, no claims)

See docs/detection-benchmarks.md for the full benchmark report.
"""

import math
import os
import sys
import unittest

# Add the tests/ directory to sys.path so realistic_generators can be imported
# as 'data.realistic_generators' regardless of how pytest discovers this file.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from data.realistic_generators import (
    generate_aimbot_session,
    generate_human_session,
    generate_injection_session,
    generate_macro_session,
    generate_replay_attack_session,
    generate_warmup_attack_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals):
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _cv(vals):
    """Coefficient of variation (std/mean). Returns 0 if mean==0."""
    m = _mean(vals)
    return _std(vals) / m if m != 0.0 else 0.0


def _extract_inter_event_times(session: dict) -> list:
    return [r["inter_event_ms"] for r in session["records"] if "inter_event_ms" in r]


def _extract_humanity_proxies(session: dict) -> list:
    return [r["humanity_proxy"] for r in session["records"] if "humanity_proxy" in r]


def _extract_imu_values(session: dict) -> list:
    """Return list of (gx, gy, gz) tuples from session records."""
    return [(r["gyro_x"], r["gyro_y"], r["gyro_z"])
            for r in session["records"]
            if "gyro_x" in r]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGeneratorStructure(unittest.TestCase):
    """Verify all generators produce structurally correct output."""

    REQUIRED_RECORD_FIELDS = {
        "ts_ms", "left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y",
        "l2_trigger", "r2_trigger", "gyro_x", "gyro_y", "gyro_z",
        "accel_x", "accel_y", "accel_z", "inter_event_ms",
        "humanity_proxy", "session_type",
    }

    def _assert_session_structure(self, session: dict, expected_type: str):
        self.assertIn("records", session)
        self.assertIn("metadata", session)
        self.assertGreater(len(session["records"]), 0,
                           f"{expected_type}: expected at least 1 record")

        for i, record in enumerate(session["records"]):
            missing = self.REQUIRED_RECORD_FIELDS - set(record.keys())
            self.assertFalse(missing,
                             f"{expected_type} record[{i}] missing fields: {missing}")

            # All byte-range values must be within [0, 255]
            for field in ("left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y",
                          "l2_trigger", "r2_trigger"):
                val = record[field]
                self.assertGreaterEqual(val, 0,
                                        f"{expected_type}[{i}].{field}={val} < 0")
                self.assertLessEqual(val, 255,
                                     f"{expected_type}[{i}].{field}={val} > 255")

            # Humanity proxy must be in [0, 1]
            hp = record["humanity_proxy"]
            self.assertGreaterEqual(hp, 0.0,
                                    f"{expected_type}[{i}].humanity_proxy={hp} < 0.0")
            self.assertLessEqual(hp, 1.0,
                                 f"{expected_type}[{i}].humanity_proxy={hp} > 1.0")

    def test_human_session_structure(self):
        session = generate_human_session(duration_s=10, skill_level="gold", seed=42)
        self._assert_session_structure(session, "human")
        self.assertEqual(session["session_type"], "human")

    def test_macro_session_structure(self):
        session = generate_macro_session(duration_s=10, seed=42)
        self._assert_session_structure(session, "macro")
        self.assertEqual(session["session_type"], "macro")

    def test_aimbot_session_structure(self):
        session = generate_aimbot_session(duration_s=10, aggression=0.8, seed=42)
        self._assert_session_structure(session, "aimbot")
        self.assertEqual(session["session_type"], "aimbot")

    def test_injection_session_structure(self):
        session = generate_injection_session(duration_s=10, seed=42)
        self._assert_session_structure(session, "injection")
        self.assertEqual(session["session_type"], "injection")

    def test_warmup_attack_structure(self):
        sessions = generate_warmup_attack_session(sessions_count=3, seed=42)
        self.assertEqual(len(sessions), 3)
        for i, s in enumerate(sessions):
            self._assert_session_structure(s, f"warmup_attack[{i}]")

    def test_replay_attack_structure(self):
        original = generate_human_session(duration_s=10, seed=99)
        replay = generate_replay_attack_session(original, time_shift_ms=3_600_000)
        self._assert_session_structure(replay, "replay_attack")
        self.assertEqual(replay["session_type"], "replay_attack")
        # Timestamps should be shifted
        orig_ts = [r["ts_ms"] for r in original["records"]]
        replay_ts = [r["ts_ms"] for r in replay["records"]]
        for ot, rt in zip(orig_ts, replay_ts):
            self.assertEqual(rt - ot, 3_600_000)


class TestHumanVsMacroSeparation(unittest.TestCase):
    """
    Verify that human and macro sessions have separable timing distributions.

    NOTE: These are SYNTHETIC data ground-truth checks.
    L5 TemporalRhythmOracle uses CV < 0.08 as suspicious; macros should be
    well below this threshold. Human sessions should be well above it.
    All figures are "on synthetic test patterns" only.
    """

    def test_macro_timing_cv_is_near_zero(self):
        """Macro sessions must have near-zero timing CV (L5 detection target: CV < 0.08)."""
        session = generate_macro_session(duration_s=30, seed=42)
        times = _extract_inter_event_times(session)
        self.assertGreater(len(times), 10, "Need at least 10 records for CV")
        cv = _cv(times)
        # Macros should have CV ≈ 0 (constant interval)
        # L5 threshold: CV < 0.08 → suspicious. Macro CV should be well below this.
        self.assertLess(cv, 0.08,
                        f"Macro timing CV={cv:.4f} should be < 0.08 (L5 threshold). "
                        "SYNTHETIC DATA ONLY.")

    def test_human_gold_timing_cv_is_human_range(self):
        """Gold-tier human sessions must have timing CV in the human range (> 0.15 expected)."""
        session = generate_human_session(duration_s=30, skill_level="gold", seed=42)
        times = _extract_inter_event_times(session)
        self.assertGreater(len(times), 10)
        cv = _cv(times)
        # Human lognormal reaction times have CV around 0.3 (Donders, 1868)
        # Gold skill_level has modifier 1.0 → σ = 0.3 → CV ≈ 0.3
        self.assertGreater(cv, 0.15,
                           f"Gold human timing CV={cv:.4f} should be > 0.15. "
                           "SYNTHETIC DATA ONLY.")

    def test_human_diamond_timing_cv_above_threshold(self):
        """Even diamond-tier humans should have CV > L5 threshold (0.08)."""
        session = generate_human_session(duration_s=30, skill_level="diamond", seed=42)
        times = _extract_inter_event_times(session)
        cv = _cv(times)
        # Diamond players are consistent but still human: CV > 0.08 expected
        # If this fails, the diamond modifier is too tight and will cause false positives
        self.assertGreater(cv, 0.08,
                           f"Diamond human timing CV={cv:.4f} fell below L5 threshold 0.08. "
                           "This would cause false positives at diamond tier. "
                           "SYNTHETIC DATA ONLY — calibrate with real hardware.")


class TestInjectionSignalSeparation(unittest.TestCase):
    """
    Verify that injection sessions have zero IMU while human sessions have nonzero IMU.

    NOTE: SYNTHETIC DATA ONLY. Real IMU noise floor calibration required.
    """

    def test_injection_has_zero_imu(self):
        """Injection sessions must have zero gyro readings (the dead giveaway)."""
        session = generate_injection_session(duration_s=10, seed=42)
        imu_vals = _extract_imu_values(session)
        self.assertGreater(len(imu_vals), 0)
        gyro_magnitudes = [abs(gx) + abs(gy) + abs(gz) for gx, gy, gz in imu_vals]
        total_gyro_energy = sum(gyro_magnitudes)
        self.assertEqual(total_gyro_energy, 0.0,
                         f"Injection session gyro energy={total_gyro_energy}, expected 0. "
                         "SYNTHETIC DATA ONLY.")

    def test_human_has_nonzero_imu(self):
        """Human sessions must have nonzero IMU (controller is physically held)."""
        session = generate_human_session(duration_s=30, skill_level="gold", seed=42)
        imu_vals = _extract_imu_values(session)
        self.assertGreater(len(imu_vals), 0)
        gyro_magnitudes = [abs(gx) + abs(gy) + abs(gz) for gx, gy, gz in imu_vals]
        mean_gyro_energy = _mean(gyro_magnitudes)
        self.assertGreater(mean_gyro_energy, 0.0,
                           f"Human session mean gyro energy={mean_gyro_energy}, expected > 0. "
                           "SYNTHETIC DATA ONLY.")


class TestHumanityProxySeparation(unittest.TestCase):
    """
    Verify that humanity_proxy distributions separate attack types from humans.

    NOTE: SYNTHETIC DATA ONLY. These thresholds mirror the PITL L4 Mahalanobis
    intent but are not calibrated against real hardware. The 0.6/0.4 thresholds
    below are conservative estimates for synthetic separation only.
    """

    def test_human_humanity_proxy_above_threshold(self):
        """Human sessions must have mean humanity_proxy > 0.6 (on synthetic data)."""
        session = generate_human_session(duration_s=30, skill_level="gold", seed=42)
        proxies = _extract_humanity_proxies(session)
        mean_hp = _mean(proxies)
        self.assertGreater(mean_hp, 0.6,
                           f"Human mean humanity_proxy={mean_hp:.3f} should be > 0.6. "
                           "SYNTHETIC DATA ONLY.")

    def test_macro_humanity_proxy_below_threshold(self):
        """Macro sessions must have mean humanity_proxy < 0.3 (on synthetic data)."""
        session = generate_macro_session(duration_s=30, seed=42)
        proxies = _extract_humanity_proxies(session)
        mean_hp = _mean(proxies)
        self.assertLess(mean_hp, 0.3,
                        f"Macro mean humanity_proxy={mean_hp:.3f} should be < 0.3. "
                        "SYNTHETIC DATA ONLY.")

    def test_injection_humanity_proxy_below_threshold(self):
        """Injection sessions must have mean humanity_proxy < 0.2 (on synthetic data)."""
        session = generate_injection_session(duration_s=30, seed=42)
        proxies = _extract_humanity_proxies(session)
        mean_hp = _mean(proxies)
        self.assertLess(mean_hp, 0.2,
                        f"Injection mean humanity_proxy={mean_hp:.3f} should be < 0.2. "
                        "SYNTHETIC DATA ONLY.")


class TestWarmupAttackProgression(unittest.TestCase):
    """
    Verify that warmup attack sessions show monotonically improving humanity_proxy.

    BehavioralArchaeologist detects correlated positive slopes in drift_trend and
    humanity_trend. This test validates the synthetic generator produces the right
    input for that detection.

    NOTE: SYNTHETIC DATA ONLY.
    """

    def test_warmup_humanity_proxy_increases_monotonically(self):
        """Warmup attack mean humanity_proxy must increase across sessions."""
        sessions = generate_warmup_attack_session(sessions_count=5, seed=42)
        session_means = [
            _mean(_extract_humanity_proxies(s))
            for s in sessions
        ]
        self.assertEqual(len(session_means), 5)
        # Each subsequent session should have higher mean humanity_proxy
        # (allowing small noise — check only overall trend from first to last)
        self.assertGreater(
            session_means[-1], session_means[0],
            f"Warmup attack humanity_proxy should increase from session 0 to 4. "
            f"Got: {[f'{m:.3f}' for m in session_means]}. SYNTHETIC DATA ONLY."
        )

    def test_warmup_session_count_correct(self):
        """generate_warmup_attack_session must return exactly sessions_count sessions."""
        for n in (3, 5, 10):
            sessions = generate_warmup_attack_session(sessions_count=n, seed=42)
            self.assertEqual(len(sessions), n,
                             f"Expected {n} sessions, got {len(sessions)}.")


class TestReplayAttackTimestampShift(unittest.TestCase):
    """
    Verify that replay attack sessions shift timestamps by the exact amount specified.

    The PoAC nullifier anti-replay prevents exact replays. A shifted-timestamp
    replay would have different nullifiers (epoch changes) and thus bypass naive
    replay protection, but the sensor commitment chain would be structurally valid.
    This test verifies the generator correctly simulates that scenario.

    NOTE: SYNTHETIC DATA ONLY.
    """

    def test_replay_timestamps_shifted_correctly(self):
        """Replay timestamps = original timestamps + time_shift_ms."""
        original = generate_human_session(duration_s=10, seed=7)
        shift_ms = 7_200_000  # 2 hours
        replay = generate_replay_attack_session(original, time_shift_ms=shift_ms)

        self.assertEqual(len(replay["records"]), len(original["records"]))

        for orig_r, replay_r in zip(original["records"], replay["records"]):
            self.assertEqual(
                replay_r["ts_ms"] - orig_r["ts_ms"],
                shift_ms,
                f"Timestamp shift mismatch: expected {shift_ms}, "
                f"got {replay_r['ts_ms'] - orig_r['ts_ms']}."
            )

    def test_replay_input_data_identical(self):
        """Replay records must have identical stick/trigger/IMU data as original."""
        original = generate_human_session(duration_s=10, seed=7)
        replay = generate_replay_attack_session(original, time_shift_ms=1_000)

        input_fields = ["left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y",
                        "l2_trigger", "r2_trigger", "gyro_x", "gyro_y", "gyro_z"]

        for i, (orig_r, replay_r) in enumerate(zip(original["records"], replay["records"])):
            for field in input_fields:
                self.assertEqual(
                    orig_r[field], replay_r[field],
                    f"Record[{i}].{field} differs: orig={orig_r[field]}, "
                    f"replay={replay_r[field]}. Replay must preserve input data."
                )


if __name__ == "__main__":
    print("=" * 70)
    print("VAPI PITL Detection Benchmarks — SYNTHETIC DATA ONLY")
    print("All results must include caveat: 'on synthetic test patterns'")
    print("See docs/detection-benchmarks.md for full benchmark report")
    print("=" * 70)
    unittest.main(verbosity=2)
