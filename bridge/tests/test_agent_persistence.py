"""
Phase 31 — BridgeAgent Session Persistence Tests

TestAgentSessionPersistence (4):
1.  store.store_agent_session() → stored; store.get_agent_session() retrieves same list
2.  store.get_agent_session() on unknown session_id → returns []
3.  Cross-instance persistence: history survives a new Store() on the same db
4.  BridgeAgent._save_history() calls store; fresh BridgeAgent._load_history() reads it back
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

from vapi_bridge.store import Store
from vapi_bridge.bridge_agent import BridgeAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_store() -> Store:
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


class _FakeConfig:
    operator_api_key = "testkey31"
    iotex_rpc_url = "http://127.0.0.1:8545"
    phg_credential_address = ""


_SAMPLE_HISTORY = [
    {"role": "user", "content": "What is the leaderboard?"},
    {"role": "assistant", "content": [{"type": "text", "text": "Here is the leaderboard."}]},
]


# ===========================================================================
# TestAgentSessionPersistence
# ===========================================================================


class TestAgentSessionPersistence(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()

    def test_1_store_and_retrieve_session(self):
        """store.store_agent_session() → stored; get_agent_session() retrieves same list."""
        self.store.store_agent_session("sess_abc", _SAMPLE_HISTORY)
        result = self.store.get_agent_session("sess_abc")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), len(_SAMPLE_HISTORY))
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[1]["role"], "assistant")

    def test_2_unknown_session_returns_empty_list(self):
        """get_agent_session() on unknown session_id → returns []."""
        result = self.store.get_agent_session("nonexistent_session_xyz")
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_3_cross_instance_persistence(self):
        """History survives a new Store() opened on the same database file."""
        db_path = self.store._db_path
        self.store.store_agent_session("sess_persist", _SAMPLE_HISTORY)

        # Open a brand-new Store instance on the same file
        store2 = Store(db_path)
        result = store2.get_agent_session("sess_persist")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), len(_SAMPLE_HISTORY))
        self.assertEqual(result[0]["content"], _SAMPLE_HISTORY[0]["content"])

    def test_4_bridge_agent_save_load_history(self):
        """BridgeAgent._save_history() persists; fresh agent _load_history() reads it back."""
        cfg = _FakeConfig()
        agent1 = BridgeAgent(cfg, self.store)
        agent1._save_history("sess_agent", _SAMPLE_HISTORY)

        # Create a second agent with the SAME store (simulates bridge restart)
        agent2 = BridgeAgent(cfg, self.store)
        loaded = agent2._load_history("sess_agent")
        self.assertIsInstance(loaded, list)
        self.assertEqual(len(loaded), len(_SAMPLE_HISTORY))
        self.assertEqual(loaded[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
