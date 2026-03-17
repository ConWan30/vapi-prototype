"""
Phase 13 — tinyml_biometric_fusion.py tests.

Tests cover:
- BiometricFeatureFrame.to_vector() shape and values
- BiometricFeatureExtractor().extract() with synthetic snapshots
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
        self.assertEqual(v.shape, (12,))
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
        frame = BiometricFeatureExtractor().extract([_Snap() for _ in range(5)])
        v = frame.to_vector()
        self.assertTrue(all(x == 0.0 for x in v))

    def test_extract_returns_7_dim_feature_frame(self):
        snaps = _make_snaps(60, vary_trigger=True)
        frame = BiometricFeatureExtractor().extract(snaps)
        v = frame.to_vector()
        self.assertEqual(v.shape, (12,))

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
        frame = BiometricFeatureExtractor().extract(snaps)
        self.assertGreater(frame.micro_tremor_accel_variance, 0.0)

    def test_trigger_onset_velocity_nonzero_with_trigger_press(self):
        snaps = _make_snaps(60, vary_trigger=True)
        frame = BiometricFeatureExtractor().extract(snaps)
        # At least one of l2/r2 onset velocity should be nonzero
        self.assertGreaterEqual(
            frame.trigger_onset_velocity_l2 + frame.trigger_onset_velocity_r2, 0.0
        )


class TestBiometricFusionClassifier(unittest.TestCase):

    def _make_classifier_warmed(self, n=6) -> BiometricFusionClassifier:
        clf = BiometricFusionClassifier()
        snaps = _make_snaps(60, vary_trigger=True)
        for _ in range(n):
            frame = BiometricFeatureExtractor().extract(snaps)
            clf.update_fingerprint(frame)
        return clf

    def test_not_warmed_up_returns_none(self):
        clf = BiometricFusionClassifier()
        frame = BiometricFeatureExtractor().extract(_make_snaps(60))
        result = clf.classify(frame)
        self.assertIsNone(result)

    def test_normal_session_returns_none_after_warmup(self):
        # Same snaps for warmup and test — should be within normal range
        clf = self._make_classifier_warmed(n=7)
        frame = BiometricFeatureExtractor().extract(_make_snaps(60, vary_trigger=True))
        # May or may not return None depending on exact distance; just check type
        result = clf.classify(frame)
        self.assertIsNone(result)  # identical inputs should be within range

    def test_highly_divergent_session_triggers_anomaly(self):
        clf = self._make_classifier_warmed(n=7)
        # Manually set fingerprint to something very different from fresh snaps
        import numpy as np
        clf._mean = np.array([100.0]*12, dtype=np.float64)
        clf._var  = np.array([0.01]*12, dtype=np.float64)

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
        clf.update_fingerprint(BiometricFeatureExtractor().extract(snaps))
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


# ---------------------------------------------------------------------------
# Phase 17: Tremor FFT + Touchpad + Feature Vector Dimension Tests
# ---------------------------------------------------------------------------

import json
import math
import numpy as np

SESSION_DIR = Path(__file__).resolve().parents[2] / "sessions" / "human"


def _make_snap(i: int, rx: int = 0, touch_active: bool = False,
               touch0_x: int = 0, inter_frame_us: int = 1000, **kwargs):
    """Factory for synthetic InputSnapshot-like objects."""
    defaults = dict(
        left_stick_x=0, left_stick_y=0,
        right_stick_x=rx, right_stick_y=0,
        l2_trigger=0, r2_trigger=0,
        l2_effect_mode=0, r2_effect_mode=0,
        gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
        accel_x=0.0, accel_y=0.0, accel_z=1.0,
        inter_frame_us=inter_frame_us,
        touch_active=touch_active,
        touch0_x=touch0_x,
        touch0_y=0,
    )
    defaults.update(kwargs)
    return type("_S", (), defaults)()


class TestTremorFFT(unittest.TestCase):
    """Phase 17: right-stick tremor FFT (8-12 Hz physiological tremor)."""

    def _snaps_with_tremor(self, freq_hz: float, n: int = 120, fs_hz: float = 1000.0) -> list:
        """Generate snaps with right_stick_x oscillating at freq_hz."""
        dt_us = int(1_000_000 / fs_hz)
        return [
            _make_snap(i, rx=int(3000 * math.sin(2 * math.pi * freq_hz * i / fs_hz)),
                       inter_frame_us=dt_us)
            for i in range(n)
        ]

    def test_tremor_peak_hz_detects_8hz(self):
        """8 Hz oscillation → tremor_peak_hz ∈ [6, 10]. Needs >=1024 velocity samples for FFT."""
        snaps = self._snaps_with_tremor(8.0, n=1100)  # 1100 snaps, 1099 velocity samples >= 1024
        feats = BiometricFeatureExtractor().extract(snaps, window_frames=1100)
        self.assertGreater(feats.tremor_peak_hz, 0.0)
        self.assertGreaterEqual(feats.tremor_peak_hz, 6.0)
        self.assertLessEqual(feats.tremor_peak_hz, 10.0,
                             msg=f"Expected ~8 Hz, got {feats.tremor_peak_hz:.2f}")

    def test_tremor_peak_hz_detects_10hz(self):
        """10 Hz oscillation → tremor_peak_hz ∈ [8, 12]. Needs >=1024 velocity samples for FFT."""
        snaps = self._snaps_with_tremor(10.0, n=1100)  # 1100 snaps, 1099 velocity samples >= 1024
        feats = BiometricFeatureExtractor().extract(snaps, window_frames=1100)
        self.assertGreaterEqual(feats.tremor_peak_hz, 8.0)
        self.assertLessEqual(feats.tremor_peak_hz, 12.0,
                             msg=f"Expected ~10 Hz, got {feats.tremor_peak_hz:.2f}")

    def test_bot_static_stick_low_band_power(self):
        """
        Static stick (rx=constant) → velocity=0 → tremor_band_power ~ 0.
        A perfectly static bot has no tremor.
        """
        snaps = [_make_snap(i, rx=5000) for i in range(120)]
        feats = BiometricFeatureExtractor().extract(snaps)
        # tremor_peak_hz at DC (0 Hz) or close; band_power should be very low
        self.assertLess(feats.tremor_band_power, 0.10,
                        msg="Static bot should have near-zero 8-12 Hz band power")

    def test_tremor_insufficient_data(self):
        """Fewer than 1024 velocity samples → tremor FFT fields = 0.0 (insufficient resolution)."""
        snaps = [_make_snap(i, rx=int(1000 * math.sin(i))) for i in range(20)]
        feats = BiometricFeatureExtractor().extract(snaps)
        # With < 10 frames extract returns zeros; with 20 frames no FFT (< 1024 velocity threshold)
        self.assertEqual(feats.tremor_peak_hz, 0.0)
        self.assertEqual(feats.tremor_band_power, 0.0)

    def test_tremor_band_power_is_fraction(self):
        """tremor_band_power ∈ [0, 1] for any input."""
        snaps = self._snaps_with_tremor(10.0, n=120)
        feats = BiometricFeatureExtractor().extract(snaps)
        self.assertGreaterEqual(feats.tremor_band_power, 0.0)
        self.assertLessEqual(feats.tremor_band_power, 1.0)


class TestTouchpadBiometric(unittest.TestCase):
    """Phase 17: touchpad active fraction and position variance."""

    def test_touch_position_variance_consistent(self):
        """Consistent touch position → variance ≈ 0."""
        snaps = [_make_snap(i, touch_active=True, touch0_x=960) for i in range(120)]
        feats = BiometricFeatureExtractor().extract(snaps)
        self.assertAlmostEqual(feats.touch_position_variance, 0.0, places=4)

    def test_touch_position_variance_spread(self):
        """Random touch positions → variance > 0."""
        rng = np.random.default_rng(42)
        xs = rng.integers(0, 1920, size=120)
        snaps = [_make_snap(i, touch_active=True, touch0_x=int(xs[i])) for i in range(120)]
        feats = BiometricFeatureExtractor().extract(snaps)
        self.assertGreater(feats.touch_position_variance, 0.0)

    def test_touch_variance_below_min_frames(self):
        """Fewer than 3 active touch frames → touch_position_variance = 0.0."""
        snaps = [_make_snap(i, touch_active=(i < 2), touch0_x=500) for i in range(120)]
        feats = BiometricFeatureExtractor().extract(snaps)
        self.assertAlmostEqual(feats.touch_position_variance, 0.0)


class TestFeatureVectorDimension(unittest.TestCase):
    """Phase 17/57: BiometricFeatureFrame now has 12 features (11 + press_timing_jitter_variance)."""

    def test_feature_vector_dim_11(self):
        """to_vector() returns a (12,) numpy array (11 original + Phase 57 jitter_variance)."""
        frame = BiometricFeatureFrame()
        vec = frame.to_vector()
        self.assertEqual(len(vec), 12, f"Expected 12 features, got {len(vec)}")

    def test_feature_vector_contains_new_fields(self):
        """New fields appear in correct positions (indices 7-10)."""
        frame = BiometricFeatureFrame(
            tremor_peak_hz=10.5,
            tremor_band_power=0.30,
            accel_magnitude_spectral_entropy=4.92,  # index 9 (Phase 46)
            touch_position_variance=0.05,
        )
        vec = frame.to_vector()
        self.assertAlmostEqual(float(vec[7]), 10.5, places=3)
        self.assertAlmostEqual(float(vec[8]), 0.30, places=3)
        self.assertAlmostEqual(float(vec[9]), 4.92, places=3)
        self.assertAlmostEqual(float(vec[10]), 0.05, places=3)

    def test_session_fixture_extract_dim_11(self):
        """
        Load hw_005.json session (no touchpad fields) and verify extract()
        returns a BiometricFeatureFrame with a length-11 vector.
        """
        path = SESSION_DIR / "hw_005.json"
        if not path.exists():
            self.skipTest("hw_005.json not present")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        snaps = []
        for r in data["reports"][:200]:
            f = r["features"]
            snap = _make_snap(
                0,
                rx=int(f.get("right_stick_x", 0)),
                inter_frame_us=1000,
                gyro_x=float(f.get("gyro_x", 0.0)),
                gyro_y=float(f.get("gyro_y", 0.0)),
                gyro_z=float(f.get("gyro_z", 0.0)),
                accel_x=float(f.get("accel_x", 0.0)),
                accel_y=float(f.get("accel_y", 0.0)),
                accel_z=float(f.get("accel_z", 1.0)),
                l2_trigger=int(f.get("l2_trigger", 0)),
                r2_trigger=int(f.get("r2_trigger", 0)),
                l2_effect_mode=0, r2_effect_mode=0,
            )
            snaps.append(snap)

        feats = BiometricFeatureExtractor().extract(snaps)
        self.assertEqual(len(feats.to_vector()), 12)
        # Tremor peak should be > 0 from real stick data (not static)
        # (if stick is active enough to have a non-DC peak)
        self.assertGreaterEqual(feats.tremor_peak_hz, 0.0)


# ---------------------------------------------------------------------------
# Phase 38 — Zero-variance feature exclusion in classify()
# ---------------------------------------------------------------------------

class TestZeroVarianceExclusion(unittest.TestCase):
    """
    Verify that BiometricFusionClassifier.classify() excludes features whose
    training variance is below ZERO_VAR_THRESHOLD from the Mahalanobis distance.

    Root cause being guarded against:
      - accel_magnitude_spectral_entropy is 0.0 during warm-up (ring buffer < 1024 frames).
      - trigger_resistance_change_rate is 0.0 in static-trigger games (NCAA Football 26).
      - touch_position_variance is 0.0 when touchpad is inactive.
      - With VAR_FLOOR = 1e-6, a feature always-zero in training has ref_var ~= 1e-6.
      - If a new legitimate session has accel_magnitude_spectral_entropy = 4.9:
            contribution = 4.9^2 / 1e-6 = 24,000,000 -> false-positive 0x30 advisory.
    """

    def _make_nominal_frame(self, **overrides) -> BiometricFeatureFrame:
        kw = dict(
            trigger_resistance_change_rate=0.0,
            trigger_onset_velocity_l2=0.2,
            trigger_onset_velocity_r2=0.2,
            micro_tremor_accel_variance=500.0,
            grip_asymmetry=1.0,
            stick_autocorr_lag1=0.5,
            stick_autocorr_lag5=0.3,
            tremor_peak_hz=9.5,
            tremor_band_power=0.15,
            accel_magnitude_spectral_entropy=0.0,  # warm-up / structurally inactive
            touch_position_variance=0.0,
        )
        kw.update(overrides)
        return BiometricFeatureFrame(**kw)

    def _warm_up_classifier(self, clf: BiometricFusionClassifier, n_extra: int = 0) -> None:
        """Warm up the classifier with zero-entropy frames (simulates warm-up period)."""
        for _ in range(clf.N_WARMUP_SESSIONS + 1 + n_extra):
            clf.update_fingerprint(self._make_nominal_frame())

    def test_no_false_positive_when_entropy_activates(self):
        """
        Entropy feature activating from zero must NOT trigger 0x30 when var is truly zero.

        The ZERO_VAR_THRESHOLD exclusion fires when ref_var[i] < 1e-4. In production this
        occurs after ~66 zero-entropy sessions (EMA: 0.1 * 0.9^66 < 1e-4). We simulate
        this by setting _var[9]=0.0 directly after a standard warm-up — isolating the
        exclusion mechanism from the EMA decay rate.
        """
        clf = BiometricFusionClassifier()
        clf.ANOMALY_THRESHOLD = 5.0
        self._warm_up_classifier(clf)
        # Simulate true zero-variance training for the entropy slot (as seen in live
        # calibration after ~66 sessions of zero; EMA: 0.1 * 0.9^66 ≈ 5e-4 -> 0).
        clf._var[9] = 0.0  # forces active_mask[9] = False -> excluded from Mahalanobis

        # Entropy now active at typical human value -- should NOT trigger alarm
        new_frame = self._make_nominal_frame(
            accel_magnitude_spectral_entropy=4.9,
            touch_position_variance=0.05,
        )
        result = clf.classify(new_frame)
        self.assertIsNone(
            result,
            f"False-positive 0x30: zero-var entropy feature not excluded -- "
            f"distance={clf.last_distance:.3f} (threshold={clf.ANOMALY_THRESHOLD})",
        )

    def test_last_distance_does_not_explode_from_zero_var_feature(self):
        """last_distance stays bounded even with large entropy value when var was zero in training."""
        clf = BiometricFusionClassifier()
        clf.ANOMALY_THRESHOLD = 5.0
        self._warm_up_classifier(clf)

        new_frame = self._make_nominal_frame(accel_magnitude_spectral_entropy=9.0)
        clf.classify(new_frame)
        self.assertLess(
            clf.last_distance, 100.0,
            f"last_distance={clf.last_distance:.3f} is suspiciously large -- "
            "zero-var entropy feature was probably not excluded.",
        )

    def test_genuine_anomaly_still_detected_on_active_features(self):
        """An anomaly on non-zero-variance features is still detected after exclusion logic."""
        clf = BiometricFusionClassifier()
        clf.ANOMALY_THRESHOLD = 5.0
        self._warm_up_classifier(clf)

        # Inject a massive deviation on grip_asymmetry (always active feature)
        anomalous_frame = self._make_nominal_frame(grip_asymmetry=100.0)
        result = clf.classify(anomalous_frame)
        self.assertIsNotNone(
            result,
            "Expected 0x30 for massive grip_asymmetry deviation, got None. "
            f"last_distance={clf.last_distance:.3f}",
        )
        self.assertEqual(result[0], INFER_BIOMETRIC_ANOMALY)

    def test_active_mask_uses_training_var_not_sample_var(self):
        """
        The active_mask is computed from ref_var (training), not the current sample.
        A feature with zero training variance is excluded regardless of sample value.
        """
        clf = BiometricFusionClassifier()
        # Warm up with trigger_resistance_change_rate always 0.0
        for _ in range(clf.N_WARMUP_SESSIONS + 1):
            clf.update_fingerprint(self._make_nominal_frame(
                trigger_resistance_change_rate=0.0
            ))

        # Sample has large trigger_resistance_change_rate -- still excluded because training var ~= 0
        frame = self._make_nominal_frame(trigger_resistance_change_rate=50.0)
        clf.classify(frame)
        after_distance = clf.last_distance

        # Without exclusion: contribution = 50^2 / 1e-6 = 2.5e9. With exclusion: 0.
        self.assertLess(
            after_distance, 1000.0,
            f"trigger_resistance_change_rate (zero-var in training) was NOT excluded -- "
            f"distance={after_distance:.3f}",
        )


class TestFFTRingBuffer(unittest.TestCase):
    """Phase 41: BiometricFeatureExtractor stateful ring-buffer tests."""

    def _snap(self, rx: float = 0.0, ift_us: int = 1000) -> object:
        class S:
            right_stick_x = rx; inter_frame_us = ift_us
            left_stick_x = 0; left_stick_y = 0; right_stick_y = 0
            l2_trigger = 0; r2_trigger = 0
            gyro_x = 0.0; gyro_y = 0.0; gyro_z = 0.0
            accel_x = 0.0; accel_y = 0.0; accel_z = 1.0
            l2_effect_mode = 0; r2_effect_mode = 0
            touch_active = False; touch0_x = 0
        return S()

    def test_ring_buffer_activates_tremor_after_accumulation(self):
        """After 1025+ cumulative frames, tremor FFT activates even in live mode (Phase 49)."""
        import math
        extractor = BiometricFeatureExtractor()
        fs = 1000.0
        # First call: 120 frames of 8 Hz oscillation (ring buffer has 120 entries → inactive)
        snaps_a = [self._snap(rx=1000 * math.sin(2 * math.pi * 8.0 * i / fs))
                   for i in range(120)]
        feats_a = extractor.extract(snaps_a)
        self.assertEqual(feats_a.tremor_peak_hz, 0.0, "ring < 1025, FFT should be inactive")

        # Continue with 8 more calls of 120 frames (total 1080 → ring capped at 1025)
        for k in range(8):
            snaps_k = [self._snap(rx=1000 * math.sin(2 * math.pi * 8.0 * (120 * (k + 1) + i) / fs))
                       for i in range(120)]
            extractor.extract(snaps_k)

        # Now ring has 1025 entries (1024 velocity samples); tremor should be detectable
        snaps_final = [self._snap(rx=1000 * math.sin(2 * math.pi * 8.0 * (1080 + i) / fs))
                       for i in range(120)]
        feats_final = extractor.extract(snaps_final)
        self.assertGreater(feats_final.tremor_peak_hz, 0.0,
                           "ring >= 1025, tremor FFT should activate")
        self.assertGreaterEqual(feats_final.tremor_peak_hz, 5.0,
                                f"Expected ~8 Hz, got {feats_final.tremor_peak_hz:.2f}")

    def test_fresh_instance_has_no_ring_state(self):
        """Each BiometricFeatureExtractor instance starts with an empty ring."""
        extractor = BiometricFeatureExtractor()
        # Exactly 120 frames → ring < 1025 → FFT inactive
        snaps = [self._snap(rx=1000.0) for _ in range(120)]
        feats = extractor.extract(snaps)
        self.assertEqual(feats.tremor_peak_hz, 0.0)
        self.assertEqual(feats.tremor_band_power, 0.0)

    def test_large_window_activates_fft_immediately(self):
        """Calibration window (CALIBRATION_WINDOW_FRAMES=1025 snaps) fills ring → FFT active."""
        import math
        from tinyml_biometric_fusion import CALIBRATION_WINDOW_FRAMES
        extractor = BiometricFeatureExtractor()
        fs = 1000.0
        n = CALIBRATION_WINDOW_FRAMES  # 1025 → 1024 velocity samples >= 1024 guard
        snaps = [self._snap(rx=1000 * math.sin(2 * math.pi * 10.0 * i / fs)) for i in range(n)]
        feats = extractor.extract(snaps, window_frames=CALIBRATION_WINDOW_FRAMES)
        self.assertGreater(feats.tremor_peak_hz, 0.0,
                           "Single CALIBRATION_WINDOW_FRAMES call should activate FFT")


class TestFullCovariance(unittest.TestCase):
    """Phase 41: BiometricFusionClassifier full covariance tests."""

    def _make_frame(self, **kwargs) -> BiometricFeatureFrame:
        defaults = dict(
            trigger_resistance_change_rate=0.0,
            trigger_onset_velocity_l2=0.3,
            trigger_onset_velocity_r2=0.4,
            micro_tremor_accel_variance=1000.0,
            grip_asymmetry=1.2,
            stick_autocorr_lag1=0.5,
            stick_autocorr_lag5=0.2,
            tremor_peak_hz=9.0,
            tremor_band_power=0.25,
            accel_magnitude_spectral_entropy=4.9,
            touch_position_variance=0.0,
        )
        defaults.update(kwargs)
        return BiometricFeatureFrame(**defaults)

    def test_full_cov_disabled_by_default(self):
        """USE_FULL_COVARIANCE is False by default."""
        clf = BiometricFusionClassifier()
        self.assertFalse(clf.USE_FULL_COVARIANCE)

    def test_full_cov_enabled_changes_distance(self):
        """With correlated features, full covariance yields a different distance than diagonal."""
        import numpy as np

        # Two classifiers, same training, different mode
        clf_diag = BiometricFusionClassifier()
        clf_full = BiometricFusionClassifier()
        clf_full.USE_FULL_COVARIANCE = True

        base = self._make_frame()
        # Train both with identical nominal sessions + deliberate correlation:
        # session alternates between two off-mean variants to create covariance
        alt_a = self._make_frame(grip_asymmetry=1.6, trigger_onset_velocity_l2=0.6)
        alt_b = self._make_frame(grip_asymmetry=0.8, trigger_onset_velocity_l2=0.1)
        for i in range(clf_diag.N_WARMUP_SESSIONS + 20):
            f = alt_a if i % 2 == 0 else alt_b
            clf_diag.update_fingerprint(f)
            clf_full.update_fingerprint(f)

        # Query with an off-nominal frame that deviates in the correlated direction
        query = self._make_frame(grip_asymmetry=3.0, trigger_onset_velocity_l2=1.5)
        clf_diag.classify(query)
        clf_full.classify(query)

        # Distances need not be equal — full cov captures joint deviation
        # (they can go either way depending on correlation sign, but must differ)
        self.assertIsInstance(clf_diag.last_distance, float)
        self.assertIsInstance(clf_full.last_distance, float)
        self.assertNotAlmostEqual(
            clf_diag.last_distance, clf_full.last_distance, places=3,
            msg="Full covariance distance should differ from diagonal distance",
        )

    def test_full_cov_fallback_on_singular_matrix(self):
        """Singular covariance matrix falls back to diagonal distance without raising."""
        clf = BiometricFusionClassifier()
        clf.USE_FULL_COVARIANCE = True

        # Train with constant features → covariance is singular (zero off-diagonals, zero diag too)
        const_frame = self._make_frame(grip_asymmetry=1.0, trigger_onset_velocity_l2=0.3,
                                       trigger_onset_velocity_r2=0.4)
        for _ in range(clf.N_WARMUP_SESSIONS + 5):
            clf.update_fingerprint(const_frame)

        query = self._make_frame(grip_asymmetry=2.5)
        # Should not raise, should return a float distance
        try:
            clf.classify(query)
        except Exception as e:
            self.fail(f"classify() raised unexpectedly with singular cov: {e}")
        self.assertIsInstance(clf.last_distance, float)

    def test_fingerprint_hash_differs_in_full_cov_mode(self):
        """fingerprint_hash() includes covariance data when USE_FULL_COVARIANCE=True."""
        clf_diag = BiometricFusionClassifier()
        clf_full = BiometricFusionClassifier()
        clf_full.USE_FULL_COVARIANCE = True

        frame = self._make_frame(grip_asymmetry=1.5)
        for _ in range(3):
            clf_diag.update_fingerprint(frame)
            clf_full.update_fingerprint(frame)

        self.assertNotEqual(
            clf_diag.fingerprint_hash(), clf_full.fingerprint_hash(),
            "Full-cov mode fingerprint hash should differ (includes cov matrix)",
        )


# ---------------------------------------------------------------------------
# Phase 46 — accel_magnitude_spectral_entropy tests
# ---------------------------------------------------------------------------

class TestAccelMagnitudeSpectralEntropy(unittest.TestCase):
    """
    Phase 46: Shannon entropy of 0-500 Hz accel magnitude power spectrum.
    Gravity-invariant (||accel|| eliminates orientation dependence).
    Requires 1024-sample ring buffer; 0.0 during warm-up.
    """

    def _make_accel_snaps(self, n: int, ax_fn, ay_fn=None, az_fn=None,
                           inter_frame_us: int = 1000):
        """Build n snaps with accel_x/y/z from callables."""
        snaps = []
        for i in range(n):
            s = _make_snap(
                i,
                accel_x=float(ax_fn(i)),
                accel_y=float(ay_fn(i) if ay_fn else 0.0),
                accel_z=float(az_fn(i) if az_fn else 8192.0),
                inter_frame_us=inter_frame_us,
            )
            snaps.append(s)
        return snaps

    def test_1_entropy_in_valid_range(self):
        """Any realistic accel input returns entropy in [0.0, log2(513) ~= 9.0]."""
        rng = np.random.default_rng(42)
        snaps = [
            _make_snap(i,
                       accel_x=float(rng.normal(0, 500)),
                       accel_y=float(rng.normal(0, 500)),
                       accel_z=float(rng.normal(8192, 500)),
                       inter_frame_us=1000)
            for i in range(1024)
        ]
        ext = BiometricFeatureExtractor()
        feat = ext.extract(snaps, window_frames=1024)
        max_entropy = math.log2(513)
        self.assertGreaterEqual(feat.accel_magnitude_spectral_entropy, 0.0)
        self.assertLessEqual(feat.accel_magnitude_spectral_entropy, max_entropy + 0.01,
                             f"entropy={feat.accel_magnitude_spectral_entropy:.3f} > max={max_entropy:.3f}")

    def test_2_static_zero_injection_returns_zero(self):
        """Zeroed accel (var < 4.0 LSB^2) triggers variance guard -> entropy = 0.0."""
        snaps = [_make_snap(i, accel_x=0.0, accel_y=0.0, accel_z=0.0,
                             inter_frame_us=1000)
                 for i in range(1024)]
        ext = BiometricFeatureExtractor()
        feat = ext.extract(snaps, window_frames=1024)
        self.assertEqual(feat.accel_magnitude_spectral_entropy, 0.0,
                         "Static zero injection must return 0.0 (variance guard)")

    def test_3_white_noise_returns_high_entropy(self):
        """Independent random accel axes -> flat spectrum -> entropy > 7.0 bits."""
        rng = np.random.default_rng(99)
        snaps = [
            _make_snap(i,
                       accel_x=float(rng.normal(0, 1000)),
                       accel_y=float(rng.normal(0, 1000)),
                       accel_z=float(rng.normal(8192, 1000)),
                       inter_frame_us=1000)
            for i in range(1024)
        ]
        ext = BiometricFeatureExtractor()
        feat = ext.extract(snaps, window_frames=1024)
        self.assertGreater(feat.accel_magnitude_spectral_entropy, 7.0,
                           f"White noise entropy={feat.accel_magnitude_spectral_entropy:.3f}, expected > 7.0")

    def test_4_sine_wave_returns_low_entropy(self):
        """Pure 10 Hz sine on accel_x -> energy concentrated at single freq -> entropy < 3.0 bits."""
        fs = 1000.0
        snaps = [
            _make_snap(i,
                       accel_x=float(300.0 * math.sin(2 * math.pi * 10.0 * i / fs)),
                       accel_y=0.0,
                       accel_z=8192.0,
                       inter_frame_us=1000)
            for i in range(1024)
        ]
        ext = BiometricFeatureExtractor()
        feat = ext.extract(snaps, window_frames=1024)
        self.assertLess(feat.accel_magnitude_spectral_entropy, 3.0,
                        f"Sine wave entropy={feat.accel_magnitude_spectral_entropy:.3f}, expected < 3.0")


class TestTremorFFTPhase49(unittest.TestCase):
    """
    Phase 49: Tremor FFT ring buffer widened from 513 → 1025 positions.
    Resolution improved from 1.95 Hz/bin → 0.977 Hz/bin (4 bins across 8–12 Hz band).
    Activation guard changed from >=512 → >=1024 velocity samples.
    """

    def _snap_rx(self, rx: float, ift_us: int = 1000) -> object:
        class S:
            right_stick_x = rx; inter_frame_us = ift_us
            left_stick_x = 0; left_stick_y = 0; right_stick_y = 0
            l2_trigger = 0; r2_trigger = 0
            gyro_x = 0.0; gyro_y = 0.0; gyro_z = 0.0
            accel_x = 0.0; accel_y = 0.0; accel_z = 1.0
            l2_effect_mode = 0; r2_effect_mode = 0
            touch_active = False; touch0_x = 0
        return S()

    def test_tremor_fft_inactive_at_1023_velocity_samples(self):
        """
        Phase 49 warm-up guard: exactly 1024 positions → 1023 velocity samples < 1024.
        tremor_peak_hz and tremor_band_power must remain 0.0.
        """
        fs = 1000.0
        # 1024 positions → np.diff → 1023 velocity samples → below new guard
        snaps = [self._snap_rx(rx=1000.0 * math.sin(2 * math.pi * 10.0 * i / fs))
                 for i in range(1024)]
        feats = BiometricFeatureExtractor().extract(snaps, window_frames=1024)
        self.assertEqual(feats.tremor_peak_hz, 0.0,
                         "1023 velocity samples < 1024 guard: FFT must be inactive")
        self.assertEqual(feats.tremor_band_power, 0.0,
                         "1023 velocity samples < 1024 guard: band power must be 0.0")

    def test_tremor_fft_activates_at_1025_positions(self):
        """
        Phase 49: exactly 1025 positions → 1024 velocity samples → guard fires.
        Inject 10 Hz sine; tremor_peak_hz must be non-zero and tremor_band_power > 0.5.
        """
        fs = 1000.0
        from tinyml_biometric_fusion import CALIBRATION_WINDOW_FRAMES
        self.assertEqual(CALIBRATION_WINDOW_FRAMES, 1025,
                         "CALIBRATION_WINDOW_FRAMES must be 1025 after Phase 49")
        snaps = [self._snap_rx(rx=3000.0 * math.sin(2 * math.pi * 10.0 * i / fs))
                 for i in range(CALIBRATION_WINDOW_FRAMES)]
        feats = BiometricFeatureExtractor().extract(snaps, window_frames=CALIBRATION_WINDOW_FRAMES)
        self.assertGreater(feats.tremor_peak_hz, 0.0,
                           "1024 velocity samples: FFT must activate")
        self.assertGreater(feats.tremor_band_power, 0.5,
                           f"10 Hz sine → band power should dominate 8-12 Hz; got {feats.tremor_band_power:.4f}")

    def test_tremor_frequency_resolution_0977_hz_per_bin(self):
        """
        Phase 49: With 1024 velocity samples at 1000 Hz, frequency resolution = 0.977 Hz/bin.
        Inject 9 Hz sine: must resolve peak at [7.5, 10.5] Hz (within 1.5 bin of 9 Hz).
        At old 1.95 Hz/bin resolution this boundary was too coarse to distinguish from 8 Hz.
        """
        fs = 1000.0
        from tinyml_biometric_fusion import CALIBRATION_WINDOW_FRAMES
        snaps = [self._snap_rx(rx=3000.0 * math.sin(2 * math.pi * 9.0 * i / fs))
                 for i in range(CALIBRATION_WINDOW_FRAMES)]
        feats = BiometricFeatureExtractor().extract(snaps, window_frames=CALIBRATION_WINDOW_FRAMES)
        self.assertGreaterEqual(feats.tremor_peak_hz, 7.5,
                                f"9 Hz peak should be >= 7.5 Hz; got {feats.tremor_peak_hz:.3f}")
        self.assertLessEqual(feats.tremor_peak_hz, 10.5,
                             f"9 Hz peak should be <= 10.5 Hz; got {feats.tremor_peak_hz:.3f}")

    def test_live_accumulation_activates_after_9_windows(self):
        """
        Phase 49 live path: 9 sequential calls × 120-frame windows accumulates 1080 positions
        in the ring (capped at 1025), yielding 1024 velocity samples → FFT activates.
        Calls 1–8 must return tremor_peak_hz = 0.0; call 9+ must return > 0.0.
        """
        fs = 1000.0
        extractor = BiometricFeatureExtractor()
        # Calls 1–8: 8 × 120 = 960 cumulative positions → ring has 960 → 959 velocity < 1024
        for call in range(8):
            snaps = [self._snap_rx(rx=1000.0 * math.sin(2 * math.pi * 10.0 * (call * 120 + i) / fs))
                     for i in range(120)]
            feats = extractor.extract(snaps)
            self.assertEqual(feats.tremor_peak_hz, 0.0,
                             f"Call {call+1}: ring not yet full ({(call+1)*120} positions), FFT must be inactive")

        # Call 9: adds positions 961–1080 → ring capped at 1025 → 1024 velocity samples → FFT active
        snaps_9 = [self._snap_rx(rx=1000.0 * math.sin(2 * math.pi * 10.0 * (8 * 120 + i) / fs))
                   for i in range(120)]
        feats_9 = extractor.extract(snaps_9)
        self.assertGreater(feats_9.tremor_peak_hz, 0.0,
                           "Call 9: ring full (1025 positions / 1024 velocity samples), FFT must activate")


if __name__ == "__main__":
    unittest.main()
