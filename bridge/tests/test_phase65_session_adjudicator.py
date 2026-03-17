"""
Phase 65 -- Autonomous Intelligence Layer Tests (20 tests)

TestAgentRulingsStore (8 tests):
  test_1_insert_agent_ruling_returns_id
  test_2_get_agent_rulings_empty_returns_empty_list
  test_3_get_agent_rulings_returns_most_recent_first
  test_4_commitment_hash_stored_and_retrieved
  test_5_dry_run_default_is_1
  test_6_get_agent_ruling_by_id_found
  test_7_get_agent_ruling_by_id_missing_returns_none
  test_8_get_agent_rulings_verdict_filter

TestSessionAdjudicator (6 tests):
  test_9_session_adjudicator_init
  test_10_consume_empty_events_no_rulings_created
  test_11_consume_ruling_request_event_creates_ruling
  test_12_rule_fallback_hard_cheat_produces_block
  test_13_rule_fallback_eligible_produces_certify
  test_14_rule_fallback_no_signals_produces_flag

TestAdjudicateEndpoints (4 tests):
  test_15_post_agent_adjudicate_queues_event
  test_16_post_agent_adjudicate_missing_device_id_400
  test_17_get_agent_rulings_empty_returns_count_zero
  test_18_get_agent_rulings_returns_inserted_ruling

TestBridgeAgentTools3233 (2 tests):
  test_19_tool32_get_autonomous_rulings_calls_store
  test_20_tool33_request_adjudication_writes_event
"""

import asyncio
import sys
import tempfile
import types
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

_DEVICE_A = "aa" * 32
_DEVICE_B = "bb" * 32
_COMMITMENT = "deadbeef" * 8  # 64 hex chars


def _make_store() -> Store:
    td = tempfile.mkdtemp()
    return Store(str(Path(td) / "test_p65.db"))


def _make_cfg():
    cfg = MagicMock()
    cfg.enrollment_min_sessions = 10
    cfg.enrollment_humanity_min = 0.60
    cfg.l6b_enabled = False
    cfg.agent_max_history_before_compress = 60
    return cfg


# ===========================================================================
# TestAgentRulingsStore — 8 tests
# ===========================================================================

class TestAgentRulingsStore(unittest.TestCase):

    def setUp(self):
        self.store = _make_store()
        # Register device so FK constraint passes
        self.store.upsert_device(_DEVICE_A, "aa" * 32)
        self.store.upsert_device(_DEVICE_B, "bb" * 32)

    def test_1_insert_agent_ruling_returns_id(self):
        """insert_agent_ruling returns a positive integer ruling id."""
        rid = self.store.insert_agent_ruling(
            device_id=_DEVICE_A,
            verdict="FLAG",
            confidence=0.05,
            reasoning="No anomalies detected.",
            evidence_json="{}",
            commitment_hash=_COMMITMENT,
        )
        self.assertIsInstance(rid, int)
        self.assertGreater(rid, 0)

    def test_2_get_agent_rulings_empty_returns_empty_list(self):
        """get_agent_rulings returns [] when no rulings exist for device."""
        result = self.store.get_agent_rulings(_DEVICE_A)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_3_get_agent_rulings_returns_most_recent_first(self):
        """get_agent_rulings returns rows in descending created_at order."""
        import time
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="FLAG", confidence=0.1,
            reasoning="first", evidence_json="{}", commitment_hash="aa" * 32,
        )
        time.sleep(0.01)
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="BLOCK", confidence=0.95,
            reasoning="second", evidence_json="{}", commitment_hash="bb" * 32,
        )
        rows = self.store.get_agent_rulings(_DEVICE_A)
        self.assertEqual(len(rows), 2)
        # Most recent (BLOCK) first
        self.assertEqual(rows[0]["verdict"], "BLOCK")
        self.assertEqual(rows[1]["verdict"], "FLAG")

    def test_4_commitment_hash_stored_and_retrieved(self):
        """commitment_hash is preserved exactly through insert/retrieve round-trip."""
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="HOLD", confidence=0.75,
            reasoning="chain tampered", evidence_json='{"test": 1}',
            commitment_hash=_COMMITMENT,
        )
        rows = self.store.get_agent_rulings(_DEVICE_A)
        self.assertEqual(rows[0]["commitment_hash"], _COMMITMENT)

    def test_5_dry_run_default_is_1(self):
        """dry_run defaults to True (stored as integer 1) when not specified."""
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="FLAG", confidence=0.05,
            reasoning="test", evidence_json="{}", commitment_hash=_COMMITMENT,
        )
        rows = self.store.get_agent_rulings(_DEVICE_A)
        self.assertEqual(rows[0]["dry_run"], 1)

    def test_6_get_agent_ruling_by_id_found(self):
        """get_agent_ruling_by_id returns correct row when id exists."""
        rid = self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="CERTIFY", confidence=0.85,
            reasoning="enrollment threshold met", evidence_json="{}",
            commitment_hash=_COMMITMENT,
        )
        row = self.store.get_agent_ruling_by_id(rid)
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], rid)
        self.assertEqual(row["verdict"], "CERTIFY")
        self.assertAlmostEqual(row["confidence"], 0.85, places=2)

    def test_7_get_agent_ruling_by_id_missing_returns_none(self):
        """get_agent_ruling_by_id returns None for non-existent id."""
        result = self.store.get_agent_ruling_by_id(99999)
        self.assertIsNone(result)

    def test_8_get_agent_rulings_verdict_filter(self):
        """get_agent_rulings filters by verdict when verdict_filter is set."""
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="FLAG", confidence=0.1,
            reasoning="advisory", evidence_json="{}", commitment_hash="aa" * 32,
        )
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="BLOCK", confidence=0.95,
            reasoning="hard cheat", evidence_json="{}", commitment_hash="bb" * 32,
        )
        flags = self.store.get_agent_rulings(_DEVICE_A, verdict_filter="FLAG")
        blocks = self.store.get_agent_rulings(_DEVICE_A, verdict_filter="BLOCK")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["verdict"], "FLAG")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["verdict"], "BLOCK")


