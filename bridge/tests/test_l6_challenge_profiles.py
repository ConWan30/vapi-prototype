"""
Phase C — L6 Challenge Profile Library Tests

TestL6ChallengeProfiles (8):
1. All 8 profile IDs (0-7) present in CHALLENGE_PROFILES
2. get_profile_hash() returns same value for same profile_id (deterministic)
3. Different profile_ids produce different hash values
4. BASELINE_OFF (id=0) has r2_mode==0 and l2_mode==0
5. All r2_forces and l2_forces values are in [0, 255]
6. onset_threshold_ms > 0 for all non-baseline profiles
7. PROFILE_VERSION is int type
8. TriggerChallengeProfile dataclass is frozen (setattr raises FrozenInstanceError)
"""

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from bridge.controller.l6_challenge_profiles import (
    CHALLENGE_PROFILES,
    PROFILE_VERSION,
    TriggerChallengeProfile,
    get_profile_hash,
)


class TestL6ChallengeProfiles(unittest.TestCase):

    def test_1_all_eight_profiles_present(self):
        """CHALLENGE_PROFILES must contain exactly profile IDs 0-7."""
        for pid in range(8):
            self.assertIn(pid, CHALLENGE_PROFILES, f"Missing profile_id={pid}")

    def test_2_get_profile_hash_deterministic(self):
        """Same profile_id always returns the same hash."""
        for pid in CHALLENGE_PROFILES:
            h1 = get_profile_hash(pid)
            h2 = get_profile_hash(pid)
            self.assertEqual(h1, h2, f"Hash not deterministic for profile_id={pid}")

    def test_3_different_profiles_different_hashes(self):
        """Different profile_ids produce different hash values."""
        hashes = [get_profile_hash(pid) for pid in CHALLENGE_PROFILES]
        self.assertEqual(len(hashes), len(set(hashes)),
                         "Two profiles have the same hash — collision detected")

    def test_4_baseline_off_has_zero_modes(self):
        """BASELINE_OFF (id=0) must have r2_mode==0 and l2_mode==0."""
        baseline = CHALLENGE_PROFILES[0]
        self.assertEqual(baseline.r2_mode, 0)
        self.assertEqual(baseline.l2_mode, 0)

    def test_5_all_forces_in_valid_range(self):
        """All r2_forces and l2_forces values must be in [0, 255]."""
        for pid, profile in CHALLENGE_PROFILES.items():
            for i, f in enumerate(profile.r2_forces):
                self.assertGreaterEqual(f, 0,   f"profile {pid} r2_forces[{i}]={f} < 0")
                self.assertLessEqual(f, 255,     f"profile {pid} r2_forces[{i}]={f} > 255")
            for i, f in enumerate(profile.l2_forces):
                self.assertGreaterEqual(f, 0,   f"profile {pid} l2_forces[{i}]={f} < 0")
                self.assertLessEqual(f, 255,     f"profile {pid} l2_forces[{i}]={f} > 255")

    def test_6_onset_threshold_positive_for_non_baseline(self):
        """onset_threshold_ms must be > 0 for all non-BASELINE_OFF profiles."""
        for pid, profile in CHALLENGE_PROFILES.items():
            if pid == 0:
                continue
            self.assertGreater(profile.onset_threshold_ms, 0,
                               f"profile {pid} has onset_threshold_ms <= 0")

    def test_7_profile_version_is_int(self):
        """PROFILE_VERSION must be int type."""
        self.assertIsInstance(PROFILE_VERSION, int)

    def test_8_profile_dataclass_is_frozen(self):
        """TriggerChallengeProfile must be frozen — setattr raises FrozenInstanceError."""
        profile = CHALLENGE_PROFILES[1]
        with self.assertRaises(FrozenInstanceError):
            profile.name = "HACKED"


if __name__ == "__main__":
    unittest.main()
