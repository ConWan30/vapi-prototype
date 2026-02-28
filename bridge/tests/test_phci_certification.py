"""
Phase 19 — PHCICertifier tests.

Tests cover:
1. DUALSHOCK_EDGE → PHCITier.CERTIFIED, is_certified=True
2. GENERIC_DUALSENSE → PHCITier.STANDARD, is_certified=True
3. SCUF_REFLEX_PRO → PHCITier.STANDARD, is_certified=True
4. HORI_FIGHTING_COMMANDER → PHCITier.NONE, is_certified=False
5. BATTLE_BEAVER_EDGE → PHCITier.CERTIFIED, is_certified=True
6. Certification score > 80 for CERTIFIED tier (DualShock Edge)
7. failed_checks includes 'has_adaptive_triggers' for GENERIC_DUALSENSE
8. passed_checks includes all STANDARD_REQUIREMENTS for SCUF_REFLEX_PRO
"""

import sys
import unittest
from pathlib import Path

_controller_dir = str(Path(__file__).resolve().parents[2] / "controller")
if _controller_dir not in sys.path:
    sys.path.insert(0, _controller_dir)

from device_profile import PHCITier
from phci_certification import PHCICertification, PHCICertifier
from profiles import (
    BATTLE_BEAVER_EDGE,
    DUALSHOCK_EDGE,
    GENERIC_DUALSENSE,
    HORI_FIGHTING_COMMANDER,
    SCUF_REFLEX_PRO,
)


class TestPHCICertifier(unittest.TestCase):

    def setUp(self):
        self.certifier = PHCICertifier()

    def test_dualshock_edge_is_certified(self):
        """DualShock Edge → PHCITier.CERTIFIED, is_certified=True."""
        result = self.certifier.certify(DUALSHOCK_EDGE)
        self.assertEqual(result.phci_tier, PHCITier.CERTIFIED)
        self.assertTrue(result.is_certified)
        self.assertFalse(result.failed_checks,
                         msg=f"Unexpected failed checks: {result.failed_checks}")

    def test_generic_dualsense_is_standard(self):
        """Generic DualSense → PHCITier.STANDARD, is_certified=True."""
        result = self.certifier.certify(GENERIC_DUALSENSE)
        self.assertEqual(result.phci_tier, PHCITier.STANDARD)
        self.assertTrue(result.is_certified)

    def test_scuf_reflex_pro_is_standard(self):
        """SCUF Reflex Pro → PHCITier.STANDARD, is_certified=True."""
        result = self.certifier.certify(SCUF_REFLEX_PRO)
        self.assertEqual(result.phci_tier, PHCITier.STANDARD)
        self.assertTrue(result.is_certified)

    def test_hori_fighting_commander_is_none(self):
        """HORI Fighting Commander → PHCITier.NONE, is_certified=False."""
        result = self.certifier.certify(HORI_FIGHTING_COMMANDER)
        self.assertEqual(result.phci_tier, PHCITier.NONE)
        self.assertFalse(result.is_certified)

    def test_battle_beaver_edge_is_certified(self):
        """Battle Beaver DualSense Edge mod → PHCITier.CERTIFIED (keeps adaptive triggers)."""
        result = self.certifier.certify(BATTLE_BEAVER_EDGE)
        self.assertEqual(result.phci_tier, PHCITier.CERTIFIED)
        self.assertTrue(result.is_certified)

    def test_certified_score_above_80(self):
        """DualShock Edge certification score > 80 (expect 100 for full CERTIFIED pass)."""
        result = self.certifier.certify(DUALSHOCK_EDGE)
        self.assertGreater(
            result.score, 80,
            msg=f"Expected CERTIFIED score > 80, got {result.score}",
        )

    def test_generic_dualsense_failed_checks_includes_adaptive_triggers(self):
        """Generic DualSense failed_checks includes 'has_adaptive_triggers'."""
        result = self.certifier.certify(GENERIC_DUALSENSE)
        self.assertIn(
            "has_adaptive_triggers", result.failed_checks,
            msg=f"Expected 'has_adaptive_triggers' in failed_checks; got {result.failed_checks}",
        )

    def test_scuf_passed_checks_include_standard_requirements(self):
        """SCUF Reflex Pro passed_checks includes all STANDARD_REQUIREMENTS."""
        result = self.certifier.certify(SCUF_REFLEX_PRO)
        for req in PHCICertifier.STANDARD_REQUIREMENTS:
            self.assertIn(
                req, result.passed_checks,
                msg=f"SCUF should pass '{req}' but got passed={result.passed_checks}",
            )


if __name__ == "__main__":
    unittest.main()
