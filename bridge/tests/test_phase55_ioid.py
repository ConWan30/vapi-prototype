"""
Phase 55 tests — ioID Device Identity Registry.

Tests:
1. test_ensure_ioid_registered_new_device      — isRegistered False → register() called → DID persisted
2. test_ensure_ioid_registered_idempotent      — isRegistered True → no register() call
3. test_did_derivation_format                  — device_id bytes → did:io:0x<20bytes> pattern
4. test_get_ioid_status_tool_registered        — store pre-seeded → BridgeAgent returns registered: true + did
5. test_ioid_devices_table_schema              — Store(_init_schema) twice → idempotent
"""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_bridge_dir = os.path.join(os.path.dirname(__file__), "..")
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

# Stub web3 and eth_account so chain.py imports without real dependencies
import types

_web3_mod = types.ModuleType("web3")
_web3_exc = types.ModuleType("web3.exceptions")


class _AsyncWeb3Stub:
    def __init__(self, *a, **kw):
        pass
    @property
    def eth(self):
        return self
    def contract(self, address=None, abi=None):
        return MagicMock()
    def to_checksum_address(self, addr):
        return addr


class _AsyncHTTPProviderStub:
    def __init__(self, *a, **kw):
        pass


_web3_mod.AsyncWeb3         = _AsyncWeb3Stub
_web3_mod.AsyncHTTPProvider = _AsyncHTTPProviderStub
_web3_exc.ContractLogicError  = Exception
_web3_exc.TransactionNotFound = Exception

sys.modules.setdefault("web3",            _web3_mod)
sys.modules.setdefault("web3.exceptions", _web3_exc)

_eth_acc = types.ModuleType("eth_account")
_eth_acc.Account = MagicMock()
sys.modules.setdefault("eth_account", _eth_acc)


class TestEnsureIoIDRegisteredNewDevice(unittest.IsolatedAsyncioTestCase):
    """test_ensure_ioid_registered_new_device"""

    async def test_ensure_ioid_registered_new_device(self):
        from vapi_bridge.chain import ChainClient

        device_id = "a" * 64
        dev_bytes  = bytes.fromhex(device_id)[:32]
        device_address = "0x" + dev_bytes[-20:].hex()
        expected_did = f"did:io:{device_address}"

        # Build a minimal ChainClient with a mocked _ioid_registry
        cfg_mock = MagicMock()
        cfg_mock.iotex_rpc_url                 = "http://localhost:8545"
        cfg_mock.chain_id                      = 4690
        cfg_mock.bridge_private_key            = "0x" + "aa" * 32
        cfg_mock.bridge_private_key_source     = "env"
        cfg_mock.verifier_address              = ""
        cfg_mock.bounty_market_address         = ""
        cfg_mock.device_registry_address       = ""
        cfg_mock.phg_registry_address          = ""
        cfg_mock.identity_registry_address     = ""
        cfg_mock.pitl_session_registry_address = ""
        cfg_mock.phg_credential_address        = ""
        cfg_mock.federated_threat_registry_address = ""
        cfg_mock.team_aggregator_address       = ""
        cfg_mock.progress_attestation_address  = ""
        cfg_mock.ioid_registry_address         = "0x1234567890123456789012345678901234567890"
        cfg_mock.tournament_passport_address   = ""

        store_mock = MagicMock()
        store_mock.get_ioid_device.return_value = None  # not in local store

        with patch("vapi_bridge.chain.AsyncWeb3") as w3_cls, \
             patch("vapi_bridge.chain.AsyncHTTPProvider"):
            w3_instance = MagicMock()
            w3_cls.return_value = w3_instance
            w3_instance.to_checksum_address.side_effect = lambda a: a
            w3_instance.eth.contract.return_value = MagicMock()

            client = ChainClient.__new__(ChainClient)
            client._w3 = w3_instance
            client._account = MagicMock()
            client._account.address = "0xBridge"
            client._nonce = 0
            client._nonce_lock = __import__("asyncio").Lock()

            # Wire mocked ioid registry
            ioid_contract = MagicMock()
            ioid_contract.address = "0x1234567890123456789012345678901234567890"
            # isRegistered check via eth.call → returns hex 0 (not registered)
            w3_instance.eth.call = AsyncMock(
                return_value=bytes.fromhex("00" * 31 + "00")
            )
            ioid_contract.encodeABI.return_value = b"\x00"
            client._ioid_registry = ioid_contract
            client._tournament_passport = None

            # _send_tx mock — returns fake tx hash
            client._send_tx = AsyncMock(return_value="0xdeadbeef")
            w3_instance.to_checksum_address.side_effect = lambda a: a

            result = await client.ensure_ioid_registered(device_id, store_mock)

        self.assertEqual(result, expected_did)
        # register() should have been called via _send_tx
        client._send_tx.assert_called_once()
        # store should have been updated
        store_mock.store_ioid_device.assert_called_once()
        args = store_mock.store_ioid_device.call_args[0]
        self.assertEqual(args[0], device_id)
        self.assertEqual(args[2], expected_did)


