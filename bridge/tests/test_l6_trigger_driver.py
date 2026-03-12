"""
Phase C — L6 Trigger Driver Tests

TestL6TriggerDriver (8):
1. L6TriggerDriver instantiates without hardware
2. select_random_profile() never returns 0 (BASELINE_OFF)
3. select_random_profile() always returns id in [1, 7]
4. _sync_write() calls setMode and setForce on mock trigger objects
5. send_challenge() returns a float (monotonic timestamp)
6. clear_triggers() restores BASELINE_OFF (mode=0 for both triggers)
7. current_nonce changes between select_random_profile() calls
8. is_response_window_open() returns False after timeout exceeded
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from bridge.controller.l6_challenge_profiles import CHALLENGE_PROFILES
from bridge.controller.l6_trigger_driver import ChallengeSequencer, L6TriggerDriver


def _make_mock_ds():
    """Return a mock DualSense object with triggerL and triggerR."""
    ds = MagicMock()
    ds.triggerL = MagicMock()
    ds.triggerR = MagicMock()
    return ds


def _patch_trigger_modes():
    """Patch pydualsense.TriggerModes so _sync_write works without hardware."""
    import sys
    mock_tm = MagicMock()
    mock_tm.side_effect = lambda x: x  # TriggerModes(int) → passthrough
    mock_pydualsense = MagicMock()
    mock_pydualsense.TriggerModes = mock_tm
    sys.modules["pydualsense"] = mock_pydualsense  # force-set: override bare stub from integration tests
    return mock_pydualsense


class TestL6TriggerDriver(unittest.TestCase):

    def test_1_driver_instantiates_without_hardware(self):
        """L6TriggerDriver() must not raise even without a connected controller."""
        driver = L6TriggerDriver()
        self.assertIsNotNone(driver)
        self.assertIsNotNone(driver.sequencer)

    def test_2_select_random_profile_never_baseline(self):
        """select_random_profile() must never return 0 (BASELINE_OFF)."""
        seq = ChallengeSequencer()
        for _ in range(50):
            pid = seq.select_random_profile()
            self.assertNotEqual(pid, 0,
                                "select_random_profile returned BASELINE_OFF (id=0)")

    def test_3_select_random_profile_valid_range(self):
        """select_random_profile() must return id in [1, 7]."""
        seq = ChallengeSequencer()
        for _ in range(50):
            pid = seq.select_random_profile()
            self.assertGreaterEqual(pid, 1)
            self.assertLessEqual(pid, 7)

    def test_4_sync_write_calls_setmode_and_setforce(self):
        """_sync_write() must call setMode and setForce on both trigger objects."""
        _patch_trigger_modes()
        ds = _make_mock_ds()
        profile = CHALLENGE_PROFILES[1]  # RIGID_LIGHT
        L6TriggerDriver._sync_write(ds, profile)

        # Both triggers must have setMode called
        ds.triggerL.setMode.assert_called_once()
        ds.triggerR.setMode.assert_called_once()
        # setForce must be called for each force value in the profile
        self.assertEqual(ds.triggerL.setForce.call_count, len(profile.l2_forces))
        self.assertEqual(ds.triggerR.setForce.call_count, len(profile.r2_forces))

    def test_5_send_challenge_returns_float(self):
        """send_challenge() must return a float (monotonic timestamp)."""
        _patch_trigger_modes()
        driver = L6TriggerDriver()
        ds = _make_mock_ds()

        async def _fake_thread(fn, *args, **kwargs):
            fn(*args, **kwargs)

        async def _run():
            with patch("bridge.controller.l6_trigger_driver.asyncio.to_thread",
                       side_effect=_fake_thread):
                return await driver.send_challenge(1, ds)

        ts = asyncio.run(_run())
        self.assertIsInstance(ts, float)
        self.assertGreater(ts, 0.0)

    def test_6_clear_triggers_uses_baseline_off(self):
        """clear_triggers() must call _sync_write with BASELINE_OFF (mode=0 for both)."""
        _patch_trigger_modes()
        driver = L6TriggerDriver()
        ds = _make_mock_ds()

        async def _fake_thread(fn, *args, **kwargs):
            fn(*args, **kwargs)

        async def _run():
            with patch("bridge.controller.l6_trigger_driver.asyncio.to_thread",
                       side_effect=_fake_thread):
                await driver.clear_triggers(ds)

        asyncio.run(_run())
        # BASELINE_OFF has mode 0 for both triggers
        ds.triggerL.setMode.assert_called_once_with(0)
        ds.triggerR.setMode.assert_called_once_with(0)

    def test_7_nonce_changes_between_selections(self):
        """current_nonce must be regenerated on each select_random_profile() call."""
        seq = ChallengeSequencer()
        nonces = set()
        for _ in range(20):
            seq.select_random_profile()
            nonces.add(seq.current_nonce)
        # With 4-byte random nonces across 20 draws, collision probability is negligible
        self.assertGreater(len(nonces), 1,
                           "current_nonce never changed across 20 selections")

    def test_8_response_window_expires(self):
        """is_response_window_open() must return False after timeout_s has elapsed."""
        seq = ChallengeSequencer()
        old_ts = time.monotonic() - 10.0  # 10 seconds ago
        self.assertFalse(seq.is_response_window_open(old_ts, timeout_s=3.0))

    # Helper: also verify it returns True for a fresh timestamp
    def test_8b_response_window_open_for_fresh_ts(self):
        """is_response_window_open() must return True immediately after challenge."""
        seq = ChallengeSequencer()
        fresh_ts = time.monotonic()
        self.assertTrue(seq.is_response_window_open(fresh_ts, timeout_s=3.0))


if __name__ == "__main__":
    unittest.main()
