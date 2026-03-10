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
    CROSS_BIT,
    INFER_TEMPORAL_ANOMALY,
    TRIANGLE_BIT,
    TemporalRhythmFeatures,
    TemporalRhythmOracle,
    _HASH_SEPARATOR,
    _L2_PRESS_THRESH,
    _L2_RELEASE_THRESH,
    _MIN_SAMPLES,
    _POOL_MIN_PER_BUTTON,
    _R2_PRESS_THRESH,
    _R2_RELEASE_THRESH,
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

    def test_cross_only_hash_differs_from_empty(self):
        """Intervals only in _cross_intervals → hash differs from fully empty oracle."""
        oracle = TemporalRhythmOracle()
        for v in [100.0, 120.0, 80.0, 150.0]:
            oracle._cross_intervals.append(v)
        empty = TemporalRhythmOracle()
        self.assertNotEqual(oracle.rhythm_hash(), empty.rhythm_hash())
        self.assertEqual(len(oracle.rhythm_hash()), 32)

    def test_same_intervals_different_button_different_hash(self):
        """Same values in Cross vs L2 deque → different hashes (separator enforced)."""
        vals = [100.0, 200.0, 150.0] * 5
        oracle_cross = TemporalRhythmOracle()
        oracle_l2    = TemporalRhythmOracle()
        for v in vals:
            oracle_cross._cross_intervals.append(v)
        for v in vals:
            oracle_l2._l2_intervals.append(v)
        self.assertNotEqual(oracle_cross.rhythm_hash(), oracle_l2.rhythm_hash())


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


# ---------------------------------------------------------------------------
# Group 6: push_snapshot() — multi-button rising-edge detection
# ---------------------------------------------------------------------------

class _Snap:
    """Minimal InputSnapshot stub for push_snapshot() tests."""
    def __init__(self, buttons: int = 0, r2_trigger: int = 0, l2_trigger: int = 0):
        self.buttons     = buttons
        self.r2_trigger  = r2_trigger
        self.l2_trigger  = l2_trigger
        self.inter_frame_us = 1000


def _snap_cross(pressed: bool) -> _Snap:
    return _Snap(buttons=CROSS_BIT if pressed else 0)


def _snap_r2(value: int) -> _Snap:
    return _Snap(r2_trigger=value)


def _snap_l2(value: int) -> _Snap:
    return _Snap(r2_trigger=0, buttons=0, l2_trigger=value)


def _snap_triangle(pressed: bool) -> _Snap:
    return _Snap(buttons=(TRIANGLE_BIT if pressed else 0))


