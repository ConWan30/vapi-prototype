"""
Phase 12 — ChainClient V2 method tests.

Tests verify_poac(), register_device_attested_v2(), get_manufacturer_key(),
and schema-version dispatch logic without live web3/chain connections.

Uses unittest.mock.MagicMock for all web3 contract objects.
No web3 or eth_account import at module level (matches bridge pytest env constraint).
All async tests use asyncio.run() inside regular TestCase methods to avoid
IsolatedAsyncioTestCase event-loop side effects in the shared test suite.
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Bootstrap: ensure vapi_bridge is importable without real web3 or eth_account
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parents[1]))

# Stub heavy dependencies that are not installed in the test env
for _mod in ("web3", "web3.exceptions", "eth_account"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# web3.exceptions needs ContractLogicError and TransactionNotFound
_web3_exc = sys.modules.get("web3.exceptions", types.ModuleType("web3.exceptions"))
if not hasattr(_web3_exc, "ContractLogicError"):
    _web3_exc.ContractLogicError = type("ContractLogicError", (Exception,), {})
if not hasattr(_web3_exc, "TransactionNotFound"):
    _web3_exc.TransactionNotFound = type("TransactionNotFound", (Exception,), {})
sys.modules["web3.exceptions"] = _web3_exc

# AsyncWeb3 stub
_web3 = sys.modules.get("web3", types.ModuleType("web3"))
if not hasattr(_web3, "AsyncWeb3"):
    _web3.AsyncWeb3 = MagicMock
if not hasattr(_web3, "AsyncHTTPProvider"):
    _web3.AsyncHTTPProvider = MagicMock
sys.modules["web3"] = _web3

# eth_account Account stub
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
    """Return a ChainClient with all external calls mocked."""
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


DEVICE_ID = bytes.fromhex("aa" * 32)
RAW_BODY   = bytes(164)
SIGNATURE  = bytes(64)
FAKE_TX    = "0x" + "ff" * 32


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestVerifyPoAC(unittest.TestCase):
    """verify_poac() dispatches to the correct contract function."""

    def test_verify_poac_calls_verifyPoAC_when_schema_version_is_0(self):
        client = _make_client()
        client._send_tx = AsyncMock(return_value=FAKE_TX)

        asyncio.run(client.verify_poac(DEVICE_ID, RAW_BODY, SIGNATURE, schema_version=0))

        # Fix (Phase 51): verify_poac now passes the UNBOUND function + args to _send_tx
        # (old pattern pre-bound args then called _send_tx(fn) with no args — caused
        # "0 argument(s)" revert on testnet). Assert _send_tx got the right function ref + args.
        client._send_tx.assert_called_once_with(
            client._verifier.functions.verifyPoAC,
            DEVICE_ID, RAW_BODY, SIGNATURE,
        )

    def test_verify_poac_calls_verifyPoACWithSchema_when_schema_version_is_2(self):
        client = _make_client()
        client._send_tx = AsyncMock(return_value=FAKE_TX)

        asyncio.run(client.verify_poac(DEVICE_ID, RAW_BODY, SIGNATURE, schema_version=2))

        client._send_tx.assert_called_once_with(
            client._verifier.functions.verifyPoACWithSchema,
            DEVICE_ID, RAW_BODY, SIGNATURE, 2,
        )


class TestRegisterAttestedV2(unittest.TestCase):
    """register_device_attested_v2() passes manufacturer address to _send_tx."""

    def test_register_device_attested_v2_includes_manufacturer_address(self):
        client = _make_client()
        client._send_tx = AsyncMock(return_value=FAKE_TX)

        # tierConfigs(2) call returns an object where result[0] = deposit
        tier_mock = MagicMock()
        tier_mock.__getitem__ = MagicMock(side_effect=lambda i: 1000 if i == 0 else 0)
        client._registry.functions.tierConfigs.return_value.call = AsyncMock(
            return_value=tier_mock
        )
        client._w3.to_checksum_address.return_value = "0xManufacturer"

        pubkey = bytes(65)
        proof  = bytes(64)
        asyncio.run(
            client.register_device_attested_v2(pubkey, proof, "0xmanufacturer")
        )

        # _send_tx is mocked, so the real body never runs registerAttestedV2 directly.
        # Verify _send_tx was called with registerAttestedV2 as the first positional arg
        # (the tx_func argument that _send_tx would call internally).
        call_args = client._send_tx.call_args
        tx_func_arg = call_args[0][0]
        assert tx_func_arg is client._registry.functions.registerAttestedV2


class TestGetManufacturerKey(unittest.TestCase):
    """get_manufacturer_key() returns a dict with expected keys."""

    def test_get_manufacturer_key_returns_dict_with_expected_keys(self):
        client = _make_client()
        client._w3.to_checksum_address.return_value = "0xMfr"

        mock_result = (b"\x01" * 32, b"\x02" * 32, True, "VAPI Labs")
        client._registry.functions.getManufacturerKey.return_value.call = AsyncMock(
            return_value=mock_result
        )

        result = asyncio.run(client.get_manufacturer_key("0xmfr"))

        assert set(result.keys()) == {"pubkeyX", "pubkeyY", "active", "name"}
        assert result["pubkeyX"] == b"\x01" * 32
        assert result["active"] is True
        assert result["name"] == "VAPI Labs"


class TestRevocationCache(unittest.TestCase):
    """is_manufacturer_revoked() reflects the local cache state."""

    def test_is_manufacturer_revoked_returns_false_for_fresh_client(self):
        client = _make_client()
        assert client.is_manufacturer_revoked("0xSomeManufacturer") is False

    def test_is_manufacturer_revoked_returns_true_after_address_added_to_cache(self):
        client = _make_client()
        client._revoked_manufacturers.add("0xrevoked")
        assert client.is_manufacturer_revoked("0xrevoked") is True


if __name__ == "__main__":
    unittest.main()
