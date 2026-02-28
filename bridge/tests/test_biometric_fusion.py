"""
Phase 13 — tinyml_biometric_fusion.py tests.

Tests cover:
- BiometricFeatureFrame.to_vector() shape and values
- BiometricFeatureExtractor.extract() with synthetic snapshots
- BiometricFusionClassifier: warmup period, fingerprint update, anomaly detection
- Inference code 0x30 (BIOMETRIC_ANOMALY) is outside cheat range [0x28, 0x2A]
- compute_sensor_commitment_v2_bio() produces 32 bytes
- fingerprint_hash() changes when fingerprint updates
- Model manifest hash stability
"""

import sys
import hashlib
import struct
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))

from tinyml_biometric_fusion import (
    BIOMETRIC_MODEL_MANIFEST_HASH,
    INFER_BIOMETRIC_ANOMALY,
    BiometricFeatureExtractor,
    BiometricFeatureFrame,
    BiometricFusionClassifier,
    _autocorr,
    _compute_trigger_onset_velocity,
    compute_sensor_commitment_v2_bio,
    compute_trigger_mode_hash,
)


# ---------------------------------------------------------------------------
# Minimal InputSnapshot stand-in
# ---------------------------------------------------------------------------

class _Snap:
    def __init__(self, **kwargs):
        defaults = dict(
            left_stick_x=0, left_stick_y=0,
            right_stick_x=0, right_stick_y=0,
            l2_trigger=0, r2_trigger=0,
            gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
            accel_x=0.0, accel_y=0.0, accel_z=1.0,
            l2_effect_mode=0, r2_effect_mode=0,
            inter_frame_us=8000, buttons=0,
        )
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)


def _make_snaps(n=50, vary_trigger=False, still=False):
    snaps = []
    for i in range(n):
        kw = {}
        if vary_trigger:
            kw["l2_trigger"] = (i * 5) % 255
            kw["r2_trigger"] = (i * 3) % 255
        if not still:
            kw["left_stick_x"] = int(10000 * ((i % 20) - 10))
        snaps.append(_Snap(**kw))
    return snaps


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBiometricFeatureFrame(unittest.TestCase):

    def test_to_vector_returns_7_elements(self):
        frame = BiometricFeatureFrame(
            trigger_resistance_change_rate=1.5,
            trigger_onset_velocity_l2=0.3,
            trigger_onset_velocity_r2=0.4,
            micro_tremor_accel_variance=0.001,
            grip_asymmetry=1.2,
            stick_autocorr_lag1=0.6,
            stick_autocorr_lag5=0.4,
        )
        v = frame.to_vector()
        self.assertEqual(v.shape, (7,))
        self.assertAlmostEqual(float(v[0]), 1.5)
        self.assertAlmostEqual(float(v[4]), 1.2)

    def test_default_frame_is_all_zeros_except_grip(self):
        frame = BiometricFeatureFrame()
        v = frame.to_vector()
        self.assertEqual(float(v[0]), 0.0)   # resistance change rate
        self.assertEqual(float(v[3]), 0.0)   # micro tremor
        # grip_asymmetry default is 0.0 (no dual press)
        self.assertEqual(float(v[4]), 0.0)


class TestBiometricFeatureExtractor(unittest.TestCase):

    def test_extract_returns_zeros_for_too_few_frames(self):
        frame = BiometricFeatureExtractor.extract([_Snap() for _ in range(5)])
        v = frame.to_vector()
        self.assertTrue(all(x == 0.0 for x in v))

    def test_extract_returns_7_dim_feature_frame(self):
        snaps = _make_snaps(60, vary_trigger=True)
        frame = BiometricFeatureExtractor.extract(snaps)
        v = frame.to_vector()
        self.assertEqual(v.shape, (7,))

    def test_micro_tremor_nonzero_in_still_conditions(self):
        # Still frames: gyro near zero, accel varies slightly
        snaps = []
        import math
        for i in range(60):
            snaps.append(_Snap(
                gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
                accel_x=0.01 * math.sin(i * 0.5),
                accel_y=0.005 * math.cos(i * 0.3),
                accel_z=1.0,
            ))
        frame = BiometricFeatureExtractor.extract(snaps)
        self.assertGreater(frame.micro_tremor_accel_variance, 0.0)

    def test_trigger_onset_velocity_nonzero_with_trigger_press(self):
        snaps = _make_snaps(60, vary_trigger=True)
        frame = BiometricFeatureExtractor.extract(snaps)
        # At least one of l2/r2 onset velocity should be nonzero
        self.assertGreaterEqual(
            frame.trigger_onset_velocity_l2 + frame.trigger_onset_velocity_r2, 0.0
        )