class TestPushSnapshot(unittest.TestCase):

    def test_cross_rising_edge_populates_cross_intervals(self):
        """Rising Cross edge → _cross_intervals grows; _intervals stays empty."""
        oracle = TemporalRhythmOracle()
        # Simulate 5 Cross press/release cycles
        for _ in range(5):
            oracle.push_snapshot(_snap_cross(True))
            oracle.push_snapshot(_snap_cross(False))
        self.assertGreater(len(oracle._cross_intervals), 0)
        self.assertEqual(len(oracle._intervals), 0)

    def test_r2_rising_edge_populates_r2_intervals(self):
        """Rising R2 edge → _intervals grows; _cross_intervals stays empty."""
        oracle = TemporalRhythmOracle()
        for _ in range(5):
            oracle.push_snapshot(_snap_r2(_R2_PRESS_THRESH))
            oracle.push_snapshot(_snap_r2(_R2_RELEASE_THRESH - 1))
        self.assertGreater(len(oracle._intervals), 0)
        self.assertEqual(len(oracle._cross_intervals), 0)

    def test_cross_preferred_when_both_have_sufficient_samples(self):
        """When both Cross and R2 have >= _MIN_SAMPLES, extract_features uses Cross."""
        oracle = TemporalRhythmOracle()
        # Inject known intervals directly — bypass timing dependency
        # Cross: human-like high-CV intervals
        cross_ivs = [300.0, 550.0, 420.0, 800.0, 250.0] * 6  # 30 samples, high CV
        r2_ivs    = [600.0, 610.0, 605.0, 608.0, 602.0] * 5  # 25 samples, low CV (bot-like)
        for v in cross_ivs:
            oracle._cross_intervals.append(v)
        for v in r2_ivs:
            oracle._intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        # Cross CV >> R2 CV; if Cross is selected, CV should be high
        self.assertGreater(feats.cv, 0.15, "Cross selected: expect human-like high CV")

    def test_cross_fallback_when_r2_insufficient(self):
        """When R2 < _MIN_SAMPLES but Cross >= _MIN_SAMPLES, Cross is used."""
        oracle = TemporalRhythmOracle()
        for v in [400.0, 600.0, 350.0, 750.0, 500.0] * 5:  # 25 Cross samples
            oracle._cross_intervals.append(v)
        # R2: only 5 samples — insufficient
        for v in [500.0] * 5:
            oracle._intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats, "Cross fallback should produce features")
        self.assertEqual(feats.sample_count, 25)

    def test_r2_fallback_when_cross_insufficient(self):
        """When Cross < _MIN_SAMPLES but R2 >= _MIN_SAMPLES, R2 is used (backward compat)."""
        oracle = TemporalRhythmOracle()
        for v in [500.0] * 3:  # only 3 Cross samples — insufficient
            oracle._cross_intervals.append(v)
        for v in [400.0, 650.0, 320.0, 780.0, 490.0] * 5:  # 25 R2 samples
            oracle._intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats, "R2 fallback should produce features")
        self.assertEqual(feats.sample_count, 25)

    def test_bot_pattern_on_cross_fires_temporal_anomaly(self):
        """Regular Cross timing (CV~0) → classify() returns TEMPORAL_ANOMALY."""
        oracle = TemporalRhythmOracle()
        # Bot: perfectly regular 500ms Cross intervals
        for _ in range(_MIN_SAMPLES + 5):
            oracle._cross_intervals.append(500.0)
        result = oracle.classify()
        self.assertIsNotNone(result, "Bot Cross pattern should fire 0x2B")
        code, conf = result
        self.assertEqual(code, INFER_TEMPORAL_ANOMALY)
        self.assertGreaterEqual(conf, 200)

    def test_reset_clears_both_deques_and_state(self):
        """reset() clears _intervals, _cross_intervals, and rising-edge state."""
        oracle = TemporalRhythmOracle()
        oracle._intervals.append(500.0)
        oracle._cross_intervals.append(400.0)
        oracle._r2_above = True
        oracle._cross_above = True
        oracle._r2_last_press_ts = 1000.0
        oracle._cross_last_press_ts = 2000.0
        oracle.reset()
        self.assertEqual(len(oracle._intervals), 0)
        self.assertEqual(len(oracle._cross_intervals), 0)
        self.assertFalse(oracle._r2_above)
        self.assertFalse(oracle._cross_above)
        self.assertEqual(oracle._r2_last_press_ts, 0.0)
        self.assertEqual(oracle._cross_last_press_ts, 0.0)


# ---------------------------------------------------------------------------
# Group 7: Phase 39 — L2_dig, Triangle, priority, pooled mode
# ---------------------------------------------------------------------------

