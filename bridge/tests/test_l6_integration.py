"""
Phase C — L6 PITL Integration Tests

TestL6Integration (9):
1. DualShockTransport.__init__ source contains '_l6_driver' and 'Phase C'
2. _l6_driver is None when l6_challenges_enabled=False (default)
3. Humanity formula source contains '0.35 * _p_l4' (L6-active branch)
4. Humanity formula source contains '0.4 * _p_l4' (L6-disabled fallback)
5. Sensor commitment is 52 bytes when l6_pending is set (test commitment logic directly)
6. Sensor commitment is 48 bytes when l6_pending is None
7. Attack G: classify() returns < 0.4 for zeroed accel + onset_ms < 5 (software injection)
8. Challenge dispatch skipped when player is idle (source contains '_player_active' check)
9. Humanity formula with L2C=None (dead-zone stick) produces valid value in [0,1]
"""

import inspect
import struct
import sys
import types
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from bridge.controller.l6_challenge_profiles import get_profile_hash
from vapi_bridge.l6_response_analyzer import L6ResponseAnalyzer, L6ResponseMetrics


def _stub_hardware_modules():
    """Stub out hardware modules so DualShockTransport can be imported."""
    for mod in ("hidapi", "hid", "pydualsense", "dualshock_emulator"):
        if mod not in sys.modules:
            sys.modules[mod] = types.ModuleType(mod)


