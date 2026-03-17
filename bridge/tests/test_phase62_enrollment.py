"""
Phase 62 -- Enrollment Ceremony Tests (16 tests)

TestEnrollmentStore (4 tests):
  test_1_upsert_enrollment_creates_row
  test_2_count_nominal_sessions_excludes_cheat_codes
  test_3_get_eligible_unenrolled_filters_status
  test_4_get_latest_pitl_proof_returns_most_recent

TestEnrollmentManager (6 tests):
  test_5_update_enrollment_tracks_progress
  test_6_update_enrollment_triggers_mint_when_threshold_met
  test_7_update_enrollment_skips_if_already_credentialed
  test_8_update_enrollment_hard_cheat_does_not_trigger_mint
  test_9_try_mint_credential_calls_chain_mint
  test_10_try_mint_credential_checks_on_chain_first

TestEnrollmentEndpoint (3 tests):
  test_11_enrollment_status_returns_pending_for_new_device
  test_12_enrollment_status_returns_progress_after_upsert
  test_13_enrollment_status_returns_credentialed_after_mint

TestEnrollmentBridgeAgent (3 tests):
  test_14_get_enrollment_status_tool_returns_dict
  test_15_get_enrollment_status_tool_includes_sessions_needed
  test_16_get_enrollment_status_tool_handles_no_row
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy optional dependencies before importing bridge modules
for _mod in ["web3", "web3.exceptions", "eth_account", "eth_account.signers.local"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from vapi_bridge.store import Store
from vapi_bridge.enrollment_manager import EnrollmentManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> Store:
    """Create a fresh in-memory Store backed by a temp file."""
    td = tempfile.mkdtemp()
    return Store(str(Path(td) / "test_enroll.db"))


def _make_cfg(min_sessions: int = 3, min_humanity: float = 0.60):
    cfg = MagicMock()
    cfg.enrollment_min_sessions = min_sessions
    cfg.enrollment_humanity_min = min_humanity
    return cfg


_seed_counter = 0

def _seed_pitl_proof(store: Store, device_id: str, inference_code: int, hp_int: int = 750):
    """Insert a minimal pitl_session_proofs row (uses store's internal conn).
    Uses a global counter to ensure unique nullifier_hash across repeated calls.
    """
    import time
    global _seed_counter
    _seed_counter += 1
    unique_null = hex(hash(device_id + str(_seed_counter)))
    with store._conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO pitl_session_proofs "
            "(device_id, nullifier_hash, feature_commitment, humanity_prob_int, "
            "inference_code, tx_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (device_id, unique_null,
             hex(hash(device_id + str(_seed_counter))), hp_int,
             inference_code, "", time.time()),
        )


_DEVICE_A = "aa" * 32
_DEVICE_B = "bb" * 32

NOMINAL    = 0x20   # 32
CHEAT_INJ  = 0x28   # 40


# ===========================================================================
# TestEnrollmentStore
# ===========================================================================

class TestEnrollmentStore(unittest.TestCase):

    def setUp(self):
        self.store = _make_store()

    def test_1_upsert_enrollment_creates_row(self):
        """upsert_enrollment creates a new row when none exists."""
        self.store.upsert_enrollment(_DEVICE_A, 5, 10, 0.75, "pending")
        row = self.store.get_enrollment(_DEVICE_A)
        self.assertIsNotNone(row)
        self.assertEqual(row["device_id"], _DEVICE_A)
        self.assertEqual(row["sessions_nominal"], 5)
        self.assertEqual(row["status"], "pending")
        self.assertAlmostEqual(row["avg_humanity"], 0.75, places=4)

    def test_2_count_nominal_sessions_excludes_cheat_codes(self):
        """count_nominal_sessions only counts inference_code=32 or NULL rows."""
        _seed_pitl_proof(self.store, _DEVICE_A, NOMINAL, hp_int=800)
        _seed_pitl_proof(self.store, _DEVICE_A, NOMINAL, hp_int=700)
        _seed_pitl_proof(self.store, _DEVICE_A, CHEAT_INJ, hp_int=600)
        count, avg_h = self.store.count_nominal_sessions(_DEVICE_A)
        self.assertEqual(count, 2)
        # avg_h = mean(800, 700)/1000 = 0.75
        self.assertAlmostEqual(avg_h, 0.75, places=4)

    def test_3_get_eligible_unenrolled_filters_status(self):
        """get_eligible_unenrolled returns only status='eligible' rows."""
        self.store.upsert_enrollment(_DEVICE_A, 10, 20, 0.80, "eligible")
        self.store.upsert_enrollment(_DEVICE_B, 5,  10, 0.65, "pending")
        rows = self.store.get_eligible_unenrolled()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["device_id"], _DEVICE_A)

    def test_4_get_latest_pitl_proof_returns_most_recent(self):
        """get_latest_pitl_proof returns the most recently inserted proof row."""
        import time as _t
        with self.store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pitl_session_proofs "
                "(device_id, nullifier_hash, feature_commitment, humanity_prob_int, "
                "inference_code, tx_hash, created_at) VALUES (?,?,?,?,?,?,?)",
                (_DEVICE_A, "0xaaa", "0xbbb", 700, 32, "", _t.time() - 10),
            )
            conn.execute(
                "INSERT OR IGNORE INTO pitl_session_proofs "
                "(device_id, nullifier_hash, feature_commitment, humanity_prob_int, "
                "inference_code, tx_hash, created_at) VALUES (?,?,?,?,?,?,?)",
                (_DEVICE_A, "0xccc", "0xddd", 900, 32, "", _t.time()),
            )
        row = self.store.get_latest_pitl_proof(_DEVICE_A)
        self.assertIsNotNone(row)
        self.assertEqual(row["nullifier_hash"], "0xccc")


# ===========================================================================
# TestEnrollmentManager
# ===========================================================================

class TestEnrollmentManager(unittest.TestCase):

    def setUp(self):
        self.store = _make_store()
        self.chain = MagicMock()
        self.chain.has_phg_credential = AsyncMock(return_value=False)
        self.chain.mint_phg_credential = AsyncMock(return_value="0xdeadbeef")
        self.cfg   = _make_cfg(min_sessions=2, min_humanity=0.60)
        self.em    = EnrollmentManager(self.store, self.chain, self.cfg)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_5_update_enrollment_tracks_progress(self):
        """update_enrollment creates a progress row for a device."""
        _seed_pitl_proof(self.store, _DEVICE_A, NOMINAL, hp_int=700)
        self._run(self.em.update_enrollment(_DEVICE_A, NOMINAL, 0.70))
        row = self.store.get_enrollment(_DEVICE_A)
        self.assertIsNotNone(row)
        self.assertEqual(row["sessions_nominal"], 1)

    def test_6_update_enrollment_triggers_mint_when_threshold_met(self):
        """After min_sessions NOMINAL proofs, status transitions to eligible."""
        for i in range(self.cfg.enrollment_min_sessions):
            _seed_pitl_proof(self.store, _DEVICE_A, NOMINAL, hp_int=800)
        self._run(self.em.update_enrollment(_DEVICE_A, NOMINAL, 0.80))
        row = self.store.get_enrollment(_DEVICE_A)
        # After the async task fires (still in event loop), status should be eligible or credentialed
        self.assertIn(row["status"], ("eligible", "minting", "credentialed"))

    def test_7_update_enrollment_skips_if_already_credentialed(self):
        """update_enrollment is a no-op when device is already credentialed."""
        self.store.upsert_enrollment(_DEVICE_A, 10, 10, 0.90, "credentialed", "0xabc")
        self._run(self.em.update_enrollment(_DEVICE_A, NOMINAL, 0.90))
        row = self.store.get_enrollment(_DEVICE_A)
        # Status must not change
        self.assertEqual(row["status"], "credentialed")

    def test_8_update_enrollment_hard_cheat_does_not_trigger_mint(self):
        """Hard cheat code session prevents enrollment progression to eligible."""
        for i in range(self.cfg.enrollment_min_sessions):
            _seed_pitl_proof(self.store, _DEVICE_A, NOMINAL, hp_int=800)
        # Pass a cheat code as inference_code (simulates a cheat session)
        self._run(self.em.update_enrollment(_DEVICE_A, CHEAT_INJ, 0.80))
        row = self.store.get_enrollment(_DEVICE_A)
        # Should NOT be eligible when inference_code is a hard cheat
        self.assertNotEqual(row["status"], "eligible")

    def test_9_try_mint_credential_calls_chain_mint(self):
        """_try_mint_credential calls chain.mint_phg_credential with correct args."""
        _seed_pitl_proof(self.store, _DEVICE_A, NOMINAL, hp_int=800)
        self._run(self.em._try_mint_credential(_DEVICE_A))
        self.chain.mint_phg_credential.assert_called_once()
        call_args = self.chain.mint_phg_credential.call_args[0]
        self.assertEqual(call_args[0], _DEVICE_A)  # device_id
        self.assertIsInstance(call_args[3], int)    # humanity_prob_int

    def test_10_try_mint_credential_checks_on_chain_first(self):
        """_try_mint_credential skips mint when has_phg_credential returns True."""
        self.chain.has_phg_credential = AsyncMock(return_value=True)
        _seed_pitl_proof(self.store, _DEVICE_A, NOMINAL, hp_int=800)
        self._run(self.em._try_mint_credential(_DEVICE_A))
        self.chain.mint_phg_credential.assert_not_called()
        row = self.store.get_enrollment(_DEVICE_A)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "credentialed")


# ===========================================================================
# TestEnrollmentEndpoint
# ===========================================================================

class TestEnrollmentEndpoint(unittest.TestCase):
    """Test the GET /enrollment/status/{device_id} HTTP endpoint."""

    def setUp(self):
        try:
            from fastapi.testclient import TestClient
            from vapi_bridge.transports.http import create_app
            self.store = _make_store()
            cfg = MagicMock()
            cfg.enrollment_min_sessions = 10
            cfg.enrollment_humanity_min = 0.60
            # Minimal cfg attrs required by create_app
            for attr in [
                "http_host", "http_port", "mqtt_enabled", "coap_enabled",
                "dualshock_enabled", "rate_limit_per_minute",
                "operator_api_key", "log_level",
            ]:
                setattr(cfg, attr, MagicMock())
            cfg.operator_api_key = ""
            cfg.rate_limit_per_minute = 60

            app = create_app(cfg, self.store, AsyncMock())
            self.client = TestClient(app)
            self._available = True
        except Exception:
            self._available = False

    def _skip_if_unavailable(self):
        if not self._available:
            self.skipTest("FastAPI TestClient unavailable")

    def test_11_enrollment_status_returns_pending_for_new_device(self):
        """GET /enrollment/status/{device_id} returns pending for unknown device."""
        self._skip_if_unavailable()
        resp = self.client.get(f"/enrollment/status/{_DEVICE_A}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["device_id"], _DEVICE_A)
        self.assertIn("required_sessions", data)

    def test_12_enrollment_status_returns_progress_after_upsert(self):
        """Enrollment endpoint reflects upserted enrollment state."""
        self._skip_if_unavailable()
        self.store.upsert_enrollment(_DEVICE_A, 7, 20, 0.72, "pending")
        resp = self.client.get(f"/enrollment/status/{_DEVICE_A}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["sessions_nominal"], 7)
        self.assertAlmostEqual(data["avg_humanity"], 0.72, places=2)

    def test_13_enrollment_status_returns_credentialed_after_mint(self):
        """Endpoint returns credentialed status after credential is minted."""
        self._skip_if_unavailable()
        self.store.upsert_enrollment(_DEVICE_A, 10, 20, 0.85, "credentialed", "0xabc123")
        resp = self.client.get(f"/enrollment/status/{_DEVICE_A}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "credentialed")
        self.assertEqual(data["tx_hash"], "0xabc123")


# ===========================================================================
# TestEnrollmentBridgeAgent
# ===========================================================================

class TestEnrollmentBridgeAgent(unittest.TestCase):
    """Test BridgeAgent tool #30 get_enrollment_status."""

    def setUp(self):
        try:
            from vapi_bridge.bridge_agent import BridgeAgent
            self.store = _make_store()
            cfg = MagicMock()
            cfg.enrollment_min_sessions = 10
            cfg.agent_max_history_before_compress = 60
            self.agent = BridgeAgent(cfg, self.store)
            self._available = True
        except Exception:
            self._available = False

    def _skip_if_unavailable(self):
        if not self._available:
            self.skipTest("BridgeAgent unavailable")

    def test_14_get_enrollment_status_tool_returns_dict(self):
        """Tool #30 returns a dict with device_id and status keys."""
        self._skip_if_unavailable()
        result = self.agent._execute_tool("get_enrollment_status", {"device_id": _DEVICE_A})
        self.assertIsInstance(result, dict)
        self.assertIn("status", result)
        self.assertIn("device_id", result)

    def test_15_get_enrollment_status_tool_includes_sessions_needed(self):
        """Tool #30 includes sessions_needed field for progress tracking."""
        self._skip_if_unavailable()
        self.store.upsert_enrollment(_DEVICE_A, 3, 5, 0.70, "pending")
        result = self.agent._execute_tool("get_enrollment_status", {"device_id": _DEVICE_A})
        self.assertIn("sessions_needed", result)
        # min_sessions=10, nominal=3 -> needed=7
        self.assertEqual(result["sessions_needed"], 7)

    def test_16_get_enrollment_status_tool_handles_no_row(self):
        """Tool #30 returns pending dict when device has no enrollment row."""
        self._skip_if_unavailable()
        result = self.agent._execute_tool("get_enrollment_status", {"device_id": _DEVICE_B})
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["device_id"], _DEVICE_B)
        self.assertGreaterEqual(result["sessions_needed"], 0)


if __name__ == "__main__":
    unittest.main()
