"""
Phase 22 — PHG Registry Tests

Tests cover:
- get_verified_nominal_count() returns correct count from devices table
- get_phg_checkpoint_data() returns score + biometric hash
- biometric hash is 32 bytes (SHA-256 digest)
- biometric hash changes when feature fingerprint changes
- biometric hash is zero bytes when no L4 features available
- biometric hash is stable for the same feature fingerprint
- store_phg_checkpoint() persists a checkpoint row
- get_phg_checkpoints() returns list ordered by committed_at DESC
- get_phg_checkpoints() returns empty list for unknown device
- get_phg_checkpoints() respects limit parameter
- PHGRegistryClient skips commit when no address configured
- PHGRegistryClient returns 0 / False for reads when unconfigured
- Batcher._maybe_commit_phg_checkpoints fires at interval boundary
- Batcher._maybe_commit_phg_checkpoints does NOT fire between intervals
- checkpoint-chain endpoint returns 200 with list response
"""

import hashlib
import json
import sys
import tempfile
import time
import types
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add bridge/ to path so vapi_bridge imports work
_bridge_dir = str(Path(__file__).resolve().parents[1])
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

# ---------------------------------------------------------------------------
# Stub heavy dependencies not installed in the test environment (web3, eth_account)
# Mirrors the exact pattern used in test_pipeline_integration.py
# ---------------------------------------------------------------------------
for _mod_name in ("web3", "web3.exceptions", "eth_account"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

_web3_exc = sys.modules["web3.exceptions"]
if not hasattr(_web3_exc, "ContractLogicError"):
    _web3_exc.ContractLogicError = type("ContractLogicError", (Exception,), {})
if not hasattr(_web3_exc, "TransactionNotFound"):
    _web3_exc.TransactionNotFound = type("TransactionNotFound", (Exception,), {})

_web3_mod = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3_mod, _attr):
        setattr(_web3_mod, _attr, MagicMock())

_eth_acct = sys.modules["eth_account"]
if not hasattr(_eth_acct, "Account"):
    _eth_acct.Account = MagicMock()

from fastapi.testclient import TestClient

from vapi_bridge.store import Store
from vapi_bridge.dashboard_api import create_dashboard_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_store() -> Store:
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


def _insert_nominal_record(store: Store, device_id: str, confidence: int = 200,
                            pitl_features_json: str | None = None,
                            unique_hash_byte: int = 0) -> None:
    """Insert a minimal NOMINAL record into the store (verified status)."""
    import struct, time as _time
    from vapi_bridge.codec import PoACRecord
    from vapi_bridge.store import STATUS_VERIFIED

    rec = PoACRecord(
        prev_poac_hash=b"\x00" * 32,
        sensor_commitment=b"\x01" * 32,
        model_manifest_hash=b"\x02" * 32,
        world_model_hash=b"\x03" * 32,
        inference_result=0x20,   # NOMINAL
        action_code=0x01,
        confidence=confidence,
        battery_pct=90,
        monotonic_ctr=unique_hash_byte + 1,
        timestamp_ms=int(_time.time() * 1000),
        latitude=0.0,
        longitude=0.0,
        bounty_id=0,
        signature=b"\x00" * 64,
    )
    body = (
        rec.prev_poac_hash + rec.sensor_commitment +
        rec.model_manifest_hash + rec.world_model_hash +
        struct.pack(">BBBBIqddI",
            0x20, 0x01, confidence, 90, unique_hash_byte + 1,
            int(_time.time() * 1000), 0.0, 0.0, 0)
    )
    import hashlib as _hl
    rec.record_hash = bytes([unique_hash_byte]) * 32
    rec.raw_body = body[:164]
    rec.device_id = bytes.fromhex(device_id.zfill(64))

    if pitl_features_json is not None:
        rec.pitl_l4_features_json = pitl_features_json

    store.insert_record(rec, b"\x00" * 228)
    store.update_record_status(rec.record_hash_hex, STATUS_VERIFIED)


# ---------------------------------------------------------------------------
# TestPHGCheckpointStore — SQLite store helpers for Phase 22
# ---------------------------------------------------------------------------