class TestEnsureIoIDRegisteredIdempotent(unittest.IsolatedAsyncioTestCase):
    """test_ensure_ioid_registered_idempotent — already in local store → no register() call"""

    async def test_ensure_ioid_registered_idempotent(self):
        from vapi_bridge.chain import ChainClient

        device_id = "b" * 64
        dev_bytes  = bytes.fromhex(device_id)[:32]
        device_address = "0x" + dev_bytes[-20:].hex()
        expected_did = f"did:io:{device_address}"

        store_mock = MagicMock()
        store_mock.get_ioid_device.return_value = {
            "device_id":     device_id,
            "device_address": device_address,
            "did":            expected_did,
            "tx_hash":        "0xabc",
            "registered_at":  time.time(),
        }

        client = ChainClient.__new__(ChainClient)
        client._w3 = MagicMock()
        client._w3.to_checksum_address.side_effect = lambda a: a
        client._ioid_registry = MagicMock()
        client._ioid_registry.address = "0x1234"
        client._send_tx = AsyncMock(return_value="0xtx")

        result = await client.ensure_ioid_registered(device_id, store_mock)

        self.assertEqual(result, expected_did)
        # register() must NOT be called since device was already in local store
        client._send_tx.assert_not_called()


class TestDIDDerivationFormat(unittest.TestCase):
    """test_did_derivation_format — verify DID pattern"""

    def test_did_derivation_format(self):
        import re
        device_id = "0123456789abcdef" * 4  # 64-char hex

        dev_bytes     = bytes.fromhex(device_id)[:32]
        device_address = "0x" + dev_bytes[-20:].hex()
        did = f"did:io:{device_address}"

        # Must match W3C DID pattern: did:io:0x<40 hex chars>
        pattern = r"^did:io:0x[0-9a-fA-F]{40}$"
        self.assertRegex(did, pattern, f"DID '{did}' does not match expected pattern")
        self.assertTrue(did.startswith("did:io:0x"))
        self.assertEqual(len(did), len("did:io:0x") + 40)


class TestGetIoIDStatusToolRegistered(unittest.TestCase):
    """test_get_ioid_status_tool_registered — store pre-seeded → BridgeAgent returns registered=True"""

    def test_get_ioid_status_tool_registered(self):
        from vapi_bridge.bridge_agent import BridgeAgent

        device_id = "c" * 64
        dev_bytes  = bytes.fromhex(device_id)[:32]
        device_address = "0x" + dev_bytes[-20:].hex()
        expected_did = f"did:io:{device_address}"

        store_mock = MagicMock()
        store_mock.get_ioid_device.return_value = {
            "device_id":      device_id,
            "device_address": device_address,
            "did":            expected_did,
            "tx_hash":        "0xdeadbeef",
            "registered_at":  time.time(),
        }

        cfg_mock = MagicMock()
        agent = BridgeAgent(cfg_mock, store_mock)
        result = agent._execute_tool("get_ioid_status", {"device_id": device_id})

        self.assertTrue(result.get("registered"), f"Expected registered=True, got {result}")
        self.assertEqual(result.get("did"), expected_did)
        self.assertIn("registered_at", result)


class TestIoIDDevicesTableSchema(unittest.TestCase):
    """test_ioid_devices_table_schema — Store._init_schema() idempotent"""

    def test_ioid_devices_table_schema(self):
        from vapi_bridge.store import Store

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_ioid.db")
            # First init
            s1 = Store(db_path)
            # Second init — must not raise
            s2 = Store(db_path)
            # Verify table exists by inserting and retrieving
            s1.store_ioid_device(
                device_id="d" * 64,
                device_address="0x" + "d" * 40,
                did="did:io:0x" + "d" * 40,
                tx_hash="0xtx1",
            )
            row = s2.get_ioid_device("d" * 64)
            self.assertIsNotNone(row)
            self.assertEqual(row["did"], "did:io:0x" + "d" * 40)
            # get_all_ioid_devices
            all_devs = s2.get_all_ioid_devices()
            self.assertEqual(len(all_devs), 1)


if __name__ == "__main__":
    unittest.main()
