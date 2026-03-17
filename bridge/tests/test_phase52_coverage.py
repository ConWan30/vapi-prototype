"""Phase 52 Coverage Tests — 7 tests.

Covers:
1. _run_ds_with_restart clean exit
2. _run_ds_with_restart crashes then recovers
3. _run_ds_with_restart exceeds max restarts
4. _run_ds_with_restart CancelledError propagates immediately
5. CalibrationIntelligenceAgent consecutive failure escalation (3rd failure -> log.error)
6. Batcher gas dead-letter patterns (out of gas, transaction reverted, etc.)
7. hardware_block controller_connected=False when no devices registered

All use tempfile.mkdtemp() for SQLite isolation (Windows WAL rule).
All print() calls use ASCII only (Windows encoding).
"""

import asyncio
import sys
import types
import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# ---------------------------------------------------------------------------
# Stub heavy external deps before any bridge import
# ---------------------------------------------------------------------------
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense",
             "anthropic", "numpy"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Ensure web3.exceptions has the expected attrs
_web3_exc = sys.modules["web3.exceptions"]
for _attr in ("ContractLogicError", "TransactionNotFound"):
    if not hasattr(_web3_exc, _attr):
        setattr(_web3_exc, _attr, type(_attr, (Exception,), {}))

_web3_mod = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3_mod, _attr):
        setattr(_web3_mod, _attr, MagicMock())

_eth_acct = sys.modules["eth_account"]
if not hasattr(_eth_acct, "Account"):
    _eth_acct.Account = MagicMock()

# Set minimal env vars for Config / ChainClient construction
os.environ.setdefault("POAC_VERIFIER_ADDRESS", "0x" + "12" * 20)
os.environ.setdefault("BRIDGE_PRIVATE_KEY", "0x" + "aa" * 32)

# ---------------------------------------------------------------------------
# Now import bridge modules
# ---------------------------------------------------------------------------
from vapi_bridge.main import _run_ds_with_restart
from vapi_bridge.store import Store
from vapi_bridge.batcher import Batcher
from vapi_bridge.store import STATUS_DEAD_LETTER, STATUS_FAILED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> Store:
    db_dir = tempfile.mkdtemp()
    return Store(os.path.join(db_dir, "test_p52.db"))


def _make_cfg(**kwargs):
    cfg = MagicMock()
    cfg.batch_size = 10
    cfg.batch_timeout_s = 5
    cfg.max_retries = 3
    cfg.retry_base_delay_s = 1
    cfg.phg_registry_address = ""
    cfg.phg_checkpoint_interval = 10
    cfg.l4_anomaly_threshold = 6.726
    cfg.l4_continuity_threshold = 5.097
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def _run(coro):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===========================================================================
# Test 1: _run_ds_with_restart — clean exit (ds.run returns normally)
# ===========================================================================

class TestRunDsWithRestartCleanExit(unittest.TestCase):

    def test_run_ds_with_restart_clean_exit(self):
        """ds.run() returns normally -> _run_ds_with_restart exits, run called once."""
        ds = MagicMock()
        ds.run = AsyncMock(return_value=None)

        _run(_run_ds_with_restart(ds))

        ds.run.assert_called_once()
        print("PASS: test_run_ds_with_restart_clean_exit")


# ===========================================================================
# Test 2: _run_ds_with_restart — crash on first call, recover on second
# ===========================================================================

class TestRunDsWithRestartCrashesRecovery(unittest.TestCase):

    def test_run_ds_with_restart_crashes_then_recovers(self):
        """ds.run() raises on first call, returns normally on second -> called twice."""
        call_count = 0

        async def _flaky_run():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated crash")
            # second call: return normally

        ds = MagicMock()
        ds.run = _flaky_run

        # Patch asyncio.sleep to avoid real 2s delay
        with patch("vapi_bridge.main.asyncio.sleep", new=AsyncMock(return_value=None)):
            _run(_run_ds_with_restart(ds, max_restarts=3))

        self.assertEqual(call_count, 2,
                         f"Expected ds.run called 2 times, got {call_count}")
        print("PASS: test_run_ds_with_restart_crashes_then_recovers")


# ===========================================================================
# Test 3: _run_ds_with_restart — always crashes, exceeds max restarts
# ===========================================================================

class TestRunDsWithRestartExceedsMax(unittest.TestCase):

    def test_run_ds_with_restart_exceeds_max(self):
        """ds.run() always raises -> exception re-raised after max_restarts+1 total calls."""
        call_count = 0

        async def _always_crash():
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"crash #{call_count}")

        ds = MagicMock()
        ds.run = _always_crash

        with patch("vapi_bridge.main.asyncio.sleep", new=AsyncMock(return_value=None)):
            with self.assertRaises(RuntimeError):
                _run(_run_ds_with_restart(ds, max_restarts=2))

        # Initial call + 2 restarts = 3 total calls
        self.assertEqual(call_count, 3,
                         f"Expected 3 total calls (1 initial + 2 restarts), got {call_count}")
        print("PASS: test_run_ds_with_restart_exceeds_max")