# ===========================================================================
# TestSessionAdjudicator — 6 tests
# ===========================================================================

class TestSessionAdjudicator(unittest.TestCase):

    def setUp(self):
        try:
            from vapi_bridge.session_adjudicator import SessionAdjudicator
            self.SessionAdjudicator = SessionAdjudicator
            self.store = _make_store()
            self._available = True
        except Exception:
            self._available = False

    def _skip_if_unavailable(self):
        if not self._available:
            self.skipTest("SessionAdjudicator unavailable")

    def test_9_session_adjudicator_init(self):
        """SessionAdjudicator initialises without error."""
        self._skip_if_unavailable()
        adj = self.SessionAdjudicator(_make_cfg(), self.store)
        self.assertIsNotNone(adj)

    def test_10_consume_empty_events_no_rulings_created(self):
        """_consume_pending_events does nothing when agent_events queue is empty."""
        self._skip_if_unavailable()
        adj = self.SessionAdjudicator(_make_cfg(), self.store)
        asyncio.get_event_loop().run_until_complete(adj._consume_pending_events())
        rulings = self.store.get_agent_rulings(_DEVICE_A)
        self.assertEqual(len(rulings), 0)

    def test_11_consume_ruling_request_event_creates_ruling(self):
        """_process_ruling_request writes an agent_ruling row (LLM mocked)."""
        self._skip_if_unavailable()
        # Register device so FK constraint passes
        self.store.upsert_device(_DEVICE_A, "aa" * 32)
        # Write a ruling_request event to the store
        self.store.write_agent_event(
            event_type="ruling_request",
            payload='{"device_id": "' + _DEVICE_A + '", "attestation_hash": ""}',
            source="test",
            target="session_adjudicator",
            device_id=_DEVICE_A,
        )
        events = self.store.read_unconsumed_events("session_adjudicator", limit=5)
        self.assertEqual(len(events), 1)

        adj = self.SessionAdjudicator(_make_cfg(), self.store)
        # Patch _llm_ruling to return rule fallback immediately
        async def _mock_llm(evidence):
            return "FLAG", 0.05, "No anomalies (mocked LLM)."

        adj._llm_ruling = _mock_llm
        asyncio.get_event_loop().run_until_complete(
            adj._process_ruling_request(events[0])
        )
        rulings = self.store.get_agent_rulings(_DEVICE_A)
        self.assertEqual(len(rulings), 1)
        self.assertEqual(rulings[0]["verdict"], "FLAG")

    def test_12_rule_fallback_hard_cheat_produces_block(self):
        """_rule_fallback returns BLOCK when hard_cheat_codes is non-empty."""
        self._skip_if_unavailable()
        from vapi_bridge.session_adjudicator import SessionAdjudicator
        verdict, conf, reasoning = SessionAdjudicator._rule_fallback({
            "hard_cheat_codes": [0x28],
            "advisory_codes": [],
            "enrollment_status": "pending",
            "risk_label": "stable",
        })
        self.assertEqual(verdict, "BLOCK")
        self.assertGreaterEqual(conf, 0.85)

    def test_13_rule_fallback_eligible_produces_certify(self):
        """_rule_fallback returns CERTIFY when enrollment_status='eligible'."""
        self._skip_if_unavailable()
        from vapi_bridge.session_adjudicator import SessionAdjudicator
        verdict, conf, reasoning = SessionAdjudicator._rule_fallback({
            "hard_cheat_codes": [],
            "advisory_codes": [],
            "enrollment_status": "eligible",
            "risk_label": "stable",
        })
        self.assertEqual(verdict, "CERTIFY")
        self.assertGreaterEqual(conf, 0.7)

    def test_14_rule_fallback_no_signals_produces_flag(self):
        """_rule_fallback returns FLAG with low confidence when no signals."""
        self._skip_if_unavailable()
        from vapi_bridge.session_adjudicator import SessionAdjudicator
        verdict, conf, reasoning = SessionAdjudicator._rule_fallback({
            "hard_cheat_codes": [],
            "advisory_codes": [],
            "enrollment_status": "pending",
            "risk_label": "stable",
        })
        self.assertEqual(verdict, "FLAG")
        self.assertLess(conf, 0.2)


