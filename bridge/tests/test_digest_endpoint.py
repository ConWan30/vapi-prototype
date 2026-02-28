"""
Tests for GET /digest endpoint — Phase 35

4 tests covering:
1. Missing api_key returns 422
2. Wrong api_key returns 403
3. Correct api_key returns get_all_latest_digests() result
4. window=7d calls get_latest_digest("7d")
"""
import sys
import os
import unittest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.operator_api import create_operator_app


def _make_client(digests=None):
    cfg = MagicMock()
    cfg.operator_api_key = "testkey"
    cfg.phg_registry_address = ""
    store = MagicMock()
    store.get_recent_insights.return_value = []
    store.get_federation_clusters.return_value = []
    store.get_last_phg_checkpoint.return_value = None
    store.get_credential_mint.return_value = None
    store.get_all_latest_digests.return_value = digests or []
    store.get_latest_digest.return_value = (digests[0] if digests else None)
    store.get_devices_by_risk_label.return_value = []
    app = create_operator_app(cfg, store)
    return TestClient(app), store


class TestDigestEndpoint(unittest.TestCase):

    def test_1_missing_api_key_returns_422(self):
        """Missing api_key query param → 422 Unprocessable Entity."""
        client, _ = _make_client()
        resp = client.get("/digest")
        assert resp.status_code == 422

    def test_2_wrong_api_key_returns_403(self):
        """Wrong api_key → 403 Forbidden."""
        client, _ = _make_client()
        resp = client.get("/digest?api_key=wrongkey")
        assert resp.status_code == 403

    def test_3_correct_key_returns_all_latest_digests(self):
        """Correct api_key → 200 with digests from store.get_all_latest_digests()."""
        sample_digest = {
            "id": 1, "window_label": "24h", "synthesized_at": 1700000000.0,
            "bot_farm_count": 2, "high_risk_count": 1, "federated_count": 0,
            "anomaly_count": 3, "eligible_count": 0, "dominant_severity": "critical",
            "top_devices": ["dev_aabb", "dev_ccdd"], "narrative": "24h digest: 2 bot-farm alerts.",
        }
        client, store = _make_client(digests=[sample_digest])
        resp = client.get("/digest?api_key=testkey")
        assert resp.status_code == 200
        body = resp.json()
        assert body["synthesis_available"] is True
        assert len(body["digests"]) == 1
        store.get_all_latest_digests.assert_called_once()

    def test_4_window_7d_calls_get_latest_digest(self):
        """GET /digest?window=7d calls store.get_latest_digest('7d')."""
        sample = {
            "id": 2, "window_label": "7d", "synthesized_at": 1700000000.0,
            "bot_farm_count": 5, "high_risk_count": 3, "federated_count": 1,
            "anomaly_count": 10, "eligible_count": 2, "dominant_severity": "critical",
            "top_devices": [], "narrative": "7d digest: 5 bot-farm alerts.",
        }
        client, store = _make_client(digests=[sample])
        store.get_latest_digest.return_value = sample
        resp = client.get("/digest?window=7d&api_key=testkey")
        assert resp.status_code == 200
        store.get_latest_digest.assert_called_once_with("7d")


if __name__ == "__main__":
    unittest.main()
