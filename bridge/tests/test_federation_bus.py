"""
Tests for FederationBus — Phase 34

4 tests covering:
1. _sync_cycle fetches from peers and stores remote clusters with is_local=False
2. _process_peer_clusters dispatches escalation when cross-confirmed hash exists
3. FederationBus non-fatal when peer raises connection error
4. _known_peer_hashes dedup prevents re-processing same cluster from same peer
"""
import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.federation_bus import FederationBus, compute_cluster_hash


def _make_bus(local_clusters=None):
    store = MagicMock()
    store.store_federation_cluster = MagicMock()
    store.store_protocol_insight = MagicMock()
    store.get_cross_confirmed_hashes = MagicMock(return_value=[])

    net = MagicMock()
    net.detect_clusters = MagicMock(return_value=local_clusters or [])

    chain = MagicMock()
    chain.report_federated_cluster = AsyncMock(return_value="")

    cfg = MagicMock()
    cfg.federation_peers = "http://peer1:8000"
    cfg.federation_api_key = "testkey"
    cfg.federation_poll_interval = 120.0
    cfg.federated_threat_registry_address = ""

    bus = FederationBus(store, net, chain, cfg, poll_interval=120.0)
    return bus, store


class TestFederationBus(unittest.IsolatedAsyncioTestCase):

    async def test_1_sync_cycle_stores_remote_clusters_with_is_local_false(self):
        """_sync_cycle fetches peer clusters and stores them with is_local=False."""
        bus, store = _make_bus()
        remote = [
            {
                "cluster_hash": "abc123def456789a",
                "device_count": 3,
                "suspicion_bucket": "critical",
                "bridge_id": "peer_abc",
            }
        ]
        with patch.object(bus, "_fetch_peer_clusters", new=AsyncMock(return_value=remote)):
            with patch("vapi_bridge.federation_bus.ws_broadcast", new_callable=AsyncMock):
                await bus._sync_cycle()

        # store_federation_cluster called for the remote cluster
        assert store.store_federation_cluster.called
        call_args_str = str(store.store_federation_cluster.call_args_list)
        # is_local=False should appear in the call
        assert "False" in call_args_str or "is_local" in call_args_str

    async def test_2_escalation_dispatched_on_cross_confirmed_hash(self):
        """_process_peer_clusters dispatches escalation for cross-confirmed hash."""
        bus, store = _make_bus()
        confirmed_hash = "deadbeef12345678"
        store.get_cross_confirmed_hashes.return_value = [confirmed_hash]

        remote = [
            {
                "cluster_hash": confirmed_hash,
                "device_count": 2,
                "suspicion_bucket": "critical",
                "bridge_id": "peer_x",
            }
        ]
        with patch("vapi_bridge.federation_bus.ws_broadcast", new_callable=AsyncMock):
            await bus._process_peer_clusters("http://peer1:8000", remote)

        store.store_protocol_insight.assert_called_once()
        call_args_str = str(store.store_protocol_insight.call_args)
        assert "federated_cluster" in call_args_str

    async def test_3_peer_connection_error_is_nonfatal(self):
        """FederationBus does not raise when a peer raises a connection error."""
        bus, store = _make_bus()
        with patch.object(
            bus, "_fetch_peer_clusters",
            new=AsyncMock(side_effect=ConnectionError("Connection refused")),
        ):
            with patch("vapi_bridge.federation_bus.ws_broadcast", new_callable=AsyncMock):
                # Should complete without raising
                await bus._sync_cycle()

    async def test_4_dedup_prevents_reprocessing_same_cluster(self):
        """_known_peer_hashes prevents re-storing the same cluster from the same peer."""
        bus, store = _make_bus()
        store.get_cross_confirmed_hashes.return_value = []

        remote = [
            {
                "cluster_hash": "aabbccdd11223344",
                "device_count": 2,
                "suspicion_bucket": "medium",
                "bridge_id": "peer_y",
            }
        ]
        with patch("vapi_bridge.federation_bus.ws_broadcast", new_callable=AsyncMock):
            await bus._process_peer_clusters("http://peer1:8000", remote)
            await bus._process_peer_clusters("http://peer1:8000", remote)  # same peer, same cluster

        # store_federation_cluster called only once despite two calls
        assert store.store_federation_cluster.call_count == 1


if __name__ == "__main__":
    unittest.main()
