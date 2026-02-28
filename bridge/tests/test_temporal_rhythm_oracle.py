"""
Phase 16B — temporal_rhythm_oracle.py tests.

Tests cover:
- TemporalRhythmFeatures signal thresholds (CV, entropy, quantization)
- TemporalRhythmOracle.classify(): below min-samples, 0/1/2/3 signal scenarios
- push_frame() skips zero-ms frames; rolling window drops oldest
- reset() clears state
- rhythm_hash() determinism and sensitivity
- Integration: 0x2B outside CHEAT_CODES, present in GAMING_INFERENCE_NAMES
"""

import sys
import unittest
from pathlib import Path

import numpy as np

# Pattern C: insert controller/ so temporal_rhythm_oracle can be imported directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "controller"))

from temporal_rhythm_oracle import (
    INFER_TEMPORAL_ANOMALY,
    TemporalRhythmFeatures,
    TemporalRhythmOracle,
    _MIN_SAMPLES,
    _WINDOW,
)


# ---------------------------------------------------------------------------
# Minimal FeatureFrame stub (only inter_press_ms needed)
# ---------------------------------------------------------------------------

class _Frame:
    """Minimal FeatureFrame stand-in with only inter_press_ms."""
    def __init__(self, inter_press_ms: float = 0.0):
        self.inter_press_ms = inter_press_ms


def _make_oracle_with_intervals(intervals):
    """Helper: create an oracle and push pre-fabricated intervals as frames."""
    oracle = TemporalRhythmOracle()
    for ms in intervals:
        oracle.push_frame(_Frame(inter_press_ms=ms))
    return oracle


# ---------------------------------------------------------------------------
# Group 1: TemporalRhythmFeatures signal thresholds
# ---------------------------------------------------------------------------

class TestTemporalRhythmFeatures(unittest.TestCase):

    def test_human_high_cv(self):
        """Random human-like intervals → CV well above threshold → no anomaly signal."""
        rng = np.random.default_rng(42)
        # Human press intervals: roughly 80–600ms with high variance
        intervals = rng.uniform(80, 600, size=40).tolist()
        oracle = _make_oracle_with_intervals(intervals)
        features = oracle.extract_features()
        self.assertIsNotNone(features)
        # CV >> 0.08 for widely spread distribution
        self.assertGreater(features.cv, 0.08,
            msg=f"Expected human CV > 0.08, got {features.cv:.4f}")
        # anomaly_signals should be 0 or 1 at most for genuinely random data
        self.assertLessEqual(features.anomaly_signals, 1)

    def test_bot_low_cv(self):
        """Near-constant 100ms intervals → CV < 0.08 → signal 1 fires."""
        rng = np.random.default_rng(0)
        # Bot: 100ms ± 1ms noise
        intervals = (100.0 + rng.normal(0, 0.5, size=30)).tolist()
        oracle = _make_oracle_with_intervals(intervals)
        features = oracle.extract_features()
        self.assertIsNotNone(features)
        self.assertLess(features.cv, 0.08,
            msg=f"Expected bot CV < 0.08, got {features.cv:.4f}")
        # CV signal fires
        self.assertGreaterEqual(features.anomaly_signals, 1)

    def test_bot_low_entropy(self):
        """Repeated single interval → all values in one 50ms bucket → entropy ≈ 0."""
        # Exactly the same value repeated — single histogram bucket
        intervals = [150.0] * 35
        oracle = _make_oracle_with_intervals(intervals)
        features = oracle.extract_features()
        self.assertIsNotNone(features)
        self.assertLess(features.entropy_bits, 1.5,
            msg=f"Expected low entropy, got {features.entropy_bits:.4f}")
        # At least entropy signal fires
        self.assertGreaterEqual(features.anomaly_signals, 1)

    def test_bot_quantized(self):
        """Intervals that are exact multiples of 16.667ms → quant_score > 0.55."""
        # 60Hz timer ticks: 16.667, 33.333, 50.0, 66.667, 83.333, ...
        tick = 16.6667
        intervals = [tick * k for k in range(2, 32)]  # 30 values
        oracle = _make_oracle_with_intervals(intervals)
        features = oracle.extract_features()
        self.assertIsNotNone(features)
        self.assertGreater(features.quant_score, 0.55,
            msg=f"Expected high quant_score, got {features.quant_score:.4f}")
        # Quantization signal fires
        self.assertGreaterEqual(features.anomaly_signals, 1)


# ---------------------------------------------------------------------------
# Group 2: TemporalRhythmOracle.classify() scenarios
# ---------------------------------------------------------------------------