# ===========================================================================
# TestAdjudicateEndpoints — 4 tests
# ===========================================================================

class TestAdjudicateEndpoints(unittest.TestCase):

    def setUp(self):
        try:
            from fastapi.testclient import TestClient
            from vapi_bridge.transports.http import create_app
            self.store = _make_store()
            cfg = _make_cfg()
            cfg.operator_api_key = "testkey65"
            cfg.rate_limit_per_minute = 100
            app = create_app(cfg, self.store, AsyncMock())
            self.client = TestClient(app)
            self._available = True
        except Exception:
            self._available = False

    def _skip_if_unavailable(self):
        if not self._available:
            self.skipTest("FastAPI TestClient unavailable")

    def test_15_post_agent_adjudicate_queues_event(self):
        """POST /agent/adjudicate creates a ruling_request event and returns queued."""
        self._skip_if_unavailable()
        # Device must exist in devices table first
        self.store.upsert_device(_DEVICE_A, "aa" * 32)
        resp = self.client.post("/agent/adjudicate", json={
            "device_id": _DEVICE_A,
            "attestation_hash": "cc" * 32,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "queued")
        self.assertIn("event_id", data)
        self.assertEqual(data["device_id"], _DEVICE_A)

    def test_16_post_agent_adjudicate_missing_device_id_400(self):
        """POST /agent/adjudicate returns 400 when device_id is missing."""
        self._skip_if_unavailable()
        resp = self.client.post("/agent/adjudicate", json={})
        self.assertEqual(resp.status_code, 400)

    def test_17_get_agent_rulings_empty_returns_count_zero(self):
        """GET /agent/rulings/{device_id} returns count=0 for fresh device."""
        self._skip_if_unavailable()
        resp = self.client.get(f"/agent/rulings/{_DEVICE_A}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["device_id"], _DEVICE_A)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["rulings"], [])

    def test_18_get_agent_rulings_returns_inserted_ruling(self):
        """GET /agent/rulings/{device_id} returns previously inserted ruling."""
        self._skip_if_unavailable()
        self.store.upsert_device(_DEVICE_A, "aa" * 32)
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="HOLD", confidence=0.75,
            reasoning="chain issue", evidence_json="{}",
            commitment_hash=_COMMITMENT,
        )
        resp = self.client.get(f"/agent/rulings/{_DEVICE_A}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["rulings"][0]["verdict"], "HOLD")


# ===========================================================================
# TestBridgeAgentTools3233 — 2 tests
# ===========================================================================

class TestBridgeAgentTools3233(unittest.TestCase):

    def setUp(self):
        try:
            # Stub anthropic before BridgeAgent import
            _ant = sys.modules.get("anthropic") or types.ModuleType("anthropic")
            _ant.Anthropic = MagicMock()
            _ant.AsyncAnthropic = MagicMock()
            sys.modules["anthropic"] = _ant
            _ant_types = types.ModuleType("anthropic.types")
            _ant.types = _ant_types
            sys.modules["anthropic.types"] = _ant_types

            from vapi_bridge.bridge_agent import BridgeAgent
            self.store = _make_store()
            self.agent = BridgeAgent(_make_cfg(), self.store)
            self._available = True
        except Exception:
            self._available = False

    def _skip_if_unavailable(self):
        if not self._available:
            self.skipTest("BridgeAgent unavailable")

    def test_19_tool32_get_autonomous_rulings_calls_store(self):
        """Tool #32 get_autonomous_rulings returns rulings list for device."""
        self._skip_if_unavailable()
        self.store.upsert_device(_DEVICE_A, "aa" * 32)
        self.store.insert_agent_ruling(
            device_id=_DEVICE_A, verdict="FLAG", confidence=0.05,
            reasoning="clean session", evidence_json="{}",
            commitment_hash=_COMMITMENT,
        )
        result = self.agent._execute_tool("get_autonomous_rulings",
                                          {"device_id": _DEVICE_A})
        self.assertIn("rulings", result)
        self.assertIn("device_id", result)
        self.assertEqual(len(result["rulings"]), 1)
        self.assertEqual(result["rulings"][0]["verdict"], "FLAG")

    def test_20_tool33_request_adjudication_writes_event(self):
        """Tool #33 request_adjudication returns queued status with event_id."""
        self._skip_if_unavailable()
        result = self.agent._execute_tool("request_adjudication", {
            "device_id": _DEVICE_B,
            "attestation_hash": "dd" * 32,
            "reason": "manual review requested",
        })
        self.assertEqual(result["status"], "queued")
        self.assertIn("event_id", result)
        # Confirm the event was written
        events = self.store.read_unconsumed_events("session_adjudicator", limit=5)
        self.assertGreater(len(events), 0)
        self.assertEqual(events[0]["event_type"], "ruling_request")


if __name__ == "__main__":
    unittest.main()