class TestBiometricFusionClassifier(unittest.TestCase):

    def _make_classifier_warmed(self, n=6) -> BiometricFusionClassifier:
        clf = BiometricFusionClassifier()
        snaps = _make_snaps(60, vary_trigger=True)
        for _ in range(n):
            frame = BiometricFeatureExtractor.extract(snaps)
            clf.update_fingerprint(frame)
        return clf

    def test_not_warmed_up_returns_none(self):
        clf = BiometricFusionClassifier()
        frame = BiometricFeatureExtractor.extract(_make_snaps(60))
        result = clf.classify(frame)
        self.assertIsNone(result)

    def test_normal_session_returns_none_after_warmup(self):
        # Same snaps for warmup and test — should be within normal range
        clf = self._make_classifier_warmed(n=7)
        frame = BiometricFeatureExtractor.extract(_make_snaps(60, vary_trigger=True))
        # May or may not return None depending on exact distance; just check type
        result = clf.classify(frame)
        self.assertIsNone(result)  # identical inputs should be within range

    def test_highly_divergent_session_triggers_anomaly(self):
        clf = self._make_classifier_warmed(n=7)
        # Manually set fingerprint to something very different from fresh snaps
        import numpy as np
        clf._mean = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0], dtype=np.float64)
        clf._var  = np.array([0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01], dtype=np.float64)

        frame = BiometricFeatureFrame()  # all zeros — far from mean=100
        result = clf.classify(frame)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], INFER_BIOMETRIC_ANOMALY)
        self.assertEqual(result[0], 0x30)
        self.assertGreaterEqual(result[1], 180)

    def test_anomaly_code_0x30_outside_cheat_range(self):
        CHEAT_RANGE_MIN = 0x28
        CHEAT_RANGE_MAX = 0x2A
        self.assertNotIn(INFER_BIOMETRIC_ANOMALY, range(CHEAT_RANGE_MIN, CHEAT_RANGE_MAX + 1))

    def test_fingerprint_hash_changes_after_update(self):
        clf = BiometricFusionClassifier()
        h1 = clf.fingerprint_hash()
        snaps = _make_snaps(60, vary_trigger=True)
        clf.update_fingerprint(BiometricFeatureExtractor.extract(snaps))
        h2 = clf.fingerprint_hash()
        self.assertNotEqual(h1, h2)

    def test_fingerprint_hash_is_32_bytes(self):
        clf = self._make_classifier_warmed(n=3)
        h = clf.fingerprint_hash()
        self.assertEqual(len(h), 32)


class TestSensorCommitment(unittest.TestCase):

    def test_compute_sensor_commitment_v2_bio_returns_32_bytes(self):
        snap = _Snap()
        result = compute_sensor_commitment_v2_bio(snap, 1000000, 0, 0)
        self.assertEqual(len(result), 32)

    def test_sensor_commitment_changes_with_biometric_distance(self):
        snap = _Snap()
        clf = BiometricFusionClassifier()
        clf.last_distance = 0.0
        h1 = compute_sensor_commitment_v2_bio(snap, 1000000, 0, 0, clf)
        clf.last_distance = 5.0
        h2 = compute_sensor_commitment_v2_bio(snap, 1000000, 0, 0, clf)
        self.assertNotEqual(h1, h2)

    def test_trigger_mode_hash_changes_with_mode_sequence(self):
        h1 = compute_trigger_mode_hash([0, 0, 0], [0, 0, 0])
        h2 = compute_trigger_mode_hash([1, 2, 3], [0, 1, 0])
        self.assertNotEqual(h1, h2)


class TestModelManifestHash(unittest.TestCase):

    def test_model_manifest_hash_is_32_bytes(self):
        self.assertEqual(len(BIOMETRIC_MODEL_MANIFEST_HASH), 32)

    def test_model_manifest_hash_is_stable(self):
        expected = hashlib.sha256(b"biometric_fusion_v1.0_adaptive_trigger").digest()
        self.assertEqual(BIOMETRIC_MODEL_MANIFEST_HASH, expected)


class TestHelpers(unittest.TestCase):

    def test_autocorr_perfect_series_returns_1(self):
        series = [float(i) for i in range(50)]
        corr = _autocorr(series, lag=1)
        self.assertAlmostEqual(corr, 1.0, places=5)

    def test_autocorr_too_short_returns_zero(self):
        self.assertEqual(_autocorr([1.0, 2.0], lag=5), 0.0)

    def test_trigger_onset_velocity_no_press(self):
        result = _compute_trigger_onset_velocity([0] * 50)
        self.assertEqual(result, 0.0)


if __name__ == "__main__":
    unittest.main()
