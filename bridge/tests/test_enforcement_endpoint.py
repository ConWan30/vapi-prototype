"""
Phase 37 — GET /operator/enforcement endpoint tests.

4 tests covering:
1. GET /enforcement returns 200 with empty list when no suspensions
2. GET /enforcement?device_id=X returns enforcement state for that device
3. GET /enforcement?api_key=wrong returns 403
4. enforcement_enabled=False reflected in response
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from vapi_bridge.store import Store
from vapi_bridge.operator_api import create_operator_app


def _fresh_store() -> Store:
    db_dir = tempfile.mkdtemp()
    return Store(os.path.join(db_dir, "test.db"))


def _make_client(store, enforcement_enabled=True):
    cfg = MagicMock()
    cfg.operator_api_key = "test-key"
    cfg.rate_limit_per_minute = 60
    cfg.phg_credential_enforcement_enabled = enforcement_enabled
    cfg.credential_enforcement_min_consecutive = 2
    app = create_operator_app(cfg, store)
    return TestClient(app)


class TestEnforcementEndpoint(unittest.TestCase):

    def test_1_enforcement_empty_when_no_suspensions(self):
        """GET /enforcement returns 200 with empty suspended_devices when nothing suspended."""
        store = _fresh_store()
        client = _make_client(store)
        resp = client.get("/enforcement", params={"api_key": "test-key"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("suspended_count", data)
        self.assertEqual(data["suspended_count"], 0)
        self.assertIsInstance(data["suspended_devices"], list)
        self.assertEqual(len(data["suspended_devices"]), 0)

    def test_2_enforcement_returns_device_state(self):
        """GET /enforcement?device_id=X returns enforcement state for that device."""
        import time
        store = _fresh_store()
        dev = "aa" * 32
        store.store_credential_suspension(dev, "deadbeef" * 8, time.time() + 86400)
        client = _make_client(store)
        resp = client.get("/enforcement", params={"api_key": "test-key", "device_id": dev})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["suspended_count"], 1)
        self.assertEqual(len(data["suspended_devices"]), 1)
        self.assertEqual(data["suspended_devices"][0]["device_id"], dev)

    def test_3_wrong_api_key_returns_403(self):
        """GET /enforcement with wrong api_key returns 403."""
        store = _fresh_store()
        client = _make_client(store)
        resp = client.get("/enforcement", params={"api_key": "wrong-key"})
        self.assertEqual(resp.status_code, 403)

    def test_4_enforcement_enabled_false_reflected(self):
        """Response includes enforcement_enabled=False when config disables it."""
        store = _fresh_store()
        client = _make_client(store, enforcement_enabled=False)
        resp = client.get("/enforcement", params={"api_key": "test-key"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["enforcement_enabled"])


if __name__ == "__main__":
    unittest.main()