# ===========================================================================
# Test 4: _run_ds_with_restart — CancelledError propagates immediately
# ===========================================================================

class TestRunDsWithRestartCancelledError(unittest.TestCase):

    def test_run_ds_with_restart_cancelled_error_propagates(self):
        """CancelledError from ds.run() propagates immediately — no restart attempted."""
        call_count = 0

        async def _cancelled_run():
            nonlocal call_count
            call_count += 1
            raise asyncio.CancelledError()

        ds = MagicMock()
        ds.run = _cancelled_run

        with self.assertRaises(asyncio.CancelledError):
            _run(_run_ds_with_restart(ds, max_restarts=3))

        self.assertEqual(call_count, 1,
                         "CancelledError must not trigger restart — ds.run must be called once")
        print("PASS: test_run_ds_with_restart_cancelled_error_propagates")


# ===========================================================================
# Test 5: CalibrationIntelligenceAgent consecutive failure escalation
# ===========================================================================

class TestCalibIntelAgentConsecutiveFailures(unittest.TestCase):

    def test_calib_intel_agent_consecutive_failure_escalation(self):
        """After 3rd consecutive failure, log.error is called (not just log.warning)."""
        from vapi_bridge.calibration_intelligence_agent import CalibrationIntelligenceAgent

        store = _make_store()
        cfg = _make_cfg()
        agent = CalibrationIntelligenceAgent(cfg, store)

        # Make _consume_pending_events always raise RuntimeError
        async def _always_fail():
            raise RuntimeError("db error")

        agent._consume_pending_events = _always_fail

        # We need to drive the event consumer loop for 3 iterations without
        # waiting 1800s each time. We'll patch asyncio.sleep to be a no-op
        # and cancel the loop after 3 failures by counting sleep calls.

        sleep_call_count = 0
        error_calls = []
        warning_calls = []

        async def _fake_sleep(delay):
            nonlocal sleep_call_count
            sleep_call_count += 1
            # The loop structure is: sleep -> fail -> sleep -> fail -> sleep -> fail -> sleep
            # We need 4 sleeps to observe 3 failures (failure happens between sleeps).
            # Cancel on the 4th sleep call so the 3rd failure has already been logged.
            if sleep_call_count >= 4:
                # 3 failures have occurred by the time we reach sleep call 4
                raise asyncio.CancelledError()

        real_log_error   = None
        real_log_warning = None

        with patch("vapi_bridge.calibration_intelligence_agent.asyncio.sleep",
                   side_effect=_fake_sleep), \
             patch("vapi_bridge.calibration_intelligence_agent.log") as mock_log:

            mock_log.error.side_effect   = lambda *a, **k: error_calls.append(a)
            mock_log.warning.side_effect = lambda *a, **k: warning_calls.append(a)

            try:
                _run(agent.run_event_consumer())
            except asyncio.CancelledError:
                pass  # expected — we cancel after 3 failures

        # After 3 consecutive failures, log.error must have been called at least once
        self.assertGreater(len(error_calls), 0,
                           "Expected log.error after 3 consecutive failures, got none. "
                           f"warning_calls={warning_calls}")

        # The first 2 failures should go through log.warning (not log.error)
        self.assertGreater(len(warning_calls), 0,
                           "Expected at least 1 log.warning for failures 1 and 2")

        print("PASS: test_calib_intel_agent_consecutive_failure_escalation")


# ===========================================================================
# Test 6: Batcher gas dead-letter patterns
# ===========================================================================

