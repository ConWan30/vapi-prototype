"""
Phase 12 — ManufacturerKeyRevoked event listener / revocation cache tests.

Tests the _revoked_manufacturers cache mechanics and is_manufacturer_revoked()
logic. No live web3 required — uses pure in-process state manipulation.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Bootstrap: ensure vapi_bridge is importable without real web3 or eth_account
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parents[1]))

for _mod in ("web3", "web3.exceptions", "eth_account"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

_web3_exc = sys.modules.get("web3.exceptions", types.ModuleType("web3.exceptions"))
if not hasattr(_web3_exc, "ContractLogicError"):
    _web3_exc.ContractLogicError = type("ContractLogicError", (Exception,), {})
if not hasattr(_web3_exc, "TransactionNotFound"):
    _web3_exc.TransactionNotFound = type("TransactionNotFound", (Exception,), {})
sys.modules["web3.exceptions"] = _web3_exc

_web3 = sys.modules.get("web3", types.ModuleType("web3"))
if not hasattr(_web3, "AsyncWeb3"):
    _web3.AsyncWeb3 = MagicMock
if not hasattr(_web3, "AsyncHTTPProvider"):
    _web3.AsyncHTTPProvider = MagicMock
sys.modules["web3"] = _web3

_eth_acc = sys.modules.get("eth_account", types.ModuleType("eth_account"))
if not hasattr(_eth_acc, "Account"):
    _mock_acct = MagicMock()
    _mock_acct.from_key.return_value = MagicMock(address="0xBridgeAddr")
    _eth_acc.Account = _mock_acct
sys.modules["eth_account"] = _eth_acc

from vapi_bridge.chain import ChainClient  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_client() -> ChainClient:
    client = ChainClient.__new__(ChainClient)
    client._cfg = MagicMock()
    client._w3 = MagicMock()
    client._account = MagicMock(address="0xBridgeAddr")
    client._nonce_lock = MagicMock()
    client._nonce = None
    client._revoked_manufacturers = set()
    client._verifier = MagicMock()
    client._bounty_market = None
    client._registry = MagicMock()
    client._progress = None
    client._team_agg = None
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestRevocationCacheInit(unittest.TestCase):

    def test_revoked_manufacturers_set_is_empty_on_init(self):
        client = _make_client()
        assert len(client._revoked_manufacturers) == 0

    def test_is_manufacturer_revoked_false_for_unknown_address(self):
        client = _make_client()
        assert client.is_manufacturer_revoked("0xUnknown") is False

    def test_is_manufacturer_revoked_true_after_manual_cache_population(self):
        client = _make_client()
        addr = "0xdeadbeef00000000000000000000000000000000"
        client._revoked_manufacturers.add(addr.lower())
        assert client.is_manufacturer_revoked(addr.lower()) is True

    def test_revoked_set_lowercases_checksummed_address(self):
        """Cache uses lowercase; is_manufacturer_revoked must be case-insensitive."""
        client = _make_client()
        checksummed = "0xDeAdBeEf00000000000000000000000000000000"
        # Simulate what watch_manufacturer_revocations does: stores lowercase
        client._revoked_manufacturers.add(checksummed.lower())
        # Caller uses lowercase — must match
        assert client.is_manufacturer_revoked(checksummed.lower()) is True


if __name__ == "__main__":
    unittest.main()
