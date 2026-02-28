"""
Phase 28 — FeatureNormalizer Tests

TestFeatureNormalizer (8):
1.  CERTIFIED profile (DualShock Edge) → all 7 canonical keys present
2.  STANDARD profile (DualSense) → trigger_resistance_change_rate = 0.0 (no adaptive triggers)
3.  GENERIC_XINPUT profile (Xbox Elite S2) → micro_tremor_accel_variance = 0.0 (no IMU)
4.  normalize() passes non-zero values through unchanged for supported features
5.  normalize() zero-fills missing keys (partial raw dict input)
6.  supported_keys omits IMU key for no-IMU profile
7.  supported_keys omits adaptive key for no-adaptive-triggers profile
8.  detect_profile(0x045E, 0x0B00) returns Xbox Elite S2 profile
"""

import sys
import types
import unittest
from pathlib import Path

CONTROLLER_DIR = Path(__file__).parents[2] / "controller"
sys.path.insert(0, str(CONTROLLER_DIR))

# Stub heavy deps before imports
for _mod in ("hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from feature_normalizer import CANONICAL_KEYS, FeatureNormalizer
from profiles import detect_profile, get_profile
from profiles.xbox_elite_s2 import XBOX_ELITE_S2


# ===========================================================================
# TestFeatureNormalizer
# ===========================================================================

class TestFeatureNormalizer(unittest.TestCase):

    def test_1_certified_profile_all_7_keys_present(self):
        """CERTIFIED profile (DualShock Edge) → normalize() output has all 7 canonical keys."""
        profile = get_profile("sony_dualshock_edge_v1")
        norm = FeatureNormalizer(profile)
        result = norm.normalize({})  # empty raw → all zero-filled
        self.assertEqual(set(result.keys()), set(CANONICAL_KEYS))
        self.assertEqual(len(result), 7)

    def test_2_standard_dualsense_trigger_resistance_zeroed(self):
        """STANDARD profile (DualSense) → trigger_resistance_change_rate = 0.0."""
        profile = get_profile("sony_dualsense_v1")
        norm = FeatureNormalizer(profile)
        # Provide a non-zero value — should be overridden to 0.0
        raw = {"trigger_resistance_change_rate": 0.5, "stick_autocorr_lag1": 0.3}
        result = norm.normalize(raw)
        self.assertEqual(result["trigger_resistance_change_rate"], 0.0)
        # Other supported keys pass through
        self.assertAlmostEqual(result["stick_autocorr_lag1"], 0.3, places=6)

    def test_3_xbox_elite_s2_micro_tremor_zeroed(self):
        """Xbox Elite S2 (no IMU) → micro_tremor_accel_variance = 0.0."""
        norm = FeatureNormalizer(XBOX_ELITE_S2)
        raw = {"micro_tremor_accel_variance": 0.8, "grip_asymmetry": 0.4}
        result = norm.normalize(raw)
        self.assertEqual(result["micro_tremor_accel_variance"], 0.0)
        # grip_asymmetry is not IMU-dependent, passes through
        self.assertAlmostEqual(result["grip_asymmetry"], 0.4, places=6)

    def test_4_non_zero_values_pass_through_for_supported_features(self):
        """normalize() passes non-zero values unchanged for features the profile supports."""
        profile = get_profile("sony_dualshock_edge_v1")
        norm = FeatureNormalizer(profile)
        raw = {
            "trigger_resistance_change_rate": 0.7,
            "trigger_onset_velocity_l2": 0.55,
            "micro_tremor_accel_variance": 0.12,
            "stick_autocorr_lag5": 0.33,
        }
        result = norm.normalize(raw)
        self.assertAlmostEqual(result["trigger_resistance_change_rate"], 0.7, places=6)
        self.assertAlmostEqual(result["trigger_onset_velocity_l2"], 0.55, places=6)
        self.assertAlmostEqual(result["micro_tremor_accel_variance"], 0.12, places=6)
        self.assertAlmostEqual(result["stick_autocorr_lag5"], 0.33, places=6)

    def test_5_zero_fills_missing_keys(self):
        """normalize() zero-fills keys not present in the raw dict."""
        profile = get_profile("sony_dualshock_edge_v1")
        norm = FeatureNormalizer(profile)
        # Only provide 2 of 7 keys
        raw = {"grip_asymmetry": 0.6}
        result = norm.normalize(raw)
        self.assertEqual(set(result.keys()), set(CANONICAL_KEYS))
        self.assertAlmostEqual(result["grip_asymmetry"], 0.6, places=6)
        # All other keys should be 0.0
        for k in CANONICAL_KEYS:
            if k != "grip_asymmetry":
                self.assertEqual(result[k], 0.0, msg=f"Expected 0.0 for key {k!r}")

    def test_6_supported_keys_omits_imu_for_no_imu_profile(self):
        """supported_keys omits IMU-dependent keys for profiles with no IMU."""
        norm = FeatureNormalizer(XBOX_ELITE_S2)
        self.assertNotIn("micro_tremor_accel_variance", norm.supported_keys)

    def test_7_supported_keys_omits_adaptive_for_no_adaptive_profile(self):
        """supported_keys omits adaptive-trigger keys for profiles with no adaptive triggers."""
        profile = get_profile("sony_dualsense_v1")  # STANDARD, no adaptive triggers
        norm = FeatureNormalizer(profile)
        self.assertNotIn("trigger_resistance_change_rate", norm.supported_keys)

    def test_8_detect_profile_xbox_elite_s2(self):
        """detect_profile(0x045E, 0x0B00) returns the Xbox Elite S2 profile."""
        profile = detect_profile(0x045E, 0x0B00)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.profile_id, "xbox_elite_s2_v1")
        self.assertEqual(profile.display_name, "Xbox Elite Series 2")


if __name__ == "__main__":
    unittest.main()
