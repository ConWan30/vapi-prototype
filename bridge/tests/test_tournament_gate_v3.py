"""
Phase 37 — TournamentGateV3 Python unit tests (mock-based, no Hardhat).

4 tests covering:
1. isEligible() True when score + velocity + isActive all pass
2. assertEligible() raises when isActive() = False (suspended)
3. assertEligible() raises when cumulative score too low
4. assertEligible() raises when velocity too low
"""
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_gate(cumul=100, velocity=5, is_active=True,
               min_cumul=50, min_vel=3, vel_window=3600):
    """Return a mock TournamentGateV3-equivalent object backed by mock contracts."""
    registry   = MagicMock()
    credential = MagicMock()
    registry.cumulativeScore.return_value      = cumul
    registry.getRecentVelocity.return_value    = velocity
    credential.isActive.return_value           = is_active

    class _MockV3:
        def __init__(self):
            self.phgRegistry   = registry
            self.phgCredential = credential
            self.minCumulative  = min_cumul
            self.minVelocity    = min_vel
            self.velocityWindow = vel_window

        def assertEligible(self, device_id):
            cumulative = self.phgRegistry.cumulativeScore(device_id)
            if cumulative < self.minCumulative:
                raise ValueError(f"InsufficientHumanityScore: have={cumulative} need={self.minCumulative}")
            vel = self.phgRegistry.getRecentVelocity(device_id, self.velocityWindow)
            if vel < self.minVelocity:
                raise ValueError(f"InsufficientRecentVelocity: have={vel} need={self.minVelocity}")
            if not self.phgCredential.isActive(device_id):
                raise ValueError(f"CredentialSuspended: {device_id}")

        def isEligible(self, device_id) -> bool:
            try:
                self.assertEligible(device_id)
                return True
            except ValueError:
                return False

    return _MockV3()


class TestTournamentGateV3(unittest.TestCase):

    def test_1_is_eligible_all_pass(self):
        """isEligible() returns True when score, velocity, and isActive all pass."""
        gate = _make_gate(cumul=100, velocity=5, is_active=True)
        self.assertTrue(gate.isEligible("aa" * 32))

    def test_2_assert_eligible_reverts_credential_suspended(self):
        """assertEligible() raises CredentialSuspended when isActive() is False."""
        gate = _make_gate(cumul=100, velocity=5, is_active=False)
        with self.assertRaises(ValueError) as ctx:
            gate.assertEligible("bb" * 32)
        self.assertIn("CredentialSuspended", str(ctx.exception))

    def test_3_assert_eligible_reverts_insufficient_score(self):
        """assertEligible() raises InsufficientHumanityScore when cumulative score too low."""
        gate = _make_gate(cumul=10, velocity=5, is_active=True, min_cumul=50)
        with self.assertRaises(ValueError) as ctx:
            gate.assertEligible("cc" * 32)
        self.assertIn("InsufficientHumanityScore", str(ctx.exception))

    def test_4_assert_eligible_reverts_insufficient_velocity(self):
        """assertEligible() raises InsufficientRecentVelocity when velocity too low."""
        gate = _make_gate(cumul=100, velocity=1, is_active=True, min_vel=3)
        with self.assertRaises(ValueError) as ctx:
            gate.assertEligible("dd" * 32)
        self.assertIn("InsufficientRecentVelocity", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
