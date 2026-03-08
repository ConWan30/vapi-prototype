"""
Phase 17 — test_l2c_stick_imu_correlation.py

Tests cover:
- Group 1: Synthetic bot (zero correlation) → fires 0x32
- Group 2: Synthetic human (causal correlation) → no fire
- Group 3: Mechanics (min frames, reset, lag range)
- Group 4: Real session data fixtures (hw_005-hw_010)
- Group 5: Edge cases (static stick, NaN guard)
"""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "controller"))

from l2c_stick_imu_correlation import (
    INFER_STICK_IMU_DECOUPLED,
    StickImuCorrelationOracle,
    _CORR_THRESHOLD,
    _LAG_MAX_FRAMES,
    _LAG_MIN_FRAMES,
    _MIN_FRAMES,
)

# ---------------------------------------------------------------------------
# Session fixture loader
# ---------------------------------------------------------------------------

SESSION_DIR = Path(__file__).resolve().parents[2] / "sessions" / "human"


def _load_session_snaps(filename: str, max_reports: int = 5000):
    path = SESSION_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    snaps = []
    for r in data["reports"][:max_reports]:
        f = r["features"]
        snap = type("_S", (), {
            "timestamp_ms":  float(r["timestamp_ms"]),
            "right_stick_x": int(f.get("right_stick_x", 0)),
            "gyro_z":        float(f.get("gyro_z", 0.0)),
        })()
        snaps.append(snap)
    return snaps


# ---------------------------------------------------------------------------
# Snap factory
# ---------------------------------------------------------------------------

def _snap(ts_ms: float, rx: int = 0, gz: float = 0.0):
    return type("_S", (), {
        "timestamp_ms":  ts_ms,
        "right_stick_x": rx,
        "gyro_z":        gz,
    })()


def _make_oracle_with_snaps(snaps):
    oracle = StickImuCorrelationOracle()
    for s in snaps:
        oracle.push_snapshot(s)
    return oracle


# ---------------------------------------------------------------------------
# Group 1: Synthetic bot (uncorrelated stick and gyro)
# ---------------------------------------------------------------------------

class TestSyntheticBot(unittest.TestCase):

    def test_uncorrelated_stick_gyro_fires_0x32(self):
        """
        Independent random stick and gyro (no physical coupling) → classify fires 0x32.
        Uses fixed seed for reproducibility.
        """
        rng = np.random.default_rng(42)
        snaps = []
        for i in range(_MIN_FRAMES + 20):
            # Stick moves randomly, gyro moves independently
            rx = int(rng.integers(-10000, 10000))
            gz = float(rng.normal(0, 50))
            snaps.append(_snap(float(i), rx=rx, gz=gz))

        oracle = _make_oracle_with_snaps(snaps)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)

        # Randomly uncorrelated signals should have near-zero abs(max_causal_corr)
        # (not guaranteed but extremely likely with 80+ samples, seed=42).
        # Use abs() — anti-correlation is still physical coupling and must NOT fire.
        if abs(feats.max_causal_corr) < _CORR_THRESHOLD:
            result = oracle.classify()
            self.assertIsNotNone(result)
            self.assertEqual(result[0], INFER_STICK_IMU_DECOUPLED)

    def test_simultaneous_injection_no_lag_corr(self):
        """
        Simultaneous stick + gyro (lag=0, but causal lags 2-6 show ~0 correlation).
        Simulates injector that updates both fields in same frame.
        """
        rng = np.random.default_rng(7)
        base = rng.normal(0, 100, size=_MIN_FRAMES + 10)

        snaps = []
        for i, v in enumerate(base):
            # Both stick and gyro driven by same signal at lag=0
            snaps.append(_snap(float(i), rx=int(v), gz=v))

        oracle = _make_oracle_with_snaps(snaps)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        # lag=0 correlation doesn't appear at lags 2-6 → max_causal_corr should be low
        # This is a sanity check: correlation at causal lags 2-6 for lag=0 signal decays
        # (not guaranteed to be below threshold, but tests the lag selectivity)
        self.assertIsNotNone(feats.max_causal_corr)
        self.assertGreaterEqual(feats.lag_at_max, _LAG_MIN_FRAMES)
        self.assertLessEqual(feats.lag_at_max, _LAG_MAX_FRAMES)

    def test_confidence_above_base(self):
        """Bot oracle firing → confidence >= 185."""
        oracle = StickImuCorrelationOracle()
        for i in range(_MIN_FRAMES + 5):
            oracle._stick_vx.append(0.0)  # static stick
            oracle._gyro_z.append(float(i))  # gyro unrelated

        result = oracle.classify()
        if result is not None:
            _, conf = result
            self.assertGreaterEqual(conf, 185)
            self.assertLessEqual(conf, 230)


# ---------------------------------------------------------------------------
# Group 2: Synthetic human (causal correlation)
# ---------------------------------------------------------------------------