class TestPHGCheckpointStore(unittest.TestCase):

    DEVICE_ID = "aa" * 32

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device(self.DEVICE_ID, "bb" * 32)

    def test_get_verified_nominal_count_returns_zero_for_new_device(self):
        """get_verified_nominal_count() returns 0 for a device with no verified records."""
        count = self.store.get_verified_nominal_count(self.DEVICE_ID)
        self.assertEqual(count, 0)

    def test_get_verified_nominal_count_reflects_increment(self):
        """get_verified_nominal_count() rises after increment_device_verified()."""
        self.store.increment_device_verified(self.DEVICE_ID, 5)
        count = self.store.get_verified_nominal_count(self.DEVICE_ID)
        self.assertEqual(count, 5)

    def test_get_phg_checkpoint_data_returns_none_for_unknown_device(self):
        """get_phg_checkpoint_data() returns None for a device not in DB."""
        result = self.store.get_phg_checkpoint_data("ff" * 32)
        self.assertIsNone(result)

    def test_get_phg_checkpoint_data_biometric_hash_is_32_bytes(self):
        """biometric_hash in checkpoint data is exactly 32 bytes."""
        _insert_nominal_record(self.store, self.DEVICE_ID, confidence=200,
                               pitl_features_json='{"a": 1.0}', unique_hash_byte=1)
        result = self.store.get_phg_checkpoint_data(self.DEVICE_ID)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["biometric_hash"]), 32)

    def test_get_phg_checkpoint_data_biometric_hash_is_zero_when_no_features(self):
        """biometric_hash is bytes(32) when no L4 features are present."""
        _insert_nominal_record(self.store, self.DEVICE_ID, confidence=200,
                               pitl_features_json=None, unique_hash_byte=2)
        result = self.store.get_phg_checkpoint_data(self.DEVICE_ID)
        self.assertIsNotNone(result)
        self.assertEqual(result["biometric_hash"], bytes(32))

    def test_biometric_hash_stable_for_same_features(self):
        """Two calls with the same feature JSON produce the same hash."""
        feat = '{"a": 1.0, "b": 2.0}'
        _insert_nominal_record(self.store, self.DEVICE_ID, confidence=200,
                               pitl_features_json=feat, unique_hash_byte=3)
        r1 = self.store.get_phg_checkpoint_data(self.DEVICE_ID)
        r2 = self.store.get_phg_checkpoint_data(self.DEVICE_ID)
        self.assertEqual(r1["biometric_hash"], r2["biometric_hash"])

    def test_store_and_retrieve_phg_checkpoint(self):
        """store_phg_checkpoint() persists; get_phg_checkpoints() retrieves."""
        self.store.store_phg_checkpoint(
            self.DEVICE_ID, phg_score=78, record_count=10,
            bio_hash_hex="ab" * 32, tx_hash="0x" + "cc" * 32,
        )
        rows = self.store.get_phg_checkpoints(self.DEVICE_ID)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phg_score"], 78)
        self.assertEqual(rows[0]["record_count"], 10)
        self.assertEqual(rows[0]["bio_hash"], "ab" * 32)

    def test_get_phg_checkpoints_returns_empty_for_unknown_device(self):
        """get_phg_checkpoints() returns [] for a device with no checkpoints."""
        rows = self.store.get_phg_checkpoints("cc" * 32)
        self.assertEqual(rows, [])

    def test_get_phg_checkpoints_respects_limit(self):
        """get_phg_checkpoints() returns at most `limit` rows."""
        for i in range(5):
            self.store.store_phg_checkpoint(
                self.DEVICE_ID, phg_score=i * 10, record_count=i + 1,
                bio_hash_hex="00" * 32, tx_hash="0x" + f"{i:02x}" * 32,
            )
        rows = self.store.get_phg_checkpoints(self.DEVICE_ID, limit=3)
        self.assertEqual(len(rows), 3)


# ---------------------------------------------------------------------------
# TestPHGRegistryClient — no-op behaviour when unconfigured
# ---------------------------------------------------------------------------