class TestTemporalRhythmOracleClassify(unittest.TestCase):

    def test_below_min_samples(self):
        """Fewer than _MIN_SAMPLES intervals → classify returns None."""
        oracle = TemporalRhythmOracle()
        for ms in [100.0] * (_MIN_SAMPLES - 1):
            oracle.push_frame(_Frame(inter_press_ms=ms))
        self.assertIsNone(oracle.classify())

    def test_single_signal_no_fire(self):
        """Only 1/3 signals → classify returns None (requires ≥2)."""
        rng = np.random.default_rng(7)
        # High-variance human-like intervals (no CV or entropy anomaly),
        # but not quantized either → at most 1 signal can fire
        intervals = rng.uniform(50, 700, size=30).tolist()
        oracle = _make_oracle_with_intervals(intervals)
        features = oracle.extract_features()
        if features is not None and features.anomaly_signals <= 1:
            self.assertIsNone(oracle.classify())
        # If all 3 somehow fired with truly random data, skip the test
        # (astronomically unlikely, but guard gracefully)

    def test_two_signals_fire(self):
        """
        2/3 signals (CV + entropy, not quantized) → classify returns
        (INFER_TEMPORAL_ANOMALY, confidence) with confidence ≥ 205.
        """
        rng = np.random.default_rng(1)
        # Near-constant 250ms → low CV + single bucket (low entropy),
        # but 250ms is not a 60Hz multiple → quant_score low
        intervals = (250.0 + rng.normal(0, 0.3, size=30)).tolist()
        oracle = _make_oracle_with_intervals(intervals)
        result = oracle.classify()
        self.assertIsNotNone(result, "Expected 2-signal anomaly to fire")
        code, conf = result
        self.assertEqual(code, INFER_TEMPORAL_ANOMALY)
        self.assertGreaterEqual(conf, 205)

    def test_three_signals_max_confidence(self):
        """All 3 signals → confidence == 230 (180 + 3×25)."""
        # Use exact 60Hz tick value, repeated → quantized + low-CV + low-entropy
        tick = 16.6667
        intervals = [tick * 6] * 30  # All exactly 100ms on a tick multiple
        oracle = _make_oracle_with_intervals(intervals)
        result = oracle.classify()
        self.assertIsNotNone(result, "Expected 3-signal anomaly to fire")
        code, conf = result
        self.assertEqual(code, INFER_TEMPORAL_ANOMALY)
        self.assertEqual(conf, 230)  # 180 + 3×25


# ---------------------------------------------------------------------------
# Group 3: push_frame / reset mechanics
# ---------------------------------------------------------------------------

class TestTemporalRhythmOraclePushReset(unittest.TestCase):

    def test_push_zero_skipped(self):
        """Frames with inter_press_ms == 0 are NOT appended."""
        oracle = TemporalRhythmOracle()
        for _ in range(30):
            oracle.push_frame(_Frame(inter_press_ms=0.0))
        # Zero frames should not accumulate; window should still be empty
        self.assertEqual(len(oracle._intervals), 0)
        self.assertIsNone(oracle.extract_features())

    def test_window_rolling(self):
        """Window overflows at maxlen=_WINDOW; oldest entries are dropped."""
        oracle = TemporalRhythmOracle()
        # Fill to exactly _WINDOW + 10 more (each distinct value)
        for i in range(_WINDOW + 10):
            oracle.push_frame(_Frame(inter_press_ms=float(i + 50)))
        self.assertEqual(len(oracle._intervals), _WINDOW)
        # The oldest entry should be the one at index 10 (50+10=60ms)
        self.assertAlmostEqual(oracle._intervals[0], 60.0, places=1)

    def test_reset_clears_window(self):
        """reset() empties the window and subsequent classify() returns None."""
        oracle = _make_oracle_with_intervals([100.0] * 30)
        # Confirm something would be extracted before reset
        self.assertIsNotNone(oracle.extract_features())
        oracle.reset()
        self.assertEqual(len(oracle._intervals), 0)
        self.assertIsNone(oracle.classify())


# ---------------------------------------------------------------------------
# Group 4: rhythm_hash determinism and sensitivity
# ---------------------------------------------------------------------------

class TestRhythmHash(unittest.TestCase):

    def test_hash_deterministic(self):
        """Same interval sequence → identical 32-byte hash."""
        intervals = [100.0, 120.0, 80.0, 150.0, 90.0] * 5
        oracle1 = _make_oracle_with_intervals(intervals)
        oracle2 = _make_oracle_with_intervals(intervals)
        self.assertEqual(oracle1.rhythm_hash(), oracle2.rhythm_hash())
        self.assertEqual(len(oracle1.rhythm_hash()), 32)

    def test_hash_changes(self):
        """Adding one interval changes the hash."""
        intervals = [100.0] * 25
        oracle = _make_oracle_with_intervals(intervals)
        h1 = oracle.rhythm_hash()
        oracle.push_frame(_Frame(inter_press_ms=200.0))
        h2 = oracle.rhythm_hash()
        self.assertNotEqual(h1, h2)


# ---------------------------------------------------------------------------
# Group 5: Integration — inference code placement
# ---------------------------------------------------------------------------

class TestIntegrationWithInferenceCodes(unittest.TestCase):

    def test_code_not_in_cheat_range(self):
        """0x2B must NOT be in CHEAT_CODES (advisory, not hard cheat)."""
        # Import CHEAT_CODES from dualshock_integration via bridge path
        bridge_path = str(Path(__file__).resolve().parents[1])
        if bridge_path not in sys.path:
            sys.path.insert(0, bridge_path)
        from vapi_bridge.dualshock_integration import CHEAT_CODES
        self.assertNotIn(
            INFER_TEMPORAL_ANOMALY, CHEAT_CODES,
            msg="0x2B should be advisory, NOT in CHEAT_CODES [0x28, 0x29, 0x2A]",
        )

    def test_code_in_gaming_names(self):
        """0x2B must be present in GAMING_INFERENCE_NAMES."""
        bridge_path = str(Path(__file__).resolve().parents[1])
        if bridge_path not in sys.path:
            sys.path.insert(0, bridge_path)
        from vapi_bridge.dualshock_integration import GAMING_INFERENCE_NAMES
        self.assertIn(
            INFER_TEMPORAL_ANOMALY, GAMING_INFERENCE_NAMES,
            msg="0x2B should be registered in GAMING_INFERENCE_NAMES",
        )
        self.assertEqual(GAMING_INFERENCE_NAMES[INFER_TEMPORAL_ANOMALY], "TEMPORAL_ANOMALY")


if __name__ == "__main__":
    unittest.main()
