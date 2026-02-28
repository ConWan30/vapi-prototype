"""
Tests for ProactiveMonitor time-bounded cluster dedup eviction — Phase 36

4 tests covering:
1. Cluster entry stored with float monotonic timestamp (not bool/None)
2. Cluster older than 24h is evicted; same cluster re-triggers alert on next cycle
3. Recent cluster (1s ago) is deduped and does NOT re-alert
4. _evict_stale_clusters() returns correct eviction count
"""
import asyncio
import sys
import os
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.proactive_monitor import ProactiveMonitor


def _make_monitor():
    store = MagicMock()
    store.get_leaderboard.return_value = []
    store.store_protocol_insight = MagicMock()

    behavioral_arch = MagicMock()
    behavioral_arch.get_high_risk_devices.return_value = []

    network_detector = MagicMock()
    network_detector.detect_clusters.return_value = []

    agent = MagicMock()
    cfg = MagicMock()

    return ProactiveMonitor(
        store, behavioral_arch, network_detector, agent, cfg,
        poll_interval=60.0,
    ), store, network_detector


class TestProactiveEviction(unittest.IsolatedAsyncioTestCase):

    async def test_1_cluster_stored_with_float_timestamp(self):
        """When a cluster is first seen, its key is stored with a float monotonic timestamp."""
        monitor, store, net_det = _make_monitor()

        cluster = MagicMock()
        cluster.is_flagged = True
        cluster.device_ids = ["dev_aaa", "dev_bbb"]
        cluster.farm_suspicion_score = 0.9
        cluster.avg_intra_distance = 0.1
        cluster.cluster_id = "c001"
        net_det.detect_clusters.return_value = [cluster]

        await monitor._check_anomaly_clusters()

        key = frozenset(cluster.device_ids)
        assert key in monitor._known_flagged_clusters
        ts = monitor._known_flagged_clusters[key]
        assert isinstance(ts, float), f"Expected float timestamp, got {type(ts)}"

    async def test_2_stale_cluster_evicted_and_re_alerts(self):
        """Cluster older than 24h is evicted; same cluster triggers alert on next check."""
        monitor, store, net_det = _make_monitor()

        device_ids = ["dev_ccc", "dev_ddd"]
        key = frozenset(device_ids)
        # Pre-populate with a 25-hour-old entry (stale)
        monitor._known_flagged_clusters[key] = time.monotonic() - 90001.0

        cluster = MagicMock()
        cluster.is_flagged = True
        cluster.device_ids = device_ids
        cluster.farm_suspicion_score = 0.92
        cluster.avg_intra_distance = 0.08
        cluster.cluster_id = "c002"
        net_det.detect_clusters.return_value = [cluster]

        await monitor._check_anomaly_clusters()

        # After eviction, cluster should re-alert and be re-stored with fresh timestamp
        assert key in monitor._known_flagged_clusters
        new_ts = monitor._known_flagged_clusters[key]
        # New timestamp should be recent (within last 5 seconds)
        assert time.monotonic() - new_ts < 5.0, "New timestamp not fresh after eviction"
        # store_protocol_insight should have been called (alert dispatched)
        store.store_protocol_insight.assert_called()

    async def test_3_recent_cluster_is_deduped_and_no_alert(self):
        """A cluster seen 1 second ago is deduped and does NOT dispatch a new alert."""
        monitor, store, net_det = _make_monitor()

        device_ids = ["dev_eee", "dev_fff"]
        key = frozenset(device_ids)
        # Pre-populate with a fresh entry (1s ago)
        monitor._known_flagged_clusters[key] = time.monotonic() - 1.0

        cluster = MagicMock()
        cluster.is_flagged = True
        cluster.device_ids = device_ids
        cluster.farm_suspicion_score = 0.88
        cluster.avg_intra_distance = 0.12
        cluster.cluster_id = "c003"
        net_det.detect_clusters.return_value = [cluster]

        await monitor._check_anomaly_clusters()

        # store_protocol_insight should NOT be called for this deduped cluster
        store.store_protocol_insight.assert_not_called()

    async def test_4_evict_stale_clusters_returns_correct_count(self):
        """_evict_stale_clusters() returns the number of entries evicted."""
        monitor, _, _ = _make_monitor()

        # Add 3 stale entries (>24h old) and 2 fresh entries
        stale_cutoff = time.monotonic() - 90001.0
        fresh_ts = time.monotonic() - 100.0

        monitor._known_flagged_clusters = {
            frozenset(["dev_g1", "dev_g2"]): stale_cutoff,
            frozenset(["dev_g3", "dev_g4"]): stale_cutoff,
            frozenset(["dev_g5", "dev_g6"]): stale_cutoff,
            frozenset(["dev_g7", "dev_g8"]): fresh_ts,
            frozenset(["dev_g9", "dev_g10"]): fresh_ts,
        }

        evicted = monitor._evict_stale_clusters()

        assert evicted == 3, f"Expected 3 evictions, got {evicted}"
        assert len(monitor._known_flagged_clusters) == 2, (
            f"Expected 2 remaining, got {len(monitor._known_flagged_clusters)}"
        )


if __name__ == "__main__":
    unittest.main()
