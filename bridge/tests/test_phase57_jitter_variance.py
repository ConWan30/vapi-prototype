"""
Phase 57 tests — Press Timing Jitter Variance.

Tests:
1. test_jitter_variance_human_ibi        — Gaussian IBIs → jitter_var in [0.0001, 0.05]
2. test_jitter_variance_macro_zero       — Perfect-interval IBIs → jitter_var < 0.00005
3. test_jitter_variance_insufficient_data — < 4 IBIs → returns 0.0
4. test_jitter_variance_white_noise      — Random IBIs → high jitter_var
5. test_feature_vector_12_elements       — extract() returns 12-element list
"""

import math
import os
import sys
import unittest

# ---------------------------------------------------------------------------
# Path setup — controller dir must be on path for tinyml_biometric_fusion
# ---------------------------------------------------------------------------
_controller_dir = os.path.join(os.path.dirname(__file__), "..", "..", "controller")
if _controller_dir not in sys.path:
    sys.path.insert(0, _controller_dir)


# Minimal InputSnapshot-like stub for extract() calls
class _Snap:
    def __init__(self, **kw):
        self.left_stick_x  = kw.get("left_stick_x", 128)
        self.left_stick_y  = kw.get("left_stick_y", 128)
        self.right_stick_x = kw.get("right_stick_x", 128)
        self.right_stick_y = kw.get("right_stick_y", 128)
        self.l2_trigger    = kw.get("l2_trigger", 0)
        self.r2_trigger    = kw.get("r2_trigger", 0)
        self.gyro_x        = kw.get("gyro_x", 0.0)
        self.gyro_y        = kw.get("gyro_y", 0.0)
        self.gyro_z        = kw.get("gyro_z", 0.0)
        self.accel_x       = kw.get("accel_x", 0.0)
        self.accel_y       = kw.get("accel_y", 0.0)
        self.accel_z       = kw.get("accel_z", 1.0)
        self.l2_effect_mode = kw.get("l2_effect_mode", 0)
        self.r2_effect_mode = kw.get("r2_effect_mode", 0)
        self.inter_frame_us = kw.get("inter_frame_us", 1000)
        self.touch_active  = kw.get("touch_active", False)
        self.touch0_x      = kw.get("touch0_x", 0)
        self.touch0_y      = kw.get("touch0_y", 0)
        self.buttons_0     = kw.get("buttons_0", 0)
        self.buttons_1     = kw.get("buttons_1", 0)
        self.timestamp_ms  = kw.get("timestamp_ms", 0)
        self.battery_mv    = kw.get("battery_mv", 4000)
        self.buttons       = kw.get("buttons", 0)
        self.bt_seq_byte   = kw.get("bt_seq_byte", 0)


class TestJitterVarianceHumanIBI(unittest.TestCase):
    """test_jitter_variance_human_ibi — Gaussian IBIs produce jitter in human range"""

    def test_jitter_variance_human_ibi(self):
        from tinyml_biometric_fusion import BiometricFeatureExtractor

        # Simulate human IBIs: ~500ms mean, Gaussian noise std=50ms
        import random
        rng = random.Random(42)
        ibis = [500.0 + rng.gauss(0, 50) for _ in range(20)]
        ibis = [max(50.0, iv) for iv in ibis]  # clamp negatives

        result = BiometricFeatureExtractor._press_timing_jitter_variance(ibis, min_samples=4)

        self.assertGreater(result, 0.0001,
                           f"Expected jitter_var > 0.0001 for human IBIs, got {result}")
        self.assertLess(result, 0.5,
                        f"Expected jitter_var < 0.5 for human IBIs, got {result}")


class TestJitterVarianceMacroZero(unittest.TestCase):
    """test_jitter_variance_macro_zero — Perfectly equal IBIs → near-zero variance"""

    def test_jitter_variance_macro_zero(self):
        from tinyml_biometric_fusion import BiometricFeatureExtractor

        # Perfect macro timing: every press exactly 500ms
        ibis = [500.0] * 20

        result = BiometricFeatureExtractor._press_timing_jitter_variance(ibis, min_samples=4)

        self.assertLess(result, 1e-9,
                        f"Expected jitter_var ~0 for macro IBIs, got {result}")


class TestJitterVarianceInsufficientData(unittest.TestCase):
    """test_jitter_variance_insufficient_data — < 4 IBIs → 0.0"""

    def test_jitter_variance_insufficient_data(self):
        from tinyml_biometric_fusion import BiometricFeatureExtractor

        # Only 3 intervals
        result3 = BiometricFeatureExtractor._press_timing_jitter_variance(
            [100.0, 200.0, 300.0], min_samples=4
        )
        self.assertEqual(result3, 0.0, "Expected 0.0 with 3 IBIs (< min_samples=4)")

        # Empty list
        result_empty = BiometricFeatureExtractor._press_timing_jitter_variance(
            [], min_samples=4
        )
        self.assertEqual(result_empty, 0.0, "Expected 0.0 with empty list")

        # None
        result_none = BiometricFeatureExtractor._press_timing_jitter_variance(
            None, min_samples=4
        )
        self.assertEqual(result_none, 0.0, "Expected 0.0 with None")


class TestJitterVarianceWhiteNoise(unittest.TestCase):
    """test_jitter_variance_white_noise — random IBIs produce noticeably higher jitter_var"""

    def test_jitter_variance_white_noise(self):
        from tinyml_biometric_fusion import BiometricFeatureExtractor

        import random
        rng = random.Random(99)
        # Highly variable IBIs: uniform [50, 1000] ms
        ibis = [rng.uniform(50, 1000) for _ in range(30)]

        result = BiometricFeatureExtractor._press_timing_jitter_variance(ibis, min_samples=4)

        # High variance signal: should be >> 0.01
        self.assertGreater(result, 0.01,
                           f"Expected high jitter_var for white-noise IBIs, got {result}")


class TestFeatureVector12Elements(unittest.TestCase):
    """test_feature_vector_12_elements — extract() returns 12-element list"""

    def test_feature_vector_12_elements(self):
        from tinyml_biometric_fusion import BiometricFeatureExtractor, BiometricFeatureFrame

        extractor = BiometricFeatureExtractor()

        # Build 30 minimal snapshots
        snaps = []
        for i in range(30):
            snaps.append(_Snap(
                timestamp_ms=i * 1000,
                accel_x=0.1, accel_y=0.0, accel_z=9.8,
                gyro_x=1.0, gyro_y=0.5, gyro_z=0.2,
                left_stick_x=128 + (i % 5),
                right_stick_x=128,
                l2_trigger=10, r2_trigger=20,
                inter_frame_us=1000,
            ))

        frame = extractor.extract(snaps)

        # Must be a BiometricFeatureFrame
        self.assertIsInstance(frame, BiometricFeatureFrame)

        # to_vector() must return 12 elements
        vec = frame.to_vector()
        self.assertEqual(len(vec), 12,
                         f"Expected 12-element feature vector, got {len(vec)}")

        # press_timing_jitter_variance is the 12th element (index 11)
        self.assertIsNotNone(frame.press_timing_jitter_variance)
        self.assertAlmostEqual(vec[11], frame.press_timing_jitter_variance, places=6)


if __name__ == "__main__":
    unittest.main()