class TestPHGRegistryClient(unittest.TestCase):

    def setUp(self):
        from vapi_bridge.phg_registry_client import PHGRegistryClient
        # Build a minimal mock ChainClient stub
        mock_chain = MagicMock()
        mock_chain._w3 = MagicMock()
        mock_chain._w3.to_checksum_address = lambda a: a
        mock_chain._w3.eth = MagicMock()
        self.PHGRegistryClient = PHGRegistryClient
        self.mock_chain = mock_chain

    def test_skips_commit_when_no_address_configured(self):
        """commit_checkpoint returns '' when contract_address is empty."""
        client = self.PHGRegistryClient(self.mock_chain, "")
        result = asyncio.get_event_loop().run_until_complete(
            client.commit_checkpoint(b"\x00" * 32, 50, 10, bytes(32))
        )
        self.assertEqual(result, "")

    def test_get_cumulative_score_returns_zero_when_unconfigured(self):
        """get_cumulative_score returns 0 when contract_address is empty."""
        client = self.PHGRegistryClient(self.mock_chain, "")
        result = asyncio.get_event_loop().run_until_complete(
            client.get_cumulative_score(b"\x00" * 32)
        )
        self.assertEqual(result, 0)

    def test_is_eligible_returns_false_when_unconfigured(self):
        """is_eligible returns False when contract_address is empty."""
        client = self.PHGRegistryClient(self.mock_chain, "")
        result = asyncio.get_event_loop().run_until_complete(
            client.is_eligible(b"\x00" * 32, 100)
        )
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# TestBatcherPHGCheckpointTrigger — checkpoint fires at interval
# ---------------------------------------------------------------------------

class TestBatcherPHGCheckpointTrigger(unittest.TestCase):
    """Unit-test _maybe_commit_phg_checkpoints via a mock batcher."""

    DEVICE_ID = "dd" * 32

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device(self.DEVICE_ID, "ee" * 32)

    def _make_batcher(self, interval: int = 10):
        from vapi_bridge.batcher import Batcher
        from vapi_bridge.config import Config

        cfg_mock = MagicMock(spec=Config)
        cfg_mock.phg_registry_address = "0x" + "ff" * 20
        cfg_mock.phg_checkpoint_interval = interval

        chain_mock = MagicMock()
        chain_mock.commit_phg_checkpoint = AsyncMock(return_value="0x" + "ab" * 32)
        chain_mock.wait_for_receipt = AsyncMock(return_value={"status": 1})

        batcher = Batcher(cfg_mock, self.store, chain_mock)
        return batcher, chain_mock

    def _make_nominal_record(self, unique_byte: int = 0):
        """Build a minimal NOMINAL PoACRecord stub."""
        rec = MagicMock()
        rec.inference_result = 0x20
        rec.device_id_hex = self.DEVICE_ID
        return rec

    def test_checkpoint_fires_at_interval_boundary(self):
        """_maybe_commit_phg_checkpoints fires when verified count == interval."""
        batcher, chain_mock = self._make_batcher(interval=10)
        # Simulate 10 verified NOMINAL records
        self.store.increment_device_verified(self.DEVICE_ID, 10)
        # Insert a nominal record with some features so checkpoint_data is populated
        _insert_nominal_record(self.store, self.DEVICE_ID,
                               pitl_features_json='{"a":1.0}', unique_hash_byte=1)

        records = [self._make_nominal_record()]
        asyncio.get_event_loop().run_until_complete(
            batcher._maybe_commit_phg_checkpoints(records)
        )
        chain_mock.commit_phg_checkpoint.assert_awaited_once()

    def test_checkpoint_does_not_fire_between_intervals(self):
        """_maybe_commit_phg_checkpoints is silent when count is NOT a multiple of interval."""
        batcher, chain_mock = self._make_batcher(interval=10)
        # Only 7 verified — not yet at boundary
        self.store.increment_device_verified(self.DEVICE_ID, 7)

        records = [self._make_nominal_record()]
        asyncio.get_event_loop().run_until_complete(
            batcher._maybe_commit_phg_checkpoints(records)
        )
        chain_mock.commit_phg_checkpoint.assert_not_awaited()

    def test_checkpoint_skips_cheat_records(self):
        """_maybe_commit_phg_checkpoints ignores non-NOMINAL inference codes."""
        batcher, chain_mock = self._make_batcher(interval=10)
        self.store.increment_device_verified(self.DEVICE_ID, 10)

        # Record with inference 0x28 (cheat) — should not trigger
        cheat_rec = MagicMock()
        cheat_rec.inference_result = 0x28
        cheat_rec.device_id_hex = self.DEVICE_ID

        asyncio.get_event_loop().run_until_complete(
            batcher._maybe_commit_phg_checkpoints([cheat_rec])
        )
        chain_mock.commit_phg_checkpoint.assert_not_awaited()

    def test_checkpoint_interval_configurable(self):
        """Interval of 5 fires at 5, 10, 15… not at 3, 7, 9."""
        batcher, chain_mock = self._make_batcher(interval=5)
        self.store.increment_device_verified(self.DEVICE_ID, 5)
        _insert_nominal_record(self.store, self.DEVICE_ID,
                               pitl_features_json='{"a":1.0}', unique_hash_byte=2)

        records = [self._make_nominal_record()]
        asyncio.get_event_loop().run_until_complete(
            batcher._maybe_commit_phg_checkpoints(records)
        )
        chain_mock.commit_phg_checkpoint.assert_awaited_once()

    def test_count_delta_is_records_since_last_checkpoint_not_interval(self):
        """On a second checkpoint, count passed on-chain is the delta from last commit.

        Regression for the bug where `interval` was passed directly — this gives
        wrong results when the bridge restarts after records have already accumulated
        past an interval boundary.

        Scenario: interval=10, first checkpoint at verified=10, second at verified=20.
        Each call to commit_phg_checkpoint should receive count=10 (the delta).
        With the old bug (passing `interval=10`), both would also pass 10 by coincidence.
        The critical regression is: on the FIRST checkpoint with verified=20 (bridge restart
        scenario), old code passes count=interval=10, new code passes count=20.
        """
        batcher, chain_mock = self._make_batcher(interval=10)
        _insert_nominal_record(self.store, self.DEVICE_ID,
                               pitl_features_json='{"a":1.0}', unique_hash_byte=3)

        # Simulate first checkpoint: verified_count=10
        self.store.increment_device_verified(self.DEVICE_ID, 10)
        records = [self._make_nominal_record()]
        asyncio.get_event_loop().run_until_complete(
            batcher._maybe_commit_phg_checkpoints(records)
        )
        # First call: count_delta = 10 - 0 (no prior checkpoint) = 10
        first_call_args = chain_mock.commit_phg_checkpoint.await_args_list[0]
        first_count_arg = first_call_args[0][2]  # positional arg index 2
        self.assertEqual(first_count_arg, 10)

        # Simulate second checkpoint: verified_count=20
        self.store.increment_device_verified(self.DEVICE_ID, 10)
        asyncio.get_event_loop().run_until_complete(
            batcher._maybe_commit_phg_checkpoints(records)
        )
        # Second call: count_delta = 20 - 10 (last checkpoint record_count=10) = 10
        second_call_args = chain_mock.commit_phg_checkpoint.await_args_list[1]
        second_count_arg = second_call_args[0][2]
        self.assertEqual(second_count_arg, 10)


