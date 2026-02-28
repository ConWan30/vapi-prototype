"""
Phase 29 — Operator Gate API Tests

TestOperatorApiConfigured (5):
1.  GET /gate/{device_id}?api_key=correct → 200, sig field present
2.  GET /gate/{device_id}?api_key=wrong → 403
3.  Signature verifies: HMAC-SHA256(f"{device_id}:{int(eligible)}:{timestamp}", key)
4.  POST /gate/batch → returns list with one entry per device_id
5.  POST /gate/batch with 51 ids → only first 50 returned (cap enforced)

TestOperatorApiUnconfigured (2):
6.  GET /gate/{device_id}?api_key=anything → 503 (key not configured)
7.  GET /health → 200, operator_key_configured=False

TestOperatorApiHealth (1):
8.  GET /health with key configured → operator_key_configured=True
"""

import hashlib
import hmac
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy deps before import
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

_web3_exc = sys.modules["web3.exceptions"]
for _attr in ("ContractLogicError", "TransactionNotFound"):
    if not hasattr(_web3_exc, _attr):
        setattr(_web3_exc, _attr, type(_attr, (Exception,), {}))
_web3_mod = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3_mod, _attr):
        setattr(_web3_mod, _attr, MagicMock())
_eth_acct = sys.modules["eth_account"]
if not hasattr(_eth_acct, "Account"):
    _eth_acct.Account = MagicMock()

from fastapi.testclient import TestClient

from vapi_bridge.operator_api import create_operator_app

_API_KEY = "test-operator-key-phase29"
_DEVICE_ID = "ab" * 32


def _make_cfg(key: str = _API_KEY):
    cfg = MagicMock()
    cfg.operator_api_key = key
    return cfg


def _make_store():
    store = MagicMock()
    store.get_last_phg_checkpoint.return_value = None
    store.get_credential_mint.return_value = None
    return store


def _verify_sig(device_id: str, eligible: bool, ts: int, sig: str, key: str) -> bool:
    msg = f"{device_id}:{int(eligible)}:{ts}".encode()
    expected = hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


# ===========================================================================
# TestOperatorApiConfigured
# ===========================================================================

class TestOperatorApiConfigured(unittest.TestCase):

    def setUp(self):
        self.store = _make_store()
        self.client = TestClient(create_operator_app(_make_cfg(), self.store))

    def test_1_correct_key_returns_200_with_sig(self):
        """GET /gate/{device_id}?api_key=correct → 200, sig field present."""
        r = self.client.get(f"/gate/{_DEVICE_ID}", params={"api_key": _API_KEY})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("sig", data)
        self.assertIn("eligible", data)
        self.assertIn("device_id", data)
        self.assertEqual(data["device_id"], _DEVICE_ID)

    def test_2_wrong_key_returns_403(self):
        """GET /gate/{device_id}?api_key=wrong → 403."""
        r = self.client.get(f"/gate/{_DEVICE_ID}", params={"api_key": "wrongkey"})
        self.assertEqual(r.status_code, 403)

    def test_3_signature_verifies_correctly(self):
        """Signature = HMAC-SHA256(device_id:eligible:timestamp, key)."""
        r = self.client.get(f"/gate/{_DEVICE_ID}", params={"api_key": _API_KEY})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        valid = _verify_sig(
            data["device_id"], data["eligible"],
            data["timestamp"], data["sig"], _API_KEY
        )
        self.assertTrue(valid, "Signature should verify correctly")

    def test_4_batch_returns_list(self):
        """POST /gate/batch → returns list with one entry per device_id."""
        ids = [f"{'aa' * 32}", f"{'bb' * 32}", f"{'cc' * 32}"]
        r = self.client.post(
            "/gate/batch",
            params={"api_key": _API_KEY},
            json=ids,
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data), 3)
        for entry in data:
            self.assertIn("sig", entry)
            self.assertIn("eligible", entry)

    def test_5_batch_caps_at_50(self):
        """POST /gate/batch with 51 ids → only first 50 returned."""
        ids = [f"{i:02x}" * 32 for i in range(51)]
        r = self.client.post(
            "/gate/batch",
            params={"api_key": _API_KEY},
            json=ids,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 50)


# ===========================================================================
# TestOperatorApiUnconfigured
# ===========================================================================

class TestOperatorApiUnconfigured(unittest.TestCase):

    def setUp(self):
        self.store = _make_store()
        self.client = TestClient(create_operator_app(_make_cfg(key=""), self.store))

    def test_6_gate_returns_503_when_no_key(self):
        """GET /gate/{device_id}?api_key=anything → 503 (key not configured)."""
        r = self.client.get(f"/gate/{_DEVICE_ID}", params={"api_key": "anything"})
        self.assertEqual(r.status_code, 503)

    def test_7_health_shows_key_not_configured(self):
        """GET /health → 200, operator_key_configured=False."""
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["operator_key_configured"])


# ===========================================================================
# TestOperatorApiHealth
# ===========================================================================

class TestOperatorApiHealth(unittest.TestCase):

    def test_8_health_shows_key_configured(self):
        """GET /health with key configured → operator_key_configured=True."""
        client = TestClient(create_operator_app(_make_cfg(), _make_store()))
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["operator_key_configured"])


if __name__ == "__main__":
    unittest.main()