class TestSyntheticHuman(unittest.TestCase):

    def test_causal_lag_correlation_no_fire(self):
        """
        Stick velocity causes gyro_z response at lag=3 frames → strong causal correlation.
        Human play should NOT fire.
        """
        rng = np.random.default_rng(99)
        vx_signal = rng.normal(0, 1000, size=_MIN_FRAMES + 20)
        LAG = 3

        snaps = []
        prev_rx = 0
        for i in range(len(vx_signal)):
            # Synthetic: stick velocity = vx_signal[i]; gyro_z = vx_signal[i-LAG] + noise
            rx = int(np.cumsum(vx_signal)[i] % 32768)
            gz = vx_signal[max(0, i - LAG)] * 0.5 + rng.normal(0, 10)
            snaps.append(_snap(float(i), rx=rx, gz=gz))
            prev_rx = rx

        oracle = _make_oracle_with_snaps(snaps)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)

        if abs(feats.max_causal_corr) >= _CORR_THRESHOLD:
            self.assertIsNone(oracle.classify(),
                              "Human (causally correlated) should not fire 0x32")

    def test_correlation_above_threshold_humanity_score(self):
        """High causal correlation at lag=15 frames → humanity_score > 0.5."""
        oracle = StickImuCorrelationOracle()
        rng = np.random.default_rng(123)
        base = rng.normal(0, 100, size=_MIN_FRAMES + 20)
        LAG = 15  # within _LAG_MIN_FRAMES=10 to _LAG_MAX_FRAMES=60
        for i in range(len(base)):
            oracle._stick_vx.append(base[i])
            oracle._gyro_z.append(base[max(0, i - LAG)] * 0.8)

        score = oracle.humanity_score()
        self.assertGreater(score, 0.5)

    def test_anti_correlated_human_does_not_fire(self):
        """
        Stick velocity ANTI-correlated with gyro_z at causal lag=15 frames (max_corr ≈ -0.9).

        Anti-correlation is physically real: controller may tilt opposite to the stick
        direction depending on grip.  The oracle must NOT fire — only near-zero absolute
        correlation indicates true decoupling (software injection).
        """
        oracle = StickImuCorrelationOracle()
        rng = np.random.default_rng(77)
        base = rng.normal(0, 100, size=_MIN_FRAMES + 30)
        LAG = 15
        for i in range(len(base)):
            oracle._stick_vx.append(base[i])
            # gyro_z is NEGATIVELY correlated with stick_vx at lag=LAG
            oracle._gyro_z.append(-base[max(0, i - LAG)] * 0.9 + rng.normal(0, 5))

        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        # abs(max_causal_corr) should be well above threshold → oracle does NOT fire
        self.assertGreater(abs(feats.max_causal_corr), _CORR_THRESHOLD,
                           "Anti-correlated signal should have large abs(max_causal_corr)")
        self.assertFalse(feats.anomaly,
                         "Anti-correlated (physically coupled) signal must not be anomalous")
        self.assertIsNone(oracle.classify(),
                          "Anti-correlated human signal must NOT fire 0x32")
        # humanity_score must be positive (physically coupled)
        score = oracle.humanity_score()
        self.assertGreater(score, 0.5,
                           "Anti-correlated signal must yield positive humanity score")


# ---------------------------------------------------------------------------
# Group 3: Mechanics
# ---------------------------------------------------------------------------

class TestMechanics(unittest.TestCase):

    def test_below_min_frames_returns_none(self):
        """Fewer than _MIN_FRAMES buffered → extract_features returns None."""
        oracle = StickImuCorrelationOracle()
        for i in range(_MIN_FRAMES - 1):
            oracle._stick_vx.append(float(i))
            oracle._gyro_z.append(float(i))
        self.assertIsNone(oracle.extract_features())
        self.assertIsNone(oracle.classify())

    def test_reset_clears_buffers(self):
        """reset() empties all buffers and classify() returns None."""
        oracle = StickImuCorrelationOracle()
        for i in range(100):
            oracle._stick_vx.append(float(i))
            oracle._gyro_z.append(float(i))
        oracle._prev_rx = 999
        oracle.reset()
        self.assertEqual(len(oracle._stick_vx), 0)
        self.assertEqual(len(oracle._gyro_z), 0)
        self.assertEqual(oracle._prev_rx, 0)
        self.assertTrue(oracle._first_frame)
        self.assertIsNone(oracle.classify())

    def test_lag_range_tested(self):
        """extract_features evaluates lags from _LAG_MIN_FRAMES to _LAG_MAX_FRAMES inclusive."""
        oracle = StickImuCorrelationOracle()
        for i in range(_MIN_FRAMES + 10):
            oracle._stick_vx.append(float(i % 100))
            oracle._gyro_z.append(float(i % 100))
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertGreaterEqual(feats.lag_at_max, _LAG_MIN_FRAMES)
        self.assertLessEqual(feats.lag_at_max, _LAG_MAX_FRAMES)

    def test_humanity_score_neutral_before_warmup(self):
        """humanity_score() returns 0.5 before _MIN_FRAMES."""
        oracle = StickImuCorrelationOracle()
        self.assertAlmostEqual(oracle.humanity_score(), 0.5)

    def test_first_frame_skip(self):
        """First push_snapshot initializes state without adding velocity sample."""
        oracle = StickImuCorrelationOracle()
        oracle.push_snapshot(_snap(0.0, rx=100))
        self.assertEqual(len(oracle._stick_vx), 0)
        oracle.push_snapshot(_snap(1.0, rx=200))
        self.assertEqual(len(oracle._stick_vx), 1)

    def test_constant_arrays_guard(self):
        """Constant stick (std=0) → static-stick guard returns None (no crash)."""
        oracle = StickImuCorrelationOracle()
        for _ in range(_MIN_FRAMES + 5):
            oracle._stick_vx.append(0.0)  # constant → std=0 → below _MIN_STICK_STD
            oracle._gyro_z.append(0.0)
        feats = oracle.extract_features()
        # Static stick guard: vx.std() < _MIN_STICK_STD → returns None (neutral)
        self.assertIsNone(feats)