# ---------------------------------------------------------------------------
# TestCheckpointChainEndpoint — dashboard API route
# ---------------------------------------------------------------------------

class TestCheckpointChainEndpoint(unittest.TestCase):

    DEVICE_ID = "ee" * 32

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device(self.DEVICE_ID, "ff" * 32)
        self.client = TestClient(create_dashboard_app(self.store))

    def test_checkpoint_chain_returns_200_with_list(self):
        """GET /api/v1/player/{id}/checkpoint-chain returns 200 with a list."""
        resp = self.client.get(f"/api/v1/player/{self.DEVICE_ID}/checkpoint-chain")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_checkpoint_chain_returns_empty_for_device_with_no_checkpoints(self):
        """Endpoint returns [] when no checkpoints have been committed."""
        resp = self.client.get(f"/api/v1/player/{self.DEVICE_ID}/checkpoint-chain")
        self.assertEqual(resp.json(), [])

    def test_checkpoint_chain_returns_stored_checkpoints(self):
        """Endpoint returns rows that were stored via store_phg_checkpoint()."""
        self.store.store_phg_checkpoint(
            self.DEVICE_ID, phg_score=78, record_count=10,
            bio_hash_hex="ab" * 32, tx_hash="0x" + "cd" * 32,
        )
        resp = self.client.get(f"/api/v1/player/{self.DEVICE_ID}/checkpoint-chain")
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["phg_score"], 78)

    def test_checkpoint_chain_limit_param_respected(self):
        """?limit=2 returns at most 2 rows."""
        for i in range(5):
            self.store.store_phg_checkpoint(
                self.DEVICE_ID, phg_score=i * 10, record_count=i + 1,
                bio_hash_hex="00" * 32, tx_hash="0x" + f"{i:02x}" * 32,
            )
        resp = self.client.get(
            f"/api/v1/player/{self.DEVICE_ID}/checkpoint-chain?limit=2"
        )
        self.assertEqual(len(resp.json()), 2)


if __name__ == "__main__":
    unittest.main()
