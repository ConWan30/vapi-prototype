"""
Tests for Operator API in-memory rate limiting — Phase 36

4 tests covering:
1. First request within rate limit returns 200
2. Third request when rate_limit_per_minute=2 returns 429 with Retry-After header
3. Two different api_keys have independent rate limit buckets
4. /health endpoint (no auth, no rate limit) always returns 200
"""
import sys
import os
import unittest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.operator_api import create_operator_app


def _make_client(rate_limit=2):
    cfg = MagicMock()
    cfg.operator_api_key = "testkey"
    cfg.phg_registry_address = ""
    cfg.rate_limit_per_minute = rate_limit

    store = MagicMock()
    store.get_recent_insights.return_value = []
    store.get_federation_clusters.return_value = []
    store.get_last_phg_checkpoint.return_value = None
    store.get_credential_mint.return_value = None
    store.get_all_latest_digests.return_value = []
    store.get_devices_by_risk_label.return_value = []
    store.get_latest_digest.return_value = None

    app = create_operator_app(cfg, store)
    return TestClient(app)


class TestRateLimiting(unittest.TestCase):

    def test_1_first_request_returns_200(self):
        """First authenticated request within rate limit returns 200."""
        client = _make_client(rate_limit=5)
        resp = client.get("/insights?api_key=testkey")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    def test_2_third_request_at_limit_2_returns_429(self):
        """Third request in the same window when rate_limit=2 returns 429."""
        client = _make_client(rate_limit=2)
        r1 = client.get("/insights?api_key=testkey")
        r2 = client.get("/insights?api_key=testkey")
        r3 = client.get("/insights?api_key=testkey")

        assert r1.status_code == 200, f"Expected 200 for first request, got {r1.status_code}"
        assert r2.status_code == 200, f"Expected 200 for second request, got {r2.status_code}"
        assert r3.status_code == 429, f"Expected 429 for third request, got {r3.status_code}"
        # Verify Retry-After header is present
        assert "retry-after" in {k.lower() for k in r3.headers}, (
            f"Retry-After header missing from 429 response. Headers: {dict(r3.headers)}"
        )

    def test_3_different_api_keys_have_independent_buckets(self):
        """Two api_keys have independent rate limit windows."""
        cfg = MagicMock()
        cfg.operator_api_key = "key1"
        cfg.phg_registry_address = ""
        cfg.rate_limit_per_minute = 1  # only 1 request per key per 60s

        store = MagicMock()
        store.get_recent_insights.return_value = []
        store.get_federation_clusters.return_value = []
        store.get_last_phg_checkpoint.return_value = None
        store.get_credential_mint.return_value = None
        store.get_all_latest_digests.return_value = []
        store.get_devices_by_risk_label.return_value = []

        # Create apps for two different keys using same underlying limiter logic
        # (same app instance, but different keys map to different buckets)
        app = create_operator_app(cfg, store)
        client = TestClient(app)

        # key1 first request — should succeed
        r1 = client.get("/insights?api_key=key1")
        assert r1.status_code == 200

        # key1 second request — should be rate limited
        r2 = client.get("/insights?api_key=key1")
        assert r2.status_code == 429

        # key2 is wrong key → 403 (but NOT rate limited — it gets auth error first)
        # The key difference: the limiter is per-key, so key2's bucket is empty.
        # We test this by using the _RateLimiter directly.
        from vapi_bridge.operator_api import _RateLimiter
        limiter = _RateLimiter(requests_per_minute=1)
        assert limiter.is_allowed("key_a") is True
        assert limiter.is_allowed("key_a") is False  # rate limited
        assert limiter.is_allowed("key_b") is True   # key_b is independent

    def test_4_health_endpoint_always_returns_200(self):
        """GET /health has no auth and no rate limit — always returns 200."""
        client = _make_client(rate_limit=1)
        # Make many requests — all should succeed
        for i in range(5):
            resp = client.get("/health")
            assert resp.status_code == 200, (
                f"Expected /health 200 on attempt {i+1}, got {resp.status_code}"
            )


if __name__ == "__main__":
    unittest.main()
