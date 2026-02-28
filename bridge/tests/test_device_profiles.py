"""
Phase 19 — device profile system tests.

Tests cover:
Group 1: TestProfileRegistry (5)
    - get_profile returns correct profile
    - detect_profile by VID/PID returns correct profile
    - detect_profile for unknown VID/PID returns None
    - all_profiles returns expected count
    - DeviceProfile is immutable (frozen dataclass)

Group 2: TestPHCITiers (5)
    - DualShock Edge → PHCITier.CERTIFIED
    - Generic DualSense → PHCITier.STANDARD
    - SCUF Reflex Pro → PHCITier.STANDARD
    - HORI Fighting Commander → PHCITier.NONE
    - Battle Beaver Edge → PHCITier.CERTIFIED

Group 3: TestProfileFields (4)
    - schema_version >= 2 for all profiles
    - pitl_layers contains only valid layer numbers (2–5)
    - sensor_commitment_size matches expected for biometric/non-biometric
    - display_name is non-empty for all profiles

Group 4: TestDeviceProfileRegistry (4)
    - resolve() with explicit device_profile_id config
    - resolve() falls back to default when auto_detect_device=False
    - detect_profile VID/PID Sony → DualShock Edge
    - detect_profile VID/PID SCUF → SCUF Reflex Pro
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Add controller/ to path for direct imports
_controller_dir = str(Path(__file__).resolve().parents[2] / "controller")
if _controller_dir not in sys.path:
    sys.path.insert(0, _controller_dir)

from device_profile import ControllerFamily, DeviceProfile, PHCITier
from profiles import (
    BATTLE_BEAVER_EDGE,
    DUALSHOCK_EDGE,
    GENERIC_DUALSENSE,
    HORI_FIGHTING_COMMANDER,
    SCUF_REFLEX_PRO,
    all_profiles,
    detect_profile,
    get_profile,
)


# ---------------------------------------------------------------------------
# Group 1: Profile registry
# ---------------------------------------------------------------------------

class TestProfileRegistry(unittest.TestCase):

    def test_get_profile_dualshock_edge(self):
        """get_profile('sony_dualshock_edge_v1') returns DUALSHOCK_EDGE."""
        profile = get_profile("sony_dualshock_edge_v1")
        self.assertEqual(profile.profile_id, "sony_dualshock_edge_v1")
        self.assertEqual(profile.manufacturer, "Sony Interactive Entertainment")

    def test_detect_profile_sony_usb_vid_pid(self):
        """detect_profile(0x054C, 0x0DF2) returns DualShock Edge."""
        profile = detect_profile(0x054C, 0x0DF2)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.profile_id, "sony_dualshock_edge_v1")

    def test_detect_profile_scuf_vid_pid(self):
        """detect_profile(0x2F24, 0x0011) returns SCUF Reflex Pro."""
        profile = detect_profile(0x2F24, 0x0011)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.profile_id, "scuf_reflex_pro_v1")

    def test_detect_profile_unknown_returns_none(self):
        """detect_profile for an unregistered VID/PID returns None."""
        profile = detect_profile(0xFFFF, 0xFFFF)
        self.assertIsNone(profile)

    def test_all_profiles_count(self):
        """all_profiles() returns at least 5 registered profiles."""
        profiles = all_profiles()
        self.assertGreaterEqual(len(profiles), 5)

    def test_profile_immutable(self):
        """DeviceProfile is a frozen dataclass — attribute assignment raises."""
        profile = get_profile("sony_dualshock_edge_v1")
        with self.assertRaises((TypeError, AttributeError)):
            profile.display_name = "hacked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Group 2: PHCI tier correctness
# ---------------------------------------------------------------------------

class TestPHCITiers(unittest.TestCase):

    def test_dualshock_edge_certified(self):
        """DualShock Edge is PHCITier.CERTIFIED."""
        self.assertEqual(DUALSHOCK_EDGE.phci_tier, PHCITier.CERTIFIED)

    def test_generic_dualsense_standard(self):
        """Generic DualSense (no adaptive triggers) is PHCITier.STANDARD."""
        self.assertEqual(GENERIC_DUALSENSE.phci_tier, PHCITier.STANDARD)

    def test_scuf_reflex_pro_standard(self):
        """SCUF Reflex Pro (no adaptive triggers) is PHCITier.STANDARD."""
        self.assertEqual(SCUF_REFLEX_PRO.phci_tier, PHCITier.STANDARD)

    def test_hori_fighting_none(self):
        """HORI Fighting Commander is PHCITier.NONE (no sticks, no IMU)."""
        self.assertEqual(HORI_FIGHTING_COMMANDER.phci_tier, PHCITier.NONE)

    def test_battle_beaver_certified(self):
        """Battle Beaver DualSense Edge mod is PHCITier.CERTIFIED (keeps adaptive triggers)."""
        self.assertEqual(BATTLE_BEAVER_EDGE.phci_tier, PHCITier.CERTIFIED)


# ---------------------------------------------------------------------------
# Group 3: Profile field invariants
# ---------------------------------------------------------------------------

class TestProfileFields(unittest.TestCase):

    def test_schema_version_at_least_2(self):
        """All profiles have schema_version >= 2 (v2 kinematic schema)."""
        for p in all_profiles():
            self.assertGreaterEqual(
                p.schema_version, 2,
                msg=f"{p.profile_id}: schema_version={p.schema_version} < 2",
            )

    def test_pitl_layers_valid(self):
        """All pitl_layers values are in the valid range [2, 5]."""
        for p in all_profiles():
            for layer in p.pitl_layers:
                self.assertIn(
                    layer, (2, 3, 4, 5),
                    msg=f"{p.profile_id}: invalid PITL layer {layer}",
                )

    def test_sensor_commitment_size_matches_biometric_flag(self):
        """
        Profiles with adaptive triggers (PHCI_CERTIFIED) must use 56B commitment.
        Profiles without adaptive triggers must use 48B.
        """
        for p in all_profiles():
            if p.has_adaptive_triggers and p.phci_tier.value >= PHCITier.CERTIFIED:
                self.assertGreaterEqual(
                    p.sensor_commitment_size_bytes, 56,
                    msg=f"{p.profile_id}: CERTIFIED but commitment < 56B",
                )
            else:
                self.assertLessEqual(
                    p.sensor_commitment_size_bytes, 48,
                    msg=f"{p.profile_id}: non-CERTIFIED but commitment > 48B",
                )

    def test_display_name_non_empty(self):
        """Every profile must have a non-empty display_name."""
        for p in all_profiles():
            self.assertTrue(
                p.display_name,
                msg=f"{p.profile_id}: display_name is empty",
            )


# ---------------------------------------------------------------------------
# Group 4: DeviceProfileRegistry
# ---------------------------------------------------------------------------

class TestDeviceProfileRegistry(unittest.TestCase):

    def _make_registry(self):
        """Import DeviceProfileRegistry with controller/ on path."""
        _bridge_dir = str(Path(__file__).resolve().parents[1])
        if _bridge_dir not in sys.path:
            sys.path.insert(0, _bridge_dir)
        from vapi_bridge.device_registry import DeviceProfileRegistry
        return DeviceProfileRegistry(Path(_controller_dir))

    def test_resolve_explicit_profile_id(self):
        """resolve() with cfg.device_profile_id returns that exact profile."""
        registry = self._make_registry()
        cfg = MagicMock()
        cfg.device_profile_id = "scuf_reflex_pro_v1"
        cfg.auto_detect_device = False
        profile = registry.resolve(cfg)
        self.assertEqual(profile.profile_id, "scuf_reflex_pro_v1")

    def test_resolve_fallback_to_default(self):
        """resolve() with auto_detect_device=False falls back to DualShock Edge."""
        registry = self._make_registry()
        cfg = MagicMock()
        cfg.device_profile_id = ""
        cfg.auto_detect_device = False
        profile = registry.resolve(cfg)
        self.assertEqual(profile.profile_id, "sony_dualshock_edge_v1")

    def test_resolve_explicit_overrides_auto_detect(self):
        """Explicit device_profile_id takes priority over auto-detection."""
        registry = self._make_registry()
        cfg = MagicMock()
        cfg.device_profile_id = "hori_fighting_commander_ps5_v1"
        cfg.auto_detect_device = True  # even with auto-detect enabled, explicit wins
        profile = registry.resolve(cfg)
        self.assertEqual(profile.profile_id, "hori_fighting_commander_ps5_v1")

    def test_all_profiles_available_via_registry(self):
        """registry.all_profiles() returns at least 5 profiles."""
        registry = self._make_registry()
        profiles = registry.all_profiles()
        self.assertGreaterEqual(len(profiles), 5)


if __name__ == "__main__":
    unittest.main()
