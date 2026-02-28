"""
Tests for GET /federation/clusters endpoint — Phase 34

4 tests covering:
1. Missing api_key returns 422
2. Wrong api_key returns 403
3. Correct api_key returns list from store.get_federation_clusters(is_local=True)
4. Returns [] when no local clusters stored
"""
import sys
import os
import unittest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.operator_api import create_operator_app


def _make_client(federation_data=None):
    cfg = MagicMock()
    cfg.operator_api_key = "testkey"
    cfg.phg_registry_address = ""
    store = MagicMock()
    store.get_recent_insights.return_value = []
    store.get_federation_clusters.return_value = federation_data or []
    store.get_last_phg_checkpoint.return_value = None
    store.get_credential_mint.return_value = None
    app = create_operator_app(cfg, store)
    return TestClient(app), store


class TestFederationEndpoint(unittest.TestCase):

    def test_1_missing_api_key_returns_422(self):
        """Missing api_key query param → 422 Unprocessable Entity."""
        client, _ = _make_client()
        resp = client.get("/federation/clusters")
        assert resp.status_code == 422

    def test_2_wrong_api_key_returns_403(self):
        """Wrong api_key → 403 Forbidden."""
        client, _ = _make_client()
        resp = client.get("/federation/clusters?api_key=wrongkey")
        assert resp.status_code == 403

    def test_3_correct_key_calls_store_with_is_local_true(self):
        """Correct api_key calls store.get_federation_clusters(is_local=True)."""
        data = [
            {
                "id": 1,
                "cluster_hash": "abc123def456789a",
                "peer_url": "",
                "device_count": 3,
                "suspicion_bucket": "critical",
                "bridge_id": "local_bridge",
                "detected_at": 1700000000.0,
                "is_local": 1,
            }
        ]
        client, store = _make_client(federation_data=data)
        resp = client.get("/federation/clusters?api_key=testkey")
        assert resp.status_code == 200
        store.get_federation_clusters.assert_called_once()
        # Verify is_local=True was passed
        call_kwargs = store.get_federation_clusters.call_args
        assert "is_local" in str(call_kwargs)
        assert "True" in str(call_kwargs)

    def test_4_returns_empty_list_when_no_local_clusters(self):
        """Returns [] when no local clusters stored."""
        client, _ = _make_client(federation_data=[])
        resp = client.get("/federation/clusters?api_key=testkey")
        assert resp.status_code == 200
        assert resp.json() == []


if __name__ == "__main__":
    unittest.main()
