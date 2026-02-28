"""
Tests for adaptive L4 detection policy logic — Phase 36

4 tests covering:
1. Store.get_detection_policy returns active policy with correct multiplier
2. Store.get_detection_policy returns None for expired policy
3. No policy in store → no adaptive change (default behavior preserved)
4. get_detection_policy BridgeAgent tool returns correct dict with multiplier + basis_label
"""
import sys
import os
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub anthropic before importing bridge_agent
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")


def _fresh_store():
    from vapi_bridge.store import Store
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return Store(f.name)


class TestStorePolicyMethods(unittest.TestCase):

    def test_1_active_policy_returned_with_correct_multiplier(self):
        """store.get_detection_policy returns policy with multiplier when not expired."""
        store = _fresh_store()
        device_id = "dev_policy_aa11"
        expires_at = time.time() + 3600.0

        store.store_detection_policy(
            device_id=device_id,
            multiplier=0.70,
            basis_label="critical",
            expires_at=expires_at,
        )

        policy = store.get_detection_policy(device_id)
        assert policy is not None, "Expected active policy, got None"
        assert abs(policy["multiplier"] - 0.70) < 0.001
        assert policy["basis_label"] == "critical"
        assert policy["device_id"] == device_id

    def test_2_expired_policy_returns_none(self):
        """store.get_detection_policy returns None when policy has expired."""
        store = _fresh_store()
        device_id = "dev_expired_bb22"
        expires_at = time.time() - 1.0  # already expired

        store.store_detection_policy(
            device_id=device_id,
            multiplier=0.70,
            basis_label="critical",
            expires_at=expires_at,
        )

        policy = store.get_detection_policy(device_id)
        assert policy is None, f"Expected None for expired policy, got {policy}"

    def test_3_no_policy_returns_none(self):
        """store.get_detection_policy returns None when no policy stored for device."""
        store = _fresh_store()
        policy = store.get_detection_policy("dev_no_policy_cc33")
        assert policy is None

    def test_4_get_detection_policy_tool_returns_correct_structure(self):
        """BridgeAgent.get_detection_policy tool returns policies + metadata."""
        from unittest.mock import MagicMock
        from vapi_bridge.bridge_agent import BridgeAgent

        device_id = "dev_tool_dd44"
        policy = {
            "device_id": device_id,
            "multiplier": 0.70,
            "basis_label": "critical",
            "set_at": time.time(),
            "expires_at": time.time() + 3600.0,
        }

        cfg = MagicMock()
        cfg.federation_peers = ""
        cfg.adaptive_thresholds_enabled = True
        store = MagicMock()
        store.get_detection_policy.return_value = policy
        store.get_all_active_policies.return_value = [policy]
        store.get_federation_clusters.return_value = []

        agent = BridgeAgent(cfg, store)

        # Query by specific device_id
        result = agent._execute_tool("get_detection_policy", {"device_id": device_id})
        assert "policies" in result
        assert result["total_count"] == 1
        assert result["policies"][0]["multiplier"] == 0.70
        assert result["critical_policy_multiplier"] == 0.70
        assert result["warming_policy_multiplier"] == 0.85

        # Query all active policies
        result_all = agent._execute_tool("get_detection_policy", {})
        assert result_all["total_count"] >= 1
        store.get_all_active_policies.assert_called()

        # Query with risk_filter=critical — should filter by basis_label
        result_crit = agent._execute_tool(
            "get_detection_policy", {"risk_filter": "critical"}
        )
        for p in result_crit["policies"]:
            assert p["basis_label"] == "critical"


if __name__ == "__main__":
    unittest.main()
