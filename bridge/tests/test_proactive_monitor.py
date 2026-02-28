"""
Tests for ProactiveMonitor — Phase 32: The Protocol Thinks Ahead

4 tests covering:
1. Cluster detection dispatches alert for new flagged cluster
2. Trajectory check dispatches alert for device with warning
3. Monitor cycle is non-fatal when behavioral_arch raises
4. Same cluster only alerts once (deduplication)
"""
import asyncio
import unittest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.proactive_monitor import ProactiveMonitor


@dataclass
class _FakeCluster:
    cluster_id: str
    device_ids: list
    avg_intra_distance: float
    farm_suspicion_score: float
    is_flagged: bool


@dataclass
class _FakeReport:
    device_id: str
    drift_trend_slope: float = 0.0
    humanity_trend_slope: float = 0.0
    warmup_attack_score: float = 0.0
    burst_farming_score: float = 0.0
    biometric_stability_cert: bool = True
    l4_consistency_cert: bool = True
    session_count: int = 5
    report_timestamp: float = 0.0
    warning: str = ""


class TestProactiveMonitorAlerts(unittest.IsolatedAsyncioTestCase):

    def _make_monitor(self, clusters=None, risky_devices=None, leaderboard=None):
        store = MagicMock()
        store.store_protocol_insight = MagicMock()
        store.get_leaderboard = MagicMock(return_value=leaderboard or [])

        net = MagicMock()
        net.detect_clusters = MagicMock(return_value=clusters or [])

        arch = MagicMock()
        arch.get_high_risk_devices = MagicMock(return_value=risky_devices or [])
        arch.analyze_device = MagicMock(return_value=_FakeReport(device_id="aabb"))

        cfg = MagicMock()
        agent = MagicMock()

        monitor = ProactiveMonitor(
            store, arch, net, agent, cfg, poll_interval=0.0
        )
        return monitor, store

    async def test_1_cluster_detection_dispatches_alert_for_new_flagged_cluster(self):
        """A new flagged cluster triggers store_protocol_insight with bot_farm_cluster type."""
        cluster = _FakeCluster(
            "c1", ["dev_a", "dev_b", "dev_c"], 0.5, 0.9, True
        )
        monitor, store = self._make_monitor(clusters=[cluster])

        with patch(
            "vapi_bridge.proactive_monitor.ws_broadcast", new_callable=AsyncMock
        ):
            await monitor._check_anomaly_clusters()

        store.store_protocol_insight.assert_called_once()
        call_args = store.store_protocol_insight.call_args
        assert call_args.kwargs.get("insight_type") == "bot_farm_cluster" or \
               "bot_farm_cluster" in str(call_args)

    async def test_2_trajectory_check_dispatches_alert_for_device_with_warning(self):
        """A high-risk device with a warning string triggers a high_risk_trajectory alert."""
        report = _FakeReport(
            device_id="dev1",
            warmup_attack_score=0.85,
            burst_farming_score=0.9,
            warning="Warmup attack pattern detected",
        )
        monitor, store = self._make_monitor(risky_devices=["dev1"])
        monitor._behavioral_arch.analyze_device.return_value = report

        with patch(
            "vapi_bridge.proactive_monitor.ws_broadcast", new_callable=AsyncMock
        ):
            await monitor._check_high_risk_trajectories()

        store.store_protocol_insight.assert_called_once()
        call_args = store.store_protocol_insight.call_args
        assert "high_risk_trajectory" in str(call_args)

    async def test_3_monitor_cycle_nonfatal_when_behavioral_arch_raises(self):
        """A RuntimeError in behavioral_arch.get_high_risk_devices does not crash the cycle."""
        monitor, store = self._make_monitor()
        monitor._behavioral_arch.get_high_risk_devices.side_effect = RuntimeError(
            "DB connection error"
        )

        # Should complete without raising
        with patch(
            "vapi_bridge.proactive_monitor.ws_broadcast", new_callable=AsyncMock
        ):
            await monitor._monitor_cycle()

    async def test_4_same_cluster_only_alerts_once_deduplication(self):
        """Calling _check_anomaly_clusters twice with the same cluster only alerts once."""
        cluster = _FakeCluster("c1", ["dev_a", "dev_b"], 0.4, 0.8, True)
        monitor, store = self._make_monitor(clusters=[cluster])

        with patch(
            "vapi_bridge.proactive_monitor.ws_broadcast", new_callable=AsyncMock
        ):
            await monitor._check_anomaly_clusters()
            await monitor._check_anomaly_clusters()  # same cluster, second call

        # frozenset dedup — should only alert once
        assert store.store_protocol_insight.call_count == 1


if __name__ == "__main__":
    unittest.main()