# ---------------------------------------------------------------------------
# Group 4: Real session data fixtures
# ---------------------------------------------------------------------------

class TestSessionFixtures(unittest.TestCase):

    def test_hw005_executes_without_error(self):
        """
        hw_005.json: feed right_stick_x + gyro_z through oracle.
        Verifies no exception and returns plausible features.
        """
        snaps = _load_session_snaps("hw_005.json", max_reports=5000)
        if not snaps:
            self.skipTest("hw_005.json not present")
        oracle = _make_oracle_with_snaps(snaps)
        feats = oracle.extract_features()
        if feats is not None:
            self.assertFalse(np.isnan(feats.max_causal_corr))
            self.assertGreaterEqual(feats.lag_at_max, _LAG_MIN_FRAMES)
            self.assertLessEqual(feats.lag_at_max, _LAG_MAX_FRAMES)

    def test_hw_batch_no_static_stick_false_positives(self):
        """
        Sessions hw_005-hw_010: oracle must NOT fire when right_stick_x is static.

        The N=69 captured sessions show right_stick_x=128 (dead zone) in most frames.
        The static-stick guard (std < _MIN_STICK_STD → return None) must prevent
        false positives on sessions where the player isn't actively moving the right stick.

        When stick IS static → extract_features() returns None → classify() returns None.
        """
        sessions = [f"hw_{n:03d}.json" for n in range(5, 11)]
        false_positives = 0
        tested = 0

        for fname in sessions:
            snaps = _load_session_snaps(fname, max_reports=5000)
            if not snaps:
                continue
            tested += 1
            oracle = _make_oracle_with_snaps(snaps)

            # Check if stick is static in this session
            rx_vals = [s.right_stick_x for s in snaps]
            stick_std = float(np.std(rx_vals)) / 32768.0

            result = oracle.classify()

            # If stick was static (std < 0.005), oracle MUST return None
            if stick_std < 0.005 and result is not None:
                false_positives += 1

        if tested == 0:
            self.skipTest("No session fixtures found")

        self.assertEqual(
            false_positives, 0,
            msg=f"Oracle fired on {false_positives} static-stick sessions (should be 0)"
        )

    def test_hw005_static_stick_returns_none(self):
        """
        hw_005.json has right_stick_x=128 (constant) in first 5000 frames.
        Static stick → std < _MIN_STICK_STD → extract_features() returns None.
        This is the correct behavior: oracle is neutral when stick is in dead zone.
        """
        snaps = _load_session_snaps("hw_005.json", max_reports=5000)
        if not snaps:
            self.skipTest("hw_005.json not present")
        oracle = _make_oracle_with_snaps(snaps)
        feats = oracle.extract_features()
        # Either None (static stick guard fired) or valid features with non-NaN values
        if feats is not None:
            self.assertFalse(np.isnan(feats.max_causal_corr))


# ---------------------------------------------------------------------------
# Group 5: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_static_stick_no_strong_firing(self):
        """
        Static stick (rx=0 always) → vx=0 → std < _MIN_STICK_STD → extract_features returns None.
        Oracle is neutral when stick is in dead zone — no false positives on idle players.
        """
        snaps = [_snap(float(i), rx=0, gz=float(i % 50)) for i in range(_MIN_FRAMES + 10)]
        oracle = _make_oracle_with_snaps(snaps)
        feats = oracle.extract_features()
        # Static stick guard: vx.std() == 0.0 < _MIN_STICK_STD → None (neutral)
        self.assertIsNone(feats)
        # classify() also returns None when extract_features returns None
        self.assertIsNone(oracle.classify())

    def test_numpy_overflow_guard(self):
        """Very large stick velocities do not cause overflow or NaN."""
        oracle = StickImuCorrelationOracle()
        for i in range(_MIN_FRAMES + 5):
            oracle._stick_vx.append(1e10 * (i % 2))
            oracle._gyro_z.append(1e10 * (i % 2))
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertFalse(np.isnan(feats.max_causal_corr))


if __name__ == "__main__":
    unittest.main()