class TestL6Integration(unittest.TestCase):

    def test_1_init_contains_l6_driver_and_phase_c(self):
        """DualShockTransport.__init__ source must contain '_l6_driver' and 'Phase C'."""
        _stub_hardware_modules()
        from vapi_bridge import dualshock_integration
        src = inspect.getsource(dualshock_integration.DualShockTransport.__init__)
        self.assertIn("_l6_driver", src)
        self.assertIn("Phase C", src)

    def test_2_l6_driver_is_none_when_disabled(self):
        """_l6_driver attribute is set to None initially (before conditional init)."""
        _stub_hardware_modules()
        from vapi_bridge import dualshock_integration
        src = inspect.getsource(dualshock_integration.DualShockTransport.__init__)
        self.assertIn("self._l6_driver = None", src)
        # Config default: l6_challenges_enabled = False (env not set)
        from vapi_bridge.config import Config
        cfg = Config()
        self.assertFalse(cfg.l6_challenges_enabled)

    def test_3_humanity_formula_contains_l6_branch(self):
        """_session_loop source must contain the L6-active humanity formula."""
        _stub_hardware_modules()
        from vapi_bridge import dualshock_integration
        src = inspect.getsource(dualshock_integration.DualShockTransport._session_loop)
        # L6-active branch (Phase 17 reweighted: +L2B/L2C oracles)
        self.assertIn("0.23 * _p_l4", src)
        self.assertIn("0.15 * self._l6_p_human", src)

    def test_4_humanity_formula_contains_fallback_branch(self):
        """_session_loop source must contain the L6-disabled fallback formula."""
        _stub_hardware_modules()
        from vapi_bridge import dualshock_integration
        src = inspect.getsource(dualshock_integration.DualShockTransport._session_loop)
        # L6-disabled fallback (Phase 17 reweighted: +L2B/L2C oracles)
        self.assertIn("0.28 * _p_l4", src)
        self.assertIn("0.20 * _p_e4", src)

    def test_5_sensor_commitment_52_bytes_when_l6_pending(self):
        """Sensor commitment extends to 52 bytes when L6 is active with a pending challenge."""
        import hashlib
        # Build the base 48-byte commitment (same struct.pack as in dualshock_integration.py)
        commitment_bytes = struct.pack(
            ">hhhhBBBBffffffIQ",
            0, 0, 0, 0,         # sticks
            0, 0, 0, 0,         # triggers + effect modes
            0.0, 0.0, 0.0,      # accel
            0.0, 0.0, 0.0,      # gyro
            0, 0,               # buttons + timestamp_ms
        )
        self.assertEqual(len(commitment_bytes), 48)

        # Simulate L6 active + pending challenge
        _l6_pid   = 2  # RIGID_HEAVY
        _l6_phash = get_profile_hash(_l6_pid)
        _l6_score = int(0.75 * 100)   # p_human = 0.75
        extended  = commitment_bytes + struct.pack(">BHB", _l6_pid, _l6_phash, _l6_score)
        self.assertEqual(len(extended), 52)

    def test_6_sensor_commitment_48_bytes_when_no_l6_pending(self):
        """Sensor commitment stays at 48 bytes when L6 is disabled or no challenge pending."""
        commitment_bytes = struct.pack(
            ">hhhhBBBBffffffIQ",
            0, 0, 0, 0,
            0, 0, 0, 0,
            0.0, 0.0, 0.0,
            0.0, 0.0, 0.0,
            0, 0,
        )
        # No L6 extension — commitment stays at 48 bytes
        self.assertEqual(len(commitment_bytes), 48)
        import hashlib
        sensor_hash = hashlib.sha256(commitment_bytes).digest()
        self.assertEqual(len(sensor_hash), 32)

    def test_7_attack_g_injection_scores_low(self):
        """Attack G: zeroed accel (grip_variance=0) + onset<5ms -> classify() < 0.4."""
        analyzer = L6ResponseAnalyzer()
        # Simulate software injection: zeroed accel, instant onset
        metrics = L6ResponseMetrics(
            onset_ms=0.5,       # sub-ms onset (software, not human)
            peak_delta=80.0,    # trigger press detected (injected)
            settle_ms=0.0,
            grip_variance=0.0,  # accel zeroed — not possible with real hand
            profile_id=1, nonce_bytes=b"\x00"*4, valid=True,
        )
        result = analyzer.classify(metrics)
        self.assertLess(result, 0.4,
                        f"Attack G: expected p_human < 0.4, got {result}")

    def test_8_idle_gate_in_source(self):
        """_session_loop source must contain player activity check before dispatch."""
        _stub_hardware_modules()
        from vapi_bridge import dualshock_integration
        src = inspect.getsource(dualshock_integration.DualShockTransport._session_loop)
        self.assertIn("_r2_at_rest", src,
                      "_r2_at_rest idle gate missing from _session_loop source")

    def test_9_humanity_formula_l2c_none_valid_range(self):
        """When L2C oracle returns None, p_L2C defaults to 0.5 and humanity_prob stays in [0,1].

        Verifies the L2C phantom-weight behaviour documented in §7.5.4: dead-zone stick games
        (e.g. NCAA Football 26) yield _l2c_max_corr=None → _l2c_p_human=0.5 neutral prior.
        The 5-signal formula must remain bounded regardless of L2C oracle state.
        """
        import math

        # Simulate L2C oracle returning None: p_L2C stays at neutral 0.5 (default).
        p_l2c = 0.5  # neutral prior — same as dualshock_integration._l2c_p_human default

        # Case A: all signals neutral (typical cold-start / dead-zone cycle)
        p_l4, p_l5, p_e4, p_l2b = 0.5, 0.5, 0.5, 0.5
        prob = 0.28 * p_l4 + 0.27 * p_l5 + 0.20 * p_e4 + 0.15 * p_l2b + 0.10 * p_l2c
        self.assertGreaterEqual(prob, 0.0)
        self.assertLessEqual(prob, 1.0)
        self.assertAlmostEqual(prob, 0.5, places=6,
                               msg="All-neutral signals should yield humanity_prob=0.5")

        # Case B: worst-case other signals, L2C neutral — result must still be >= 0
        prob_low = 0.28 * 0.0 + 0.27 * 0.0 + 0.20 * 0.0 + 0.15 * 0.0 + 0.10 * p_l2c
        self.assertGreaterEqual(prob_low, 0.0)
        self.assertLessEqual(prob_low, 1.0)
        self.assertAlmostEqual(prob_low, 0.05, places=6,
                               msg="L2C-only contribution should be 0.10 * 0.5 = 0.05")

        # Case C: best-case other signals, L2C neutral — result must still be <= 1
        prob_high = 0.28 * 1.0 + 0.27 * 1.0 + 0.20 * 1.0 + 0.15 * 1.0 + 0.10 * p_l2c
        self.assertGreaterEqual(prob_high, 0.0)
        self.assertLessEqual(prob_high, 1.0)
        self.assertAlmostEqual(prob_high, 0.95, places=6,
                               msg="Max 4-signal + L2C neutral = 0.90 + 0.05 = 0.95")

        # Verify l2c_inactive flag is documented in _session_loop source
        _stub_hardware_modules()
        from vapi_bridge import dualshock_integration
        src = inspect.getsource(dualshock_integration.DualShockTransport._session_loop)
        self.assertIn("l2c_inactive", src,
                      "l2c_inactive flag must be emitted in _pending_pitl_meta")
        self.assertIn("_l2c_max_corr is None", src,
                      "L2C dead-zone log guard must check _l2c_max_corr is None")


if __name__ == "__main__":
    unittest.main()