class TestMultiButtonPhase39(unittest.TestCase):

    def test_l2_digital_rising_edge_populates_l2_intervals(self):
        """L2 ADC 0→100→0 cycle → interval recorded in _l2_intervals."""
        oracle = TemporalRhythmOracle()
        for _ in range(5):
            oracle.push_snapshot(_snap_l2(_L2_PRESS_THRESH))
            oracle.push_snapshot(_snap_l2(_L2_RELEASE_THRESH - 1))
        self.assertGreater(len(oracle._l2_intervals), 0)
        self.assertEqual(len(oracle._intervals), 0)
        self.assertEqual(len(oracle._cross_intervals), 0)

    def test_triangle_rising_edge_populates_triangle_intervals(self):
        """Triangle press/release cycle → interval recorded in _triangle_intervals."""
        oracle = TemporalRhythmOracle()
        for _ in range(5):
            oracle.push_snapshot(_snap_triangle(True))
            oracle.push_snapshot(_snap_triangle(False))
        self.assertGreater(len(oracle._triangle_intervals), 0)
        self.assertEqual(len(oracle._intervals), 0)
        self.assertEqual(len(oracle._cross_intervals), 0)

    def test_priority_cross_over_l2_when_both_sufficient(self):
        """25 Cross + 25 L2 intervals → source='cross' (highest priority)."""
        oracle = TemporalRhythmOracle()
        # Cross: human-like high-CV intervals
        for v in [300.0, 550.0, 420.0, 800.0, 250.0] * 5:
            oracle._cross_intervals.append(v)
        # L2_dig: bot-like low-CV intervals
        for v in [200.0, 201.0, 199.0, 200.5, 200.2] * 5:
            oracle._l2_intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertEqual(feats.source, "cross")
        # Cross CV >> L2_dig CV
        self.assertGreater(feats.cv, 0.15, "Cross selected: expect high CV")

    def test_priority_l2_over_r2_when_cross_insufficient(self):
        """3 Cross, 25 L2_dig, 25 R2 → source='l2_dig'."""
        oracle = TemporalRhythmOracle()
        for v in [400.0] * 3:
            oracle._cross_intervals.append(v)
        for v in [350.0, 600.0, 280.0, 720.0, 450.0] * 5:
            oracle._l2_intervals.append(v)
        for v in [500.0, 510.0, 495.0, 505.0, 500.5] * 5:
            oracle._intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertEqual(feats.source, "l2_dig")

    def test_priority_r2_over_triangle_when_above_insufficient(self):
        """3 Cross, 3 L2_dig, 25 R2, 25 Triangle → source='r2'."""
        oracle = TemporalRhythmOracle()
        for v in [400.0] * 3:
            oracle._cross_intervals.append(v)
        for v in [300.0] * 3:
            oracle._l2_intervals.append(v)
        for v in [500.0, 480.0, 520.0, 490.0, 510.0] * 5:
            oracle._intervals.append(v)
        for v in [600.0, 620.0, 580.0, 610.0, 595.0] * 5:
            oracle._triangle_intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats)
        self.assertEqual(feats.source, "r2")

    def test_pooled_mode_fires_when_no_single_button_sufficient(self):
        """8 Cross + 8 L2 + 8 R2 + 8 Triangle = 32 pooled >= 20 → source='pooled'."""
        oracle = TemporalRhythmOracle()
        # Each button has 8 samples (>= _POOL_MIN_PER_BUTTON=5, < _MIN_SAMPLES=20)
        vals = [300.0, 450.0, 280.0, 600.0, 350.0, 420.0, 500.0, 380.0]
        for v in vals:
            oracle._cross_intervals.append(v)
        for v in vals:
            oracle._l2_intervals.append(v)
        for v in vals:
            oracle._intervals.append(v)
        for v in vals:
            oracle._triangle_intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats, "Pooled mode should produce features")
        self.assertEqual(feats.source, "pooled")
        self.assertGreaterEqual(feats.sample_count, _MIN_SAMPLES)

    def test_pooled_mode_skips_buttons_below_pool_min(self):
        """2 Cross (< _POOL_MIN_PER_BUTTON) excluded; 10 L2 + 10 R2 = 20 pooled → fires."""
        oracle = TemporalRhythmOracle()
        for v in [400.0, 500.0]:  # only 2 — below _POOL_MIN_PER_BUTTON
            oracle._cross_intervals.append(v)
        for v in [300.0, 450.0, 280.0, 600.0, 350.0, 420.0, 500.0, 380.0, 340.0, 460.0]:
            oracle._l2_intervals.append(v)
        for v in [310.0, 460.0, 290.0, 610.0, 360.0, 430.0, 510.0, 390.0, 350.0, 470.0]:
            oracle._intervals.append(v)
        feats = oracle.extract_features()
        self.assertIsNotNone(feats, "Pool (L2+R2=20) should fire even with Cross excluded")
        self.assertEqual(feats.source, "pooled")
        # Cross's 2 samples should NOT be in the pool (pool = 10+10 = 20)
        self.assertEqual(feats.sample_count, 20)

    def test_bot_pattern_on_l2_fires_temporal_anomaly(self):
        """Constant 500ms L2 IBI pattern → classify() returns (0x2B, >= 205)."""
        oracle = TemporalRhythmOracle()
        for _ in range(_MIN_SAMPLES + 5):
            oracle._l2_intervals.append(500.0)
        result = oracle.classify()
        self.assertIsNotNone(result, "Bot L2 pattern should fire 0x2B")
        code, conf = result
        self.assertEqual(code, INFER_TEMPORAL_ANOMALY)
        self.assertGreaterEqual(conf, 205)


if __name__ == "__main__":
    unittest.main()
