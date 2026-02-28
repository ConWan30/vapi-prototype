"""
Tests for query_digest BridgeAgent tool — Phase 35

4 tests covering:
1. No digests stored → synthesis_available=False
2. window="24h" returns only 24h digest
3. include_device_labels=True → device_labels key in result
4. risk_filter="critical" → get_devices_by_risk_label("critical") called
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


def _agent(digests=None, label_rows=None):
    cfg = MagicMock()
    cfg.federation_peers = ""
    store = MagicMock()
    store.get_all_latest_digests.return_value = digests or []
    store.get_latest_digest.return_value = (digests[0] if digests else None)
    store.get_devices_by_risk_label.return_value = label_rows or []
    store.get_federation_clusters.return_value = []
    return BridgeAgent(cfg, store)


class TestDigestAgentTool(unittest.TestCase):

    def test_1_no_digests_returns_synthesis_unavailable(self):
        """No digests stored → synthesis_available=False, digests=[]."""
        agent = _agent(digests=[])
        result = agent._execute_tool("query_digest", {})

        assert result["synthesis_available"] is False
        assert result["digests"] == []

    def test_2_window_24h_returns_single_digest(self):
        """window='24h' calls get_latest_digest('24h') and wraps result."""
        digest_24h = {
            "id": 1, "window_label": "24h", "synthesized_at": 1700000000.0,
            "bot_farm_count": 1, "high_risk_count": 0, "federated_count": 0,
            "anomaly_count": 0, "eligible_count": 0, "dominant_severity": "medium",
            "top_devices": ["dev_aabb"], "narrative": "24h: 1 bot-farm alert.",
        }
        agent = _agent(digests=[digest_24h])
        result = agent._execute_tool("query_digest", {"window": "24h"})

        assert result["synthesis_available"] is True
        assert len(result["digests"]) == 1
        assert result["digests"][0]["window_label"] == "24h"
        agent._store.get_latest_digest.assert_called_once_with("24h")

    def test_3_include_device_labels_true_returns_device_labels(self):
        """include_device_labels=True → device_labels key present in result."""
        label = {
            "device_id": "dev_ccdd5678", "risk_label": "warming",
            "label_evidence": {"bot": 0, "high_risk": 1, "fed": 0, "anomaly": 2},
            "label_set_at": 1700000001.0, "prior_label": "stable",
        }
        agent = _agent(digests=[], label_rows=[label])
        result = agent._execute_tool("query_digest", {"include_device_labels": True})

        assert "device_labels" in result
        assert len(result["device_labels"]) >= 1
        assert "critical_device_count" in result
        assert "warming_device_count" in result

    def test_4_risk_filter_critical_calls_get_devices_by_risk_label(self):
        """risk_filter='critical' → get_devices_by_risk_label('critical') called."""
        crit_label = {
            "device_id": "dev_crit_aaaa", "risk_label": "critical",
            "label_evidence": {"bot": 2, "high_risk": 0, "fed": 0, "anomaly": 0},
            "label_set_at": 1700000002.0, "prior_label": "warming",
        }
        agent = _agent(digests=[], label_rows=[crit_label])
        result = agent._execute_tool("query_digest", {
            "include_device_labels": True,
            "risk_filter": "critical",
        })

        assert "device_labels" in result
        # Must have called get_devices_by_risk_label with "critical"
        call_args = [str(c) for c in agent._store.get_devices_by_risk_label.call_args_list]
        assert any("critical" in s for s in call_args)


if __name__ == "__main__":
    unittest.main()
