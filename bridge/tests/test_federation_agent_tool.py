"""
Tests for get_federation_status BridgeAgent tool — Phase 34

4 tests covering:
1. No peers configured → peer_count=0, federation_enabled=False
2. 2 peers configured → peer_count=2, federation_enabled=True
3. cross_confirmed_hashes reflects store.get_cross_confirmed_hashes()
4. Store raises Exception → tool returns gracefully (no crash)
"""
import sys
import os
import types
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub anthropic before importing bridge_agent
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")

from vapi_bridge.bridge_agent import BridgeAgent


def _agent(peers="", confirmed=None):
    cfg = MagicMock()
    cfg.federation_peers = peers
    store = MagicMock()
    store.get_cross_confirmed_hashes.return_value = confirmed or []
    store.get_federation_clusters.return_value = []
    return BridgeAgent(cfg, store)


class TestFederationAgentTool(unittest.TestCase):

    def test_1_no_peers_returns_federation_disabled(self):
        """No peers configured → peer_count=0, federation_enabled=False."""
        agent = _agent(peers="")
        result = agent._execute_tool("get_federation_status", {})

        assert result["peer_count"] == 0
        assert result["federation_enabled"] is False
        assert result["cross_confirmed_count"] == 0

    def test_2_two_peers_returns_peer_count_2_and_enabled(self):
        """2 peers configured → peer_count=2, federation_enabled=True."""
        agent = _agent(peers="http://peer1:8000,http://peer2:8000")
        result = agent._execute_tool("get_federation_status", {})

        assert result["peer_count"] == 2
        assert result["federation_enabled"] is True
        assert len(result["peers_configured"]) == 2

    def test_3_cross_confirmed_hashes_reflects_store(self):
        """cross_confirmed_hashes reflects store.get_cross_confirmed_hashes()."""
        agent = _agent(confirmed=["hash_aabb1234", "hash_ccdd5678"])
        result = agent._execute_tool("get_federation_status", {})

        assert result["cross_confirmed_count"] == 2
        assert "hash_aabb1234" in result["cross_confirmed_hashes"]
        assert "hash_ccdd5678" in result["cross_confirmed_hashes"]

    def test_4_store_exception_does_not_crash_tool(self):
        """Store raises RuntimeError → tool returns gracefully with empty fallbacks."""
        cfg = MagicMock()
        cfg.federation_peers = "http://peer:8000"
        store = MagicMock()
        store.get_cross_confirmed_hashes.side_effect = RuntimeError("DB connection error")
        store.get_federation_clusters.side_effect = RuntimeError("DB connection error")

        agent = BridgeAgent(cfg, store)
        result = agent._execute_tool("get_federation_status", {})

        # Should not raise; fallback to empty
        assert result["cross_confirmed_count"] == 0
        assert result["cross_confirmed_hashes"] == []
        assert result["local_clusters_detected"] == 0
        assert result["remote_clusters_received"] == 0


if __name__ == "__main__":
    unittest.main()
