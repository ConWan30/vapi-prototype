"""
Phase 37 — BridgeAgent get_credential_status tool tests.

4 tests covering:
1. get_credential_status returns is_active=True for device with credential, not suspended
2. Returns is_active=False + suspended=True for suspended device
3. Returns has_credential=False for device without credential
4. Returns error when device_id is missing
"""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.store import Store
from vapi_bridge.bridge_agent import BridgeAgent


def _fresh_store() -> Store:
    db_dir = tempfile.mkdtemp()
    return Store(os.path.join(db_dir, "test.db"))


def _make_agent(store):
    cfg = MagicMock()
    cfg.anthropic_api_key = "test"
    cfg.phg_credential_enforcement_enabled = True
    cfg.agent_max_history_before_compress = 60
    return BridgeAgent(cfg, store)


class TestEnforcementAgentTool(unittest.TestCase):

    def test_1_active_credential_returns_is_active_true(self):
        """get_credential_status returns is_active=True for device with credential, not suspended."""
        store = _fresh_store()
        dev = "aa" * 32
        with store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO phg_credential_mints"
                " (device_id, credential_id, minted_at)"
                " VALUES (?, 1, ?)",
                (dev, time.time()),
            )
        agent = _make_agent(store)
        result = agent._execute_tool("get_credential_status", {"device_id": dev})
        self.assertTrue(result["has_credential"])
        self.assertTrue(result["is_active"])
        self.assertFalse(result["suspended"])

    def test_2_suspended_credential_returns_is_active_false(self):
        """get_credential_status returns is_active=False + suspended=True for suspended device."""
        store = _fresh_store()
        dev = "bb" * 32
        with store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO phg_credential_mints"
                " (device_id, credential_id, minted_at)"
                " VALUES (?, 1, ?)",
                (dev, time.time()),
            )
        store.store_credential_suspension(dev, "aabbccdd" * 8, time.time() + 86400)
        agent = _make_agent(store)
        result = agent._execute_tool("get_credential_status", {"device_id": dev})
        self.assertTrue(result["has_credential"])
        self.assertFalse(result["is_active"])
        self.assertTrue(result["suspended"])

    def test_3_no_credential_returns_has_credential_false(self):
        """get_credential_status returns has_credential=False for device without credential."""
        store = _fresh_store()
        dev = "cc" * 32
        agent = _make_agent(store)
        result = agent._execute_tool("get_credential_status", {"device_id": dev})
        self.assertFalse(result["has_credential"])
        self.assertFalse(result["is_active"])

    def test_4_missing_device_id_returns_error(self):
        """get_credential_status returns error dict when device_id is missing."""
        store = _fresh_store()
        agent = _make_agent(store)
        result = agent._execute_tool("get_credential_status", {})
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
