"""
Tests for BridgeAgent cross-device tools — Phase 32

4 tests covering:
1. get_behavioral_report returns dict with warmup_attack_score
2. get_network_clusters returns dict with clusters list and counts
3. Both tools return {"error": ...} when modules are None (graceful degradation)
4. get_network_clusters with min_suspicion=0.9 filters correctly
"""
import sys
import os
import types
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub anthropic before importing bridge_agent
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = types.ModuleType("anthropic")

from vapi_bridge.bridge_agent import BridgeAgent


@dataclass
class _FakeReport:
    device_id: str
    drift_trend_slope: float = 0.1
    humanity_trend_slope: float = -0.05
    warmup_attack_score: float = 0.72
    burst_farming_score: float = 0.3
    biometric_stability_cert: bool = True
    l4_consistency_cert: bool = True
    session_count: int = 12
    report_timestamp: float = 0.0
    warning: str = "Warmup attack pattern detected"


@dataclass
class _FakeCluster:
    cluster_id: str
    device_ids: list
    avg_intra_distance: float
    farm_suspicion_score: float
    is_flagged: bool


class TestAgentCrossDevice(unittest.TestCase):

    def _agent(self, arch=None, net=None):
        return BridgeAgent(
            MagicMock(), MagicMock(),
            behavioral_arch=arch,
            network_detector=net,
        )

    def test_1_get_behavioral_report_returns_dict_with_warmup_attack_score(self):
        """get_behavioral_report tool calls analyze_device and returns dataclass dict."""
        arch = MagicMock()
        arch.analyze_device.return_value = _FakeReport(device_id="dev_abc")

        agent = self._agent(arch=arch)
        result = agent._execute_tool("get_behavioral_report", {"device_id": "dev_abc"})

        assert "warmup_attack_score" in result
        assert result["warmup_attack_score"] == 0.72
        arch.analyze_device.assert_called_once_with("dev_abc")

    def test_2_get_network_clusters_returns_dict_with_clusters_list(self):
        """get_network_clusters tool returns clusters, flagged_count, total_clusters."""
        cluster = _FakeCluster("c1", ["d1", "d2"], 0.4, 0.8, True)
        net = MagicMock()
        net.detect_clusters.return_value = [cluster]

        agent = self._agent(net=net)
        result = agent._execute_tool("get_network_clusters", {})

        assert "clusters" in result
        assert len(result["clusters"]) == 1
        assert result["flagged_count"] == 1
        assert result["total_clusters"] == 1

    def test_3_tools_return_error_dict_when_modules_are_none(self):
        """Both cross-device tools gracefully degrade with error dict when not injected."""
        agent = self._agent()  # no arch or net

        r1 = agent._execute_tool("get_behavioral_report", {"device_id": "x"})
        r2 = agent._execute_tool("get_network_clusters", {})

        assert "error" in r1
        assert "error" in r2
        assert "BehavioralArchaeologist" in r1["error"]
        assert "NetworkCorrelationDetector" in r2["error"]

    def test_4_get_network_clusters_min_suspicion_filters_correctly(self):
        """min_suspicion=0.9 filters clusters; flagged_count counts ALL is_flagged clusters."""
        clusters = [
            _FakeCluster("c1", ["d1", "d2"], 0.3, 0.95, True),   # suspicion=0.95, is_flagged
            _FakeCluster("c2", ["d3", "d4"], 0.3, 0.85, True),   # suspicion=0.85, is_flagged
            _FakeCluster("c3", ["d5", "d6"], 0.3, 0.40, False),  # suspicion=0.40, not_flagged
        ]
        net = MagicMock()
        net.detect_clusters.return_value = clusters

        agent = self._agent(net=net)
        result = agent._execute_tool("get_network_clusters", {"min_suspicion": 0.9})

        # Only c1 passes the min_suspicion=0.9 filter
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["cluster_id"] == "c1"
        # flagged_count counts ALL is_flagged clusters regardless of filter (c1 + c2)
        assert result["flagged_count"] == 2
        assert result["total_clusters"] == 3


if __name__ == "__main__":
    unittest.main()