class TestBatcherGasDeadLetterPatterns(unittest.TestCase):
    """Verify Phase 52 EVM gas/revert error strings dead-letter (not STATUS_FAILED)."""

    GAS_ERROR_STRINGS = [
        "out of gas",
        "transaction reverted",
        "execution reverted",
        "gas required exceeds allowance",
    ]

    def _make_batcher_with_chain(self, chain_mock):
        store = _make_store()
        cfg = _make_cfg()
        return Batcher(cfg, store, chain_mock), store

    def _make_record(self):
        rec = MagicMock()
        rec.inference_result = 0x20
        rec.device_id = bytes(32)
        rec.device_id_hex = "00" * 32
        rec.record_hash_hex = "aa" * 32
        rec.bounty_id = 0
        rec.schema_version = 0
        return rec

    def _run_submit_batch(self, error_string):
        """Run _submit_batch with a chain that raises the given error, collect status."""
        chain = MagicMock()
        chain.verify_single = AsyncMock(side_effect=Exception(error_string))
        batcher, store = self._make_batcher_with_chain(chain)

        statuses_set = []

        original_batch_update = store.batch_update_status

        def _capture_status(record_hashes, status):
            statuses_set.append(status)
            original_batch_update(record_hashes, status)

        store.batch_update_status = _capture_status

        record = self._make_record()
        batch = [(record, b"\x00" * 228)]

        _run(batcher._submit_batch(batch))
        return statuses_set

    def test_out_of_gas_dead_letters(self):
        statuses = self._run_submit_batch("out of gas")
        self.assertIn(STATUS_DEAD_LETTER, statuses,
                      "'out of gas' should produce STATUS_DEAD_LETTER")
        self.assertNotIn(STATUS_FAILED, [s for s in statuses
                                         if s != STATUS_DEAD_LETTER],
                         "'out of gas' should not produce STATUS_FAILED")
        print("PASS: out of gas -> dead letter")

    def test_transaction_reverted_dead_letters(self):
        statuses = self._run_submit_batch("transaction reverted")
        self.assertIn(STATUS_DEAD_LETTER, statuses,
                      "'transaction reverted' should produce STATUS_DEAD_LETTER")
        print("PASS: transaction reverted -> dead letter")

    def test_execution_reverted_dead_letters(self):
        statuses = self._run_submit_batch("execution reverted")
        self.assertIn(STATUS_DEAD_LETTER, statuses,
                      "'execution reverted' should produce STATUS_DEAD_LETTER")
        print("PASS: execution reverted -> dead letter")

    def test_gas_required_exceeds_allowance_dead_letters(self):
        statuses = self._run_submit_batch("gas required exceeds allowance")
        self.assertIn(STATUS_DEAD_LETTER, statuses,
                      "'gas required exceeds allowance' should produce STATUS_DEAD_LETTER")
        print("PASS: gas required exceeds allowance -> dead letter")

    def test_non_gas_error_does_not_dead_letter(self):
        """An unrelated error should produce STATUS_FAILED, not STATUS_DEAD_LETTER."""
        statuses = self._run_submit_batch("connection refused")
        # STATUS_FAILED should appear; STATUS_DEAD_LETTER should NOT
        self.assertIn(STATUS_FAILED, statuses,
                      "Generic error should produce STATUS_FAILED")
        # The dead-letter status should not appear for generic errors
        # (BATCHED appears first, then FAILED — no DEAD_LETTER)
        final_statuses = [s for s in statuses if s not in ("BATCHED",)]
        self.assertNotIn(STATUS_DEAD_LETTER, final_statuses,
                         "Generic connection error should NOT dead-letter")
        print("PASS: generic error -> STATUS_FAILED (not dead letter)")


# ===========================================================================
# Test 7: hardware_block controller_connected=False when no devices
# ===========================================================================

class TestHardwareBlockControllerConnectedDefault(unittest.TestCase):

    def test_hardware_block_controller_connected_false_when_no_devices(self):
        """Phase 52 fix: controller_connected starts as False; only True when _devices non-empty."""
        # Import the create_app factory and use the TestClient from httpx/starlette
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi testclient not available")

        from vapi_bridge.transports.http import create_app

        store = _make_store()
        cfg = _make_cfg()

        # Patch store.list_devices to return empty list (no devices connected)
        with patch.object(store, "list_devices", return_value=[]):
            app = create_app(cfg, store, on_record=AsyncMock())
            client = TestClient(app)
            resp = client.get("/dashboard/snapshot")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        hardware = data.get("hardware", {})

        self.assertIn("controller_connected", hardware,
                      "hardware_block must contain controller_connected key")
        self.assertFalse(hardware["controller_connected"],
                         "controller_connected must be False when no devices registered "
                         f"(got: {hardware})")
        print("PASS: test_hardware_block_controller_connected_false_when_no_devices")

    def test_hardware_block_controller_connected_true_when_devices_present(self):
        """controller_connected is True when store.list_devices returns at least one device."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi testclient not available")

        from vapi_bridge.transports.http import create_app
        import time

        store = _make_store()
        cfg = _make_cfg()

        _mock_devices = [{"device_id": "aa" * 32, "last_seen": time.time()}]
        with patch.object(store, "list_devices", return_value=_mock_devices), \
             patch.object(store, "get_stats", return_value={}):
            app = create_app(cfg, store, on_record=AsyncMock())
            client = TestClient(app)
            resp = client.get("/dashboard/snapshot")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        hardware = data.get("hardware", {})

        self.assertTrue(hardware.get("controller_connected"),
                        "controller_connected must be True when devices are present "
                        f"(got: {hardware})")
        print("PASS: test_hardware_block_controller_connected_true_when_devices_present")


if __name__ == "__main__":
    unittest.main()
