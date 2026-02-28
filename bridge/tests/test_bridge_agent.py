"""
Phase 30 — BridgeAgent Tests

TestBridgeAgentTools (4):
1.  _execute_tool("get_player_profile") returns dict (or error dict for unknown device)
2.  _execute_tool("get_leaderboard") returns list
3.  _execute_tool("run_pitl_calibration") returns {"output": str}
4.  _execute_tool("get_startup_diagnostics") returns dict with "zk_artifacts" key

TestBridgeAgentEndpoint (4):
5.  POST /operator/agent without api_key → 422
6.  POST /operator/agent with wrong api_key → 403
7.  POST /operator/agent when anthropic raises ImportError → 503
8.  POST /operator/agent with mock agent → 200 with "response" field
"""

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy deps before any bridge import
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from fastapi.testclient import TestClient

from vapi_bridge.store import Store
from vapi_bridge.bridge_agent import BridgeAgent
from vapi_bridge.operator_api import create_operator_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEVICE_HEX = "ab" * 32  # 64-char hex


def _fresh_store() -> Store:
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


def _insert_pitl_row(store: Store, l4_dist: float, humanity_prob: float, seq: int = 0):
    record_hash_hex = f"{seq:062x}cc"
    with store._conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?)",
            (_DEVICE_HEX, "pubkey_ab", time.time(), time.time()),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO records
                (record_hash, device_id, inference, confidence, action_code,
                 counter, battery_pct, timestamp_ms, created_at,
                 pitl_l4_distance, pitl_humanity_prob)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_hash_hex,
                _DEVICE_HEX,
                0x20,
                200,
                0x01,
                seq,
                80,
                int(time.time() * 1000) + seq,
                time.time(),
                l4_dist,
                humanity_prob,
            ),
        )


class _FakeConfig:
    """Minimal config stub for tests."""
    operator_api_key = "testkey30"
    iotex_rpc_url = "http://127.0.0.1:8545"
    phg_credential_address = ""


class _MockAgent:
    """Mock BridgeAgent that returns a fixed response."""

    def ask(self, session_id: str, message: str) -> dict:
        return {
            "session_id": session_id,
            "response": f"Mock answer to: {message}",
            "tools_used": [],
        }


class _NoAnthropicAgent:
    """Mock BridgeAgent that raises ImportError (simulates missing package)."""

    def ask(self, session_id: str, message: str) -> dict:
        raise ImportError("No module named 'anthropic'")


# ===========================================================================
# TestBridgeAgentTools
# ===========================================================================

class TestBridgeAgentTools(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        self.cfg = _FakeConfig()
        self.agent = BridgeAgent(self.cfg, self.store)

    def test_1_get_player_profile_unknown_device(self):
        """_execute_tool('get_player_profile') returns dict (error for unknown device)."""
        result = self.agent._execute_tool("get_player_profile", {"device_id": "aa" * 32})
        self.assertIsInstance(result, dict)
        # Either a profile dict or an error dict — must be a dict
        self.assertTrue(isinstance(result, dict))

    def test_2_get_leaderboard_returns_list(self):
        """_execute_tool('get_leaderboard') returns a list."""
        result = self.agent._execute_tool("get_leaderboard", {"limit": 5})
        self.assertIsInstance(result, list)

    def test_3_run_pitl_calibration_returns_output(self):
        """_execute_tool('run_pitl_calibration') returns dict with 'output' key."""
        _insert_pitl_row(self.store, 1.5, 0.8, seq=0)
        _insert_pitl_row(self.store, 2.0, 0.9, seq=1)
        result = self.agent._execute_tool("run_pitl_calibration", {})
        self.assertIsInstance(result, dict)
        self.assertIn("output", result)
        self.assertIsInstance(result["output"], str)

    def test_4_get_startup_diagnostics_has_zk_artifacts(self):
        """_execute_tool('get_startup_diagnostics') returns dict with 'zk_artifacts' key."""
        result = self.agent._execute_tool("get_startup_diagnostics", {})
        self.assertIsInstance(result, dict)
        self.assertIn("zk_artifacts", result)
        self.assertIn("TeamProof", result["zk_artifacts"])
        self.assertIn("PitlSessionProof", result["zk_artifacts"])
        # ZK artifacts are booleans (may be False in test env — that's fine)
        self.assertIsInstance(result["zk_artifacts"]["TeamProof"], bool)


# ===========================================================================
# TestBridgeAgentEndpoint
# ===========================================================================

class TestBridgeAgentEndpoint(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        self.cfg = _FakeConfig()

    def _client(self, agent=None) -> TestClient:
        return TestClient(create_operator_app(self.cfg, self.store, _agent=agent))

    def test_5_missing_api_key_returns_422(self):
        """POST /agent without api_key → 422 (FastAPI validation error)."""
        client = self._client(_MockAgent())
        resp = client.post("/agent", json={"session_id": "s1", "message": "hi"})
        self.assertEqual(resp.status_code, 422)

    def test_6_wrong_api_key_returns_403(self):
        """POST /agent with wrong api_key → 403."""
        client = self._client(_MockAgent())
        resp = client.post(
            "/agent",
            json={"session_id": "s1", "message": "hi"},
            params={"api_key": "wrongkey"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_7_anthropic_missing_returns_503(self):
        """POST /agent when anthropic raises ImportError → 503."""
        client = self._client(_NoAnthropicAgent())
        resp = client.post(
            "/agent",
            json={"session_id": "s1", "message": "hi"},
            params={"api_key": "testkey30"},
        )
        self.assertEqual(resp.status_code, 503)

    def test_8_mock_agent_returns_200_with_response(self):
        """POST /agent with mock agent → 200 with 'response' field."""
        client = self._client(_MockAgent())
        resp = client.post(
            "/agent",
            json={"session_id": "s1", "message": "What is the leaderboard?"},
            params={"api_key": "testkey30"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("response", body)
        self.assertIn("session_id", body)
        self.assertEqual(body["session_id"], "s1")
        self.assertIn("What is the leaderboard?", body["response"])


if __name__ == "__main__":
    unittest.main()
