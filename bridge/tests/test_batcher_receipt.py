"""
Phase 25 — Batcher Receipt Verification Tests

Tests cover:
- store_phg_checkpoint(confirmed=True) stores confirmed=1
- store_phg_checkpoint(confirmed=False) stores confirmed=0
- store_phg_checkpoint() default confirmed is False (0)
- get_last_phg_checkpoint() filters WHERE confirmed=1 (unconfirmed not returned as baseline)
- Confirmed checkpoint is returned as baseline
- Batcher: successful receipt → confirmed=True stored
- Batcher: reverted tx → confirmed=False stored
- Batcher: no tx_hash → no row stored
"""

import asyncio
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# ---------------------------------------------------------------------------
# Stub heavy deps
# ---------------------------------------------------------------------------
for _mod in ("web3", "web3.exceptions", "eth_account"):
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

import os
os.environ.setdefault("POAC_VERIFIER_ADDRESS", "0x" + "12" * 20)
os.environ.setdefault("BRIDGE_PRIVATE_KEY", "0x" + "aa" * 32)

from vapi_bridge.store import Store


def _fresh_store():
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


def _insert_nominal_record(store, device_id, confidence=200, unique_byte=0):
    """Insert a minimal NOMINAL record into the store."""
    from vapi_bridge.codec import PoACRecord
    from vapi_bridge.store import STATUS_VERIFIED
    rh = bytes([unique_byte]) + b"\x00" * 31
    rec = PoACRecord(
        prev_poac_hash=b"\x00" * 32,
        sensor_commitment=b"\x00" * 32,
        model_manifest_hash=b"\x00" * 32,
        world_model_hash=b"\x00" * 32,
        inference_result=0x20,
        action_code=0x01,
        confidence=confidence,
        battery_pct=80,
        monotonic_ctr=unique_byte,
        timestamp_ms=int(time.time() * 1000),
        latitude=0.0,
        longitude=0.0,
        bounty_id=0,
        signature=b"\x00" * 64,
        record_hash=rh,
        device_id=bytes.fromhex(device_id),
        raw_body=b"\x00" * 164,
    )
    raw = b"\x00" * 228
    store.insert_record(rec, raw)


# ===========================================================================
# 1. store_phg_checkpoint confirmed parameter
# ===========================================================================

class TestStorePhgCheckpointConfirmed(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device("devA" + "00" * 30, "pubkey")

    def test_confirmed_true_stored_as_1(self):
        self.store.store_phg_checkpoint(
            "devA" + "00" * 30, phg_score=50, record_count=10,
            bio_hash_hex="01" * 32, tx_hash="0xaaa",
            cumulative_score=50, confirmed=True,
        )
        with sqlite3.connect(self.store._db_path) as conn:
            row = conn.execute(
                "SELECT confirmed FROM phg_checkpoints WHERE tx_hash=?", ("0xaaa",)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)

    def test_confirmed_false_stored_as_0(self):
        self.store.store_phg_checkpoint(
            "devA" + "00" * 30, phg_score=50, record_count=10,
            bio_hash_hex="02" * 32, tx_hash="0xbbb",
            cumulative_score=50, confirmed=False,
        )
        with sqlite3.connect(self.store._db_path) as conn:
            row = conn.execute(
                "SELECT confirmed FROM phg_checkpoints WHERE tx_hash=?", ("0xbbb",)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 0)

    def test_default_confirmed_is_false(self):
        """When confirmed not passed, default should be False (0)."""
        self.store.store_phg_checkpoint(
            "devA" + "00" * 30, phg_score=30, record_count=5,
            bio_hash_hex="03" * 32, tx_hash="0xccc",
            cumulative_score=30,
        )
        with sqlite3.connect(self.store._db_path) as conn:
            row = conn.execute(
                "SELECT confirmed FROM phg_checkpoints WHERE tx_hash=?", ("0xccc",)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 0)


# ===========================================================================
# 2. get_last_phg_checkpoint confirmed filter
# ===========================================================================

class TestGetLastCheckpointConfirmedFilter(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device("devB" + "00" * 30, "pubkey")

    def test_unconfirmed_checkpoint_not_returned_as_baseline(self):
        """Unconfirmed checkpoints must NOT serve as delta baseline (reverted/timeout)."""
        self.store.store_phg_checkpoint(
            "devB" + "00" * 30, phg_score=100, record_count=10,
            bio_hash_hex="04" * 32, tx_hash="0xrev",
            cumulative_score=100, confirmed=False,
        )
        last = self.store.get_last_phg_checkpoint("devB" + "00" * 30)
        self.assertIsNone(last)

    def test_confirmed_checkpoint_is_returned_as_baseline(self):
        """Confirmed checkpoint should be returned by get_last_phg_checkpoint."""
        self.store.store_phg_checkpoint(
            "devB" + "00" * 30, phg_score=50, record_count=5,
            bio_hash_hex="05" * 32, tx_hash="0xok",
            cumulative_score=50, confirmed=True,
        )
        last = self.store.get_last_phg_checkpoint("devB" + "00" * 30)
        self.assertIsNotNone(last)
        self.assertEqual(last["last_committed_score"], 50)

    def test_no_checkpoint_returns_none(self):
        """Device with no checkpoints at all returns None."""
        last = self.store.get_last_phg_checkpoint("devB" + "00" * 30)
        self.assertIsNone(last)


# ===========================================================================
# 3. Batcher receipt integration
# ===========================================================================

class TestBatcherReceiptVerification(unittest.TestCase):
    """Integration tests: _maybe_commit_phg_checkpoints stores confirmed flag correctly."""

    DEVICE_ID = "cc" * 32

    def setUp(self):
        self.store = _fresh_store()
        self.store.upsert_device(self.DEVICE_ID, "pubkey")

    def _make_batcher(self, chain_mock):
        from vapi_bridge.batcher import Batcher
        from vapi_bridge.config import Config
        cfg_mock = MagicMock(spec=Config)
        cfg_mock.phg_registry_address = "0x" + "ff" * 20
        cfg_mock.phg_checkpoint_interval = 10
        return Batcher(cfg_mock, self.store, chain_mock)

    def _make_nominal_record(self):
        rec = MagicMock()
        rec.inference_result = 0x20
        rec.device_id_hex = self.DEVICE_ID
        return rec

    def _run(self, coro):
        return asyncio.run(coro)

    def _setup_device_at_boundary(self):
        """Put device at interval boundary so checkpoint fires."""
        self.store.increment_device_verified(self.DEVICE_ID, 10)
        _insert_nominal_record(self.store, self.DEVICE_ID, confidence=200, unique_byte=1)

    def test_successful_receipt_stores_confirmed_true(self):
        self._setup_device_at_boundary()
        chain = MagicMock()
        chain.commit_phg_checkpoint = AsyncMock(return_value="0xsuccess")
        chain.wait_for_receipt = AsyncMock(return_value={"status": 1})
        batcher = self._make_batcher(chain)
        records = [self._make_nominal_record()]
        self._run(batcher._maybe_commit_phg_checkpoints(records))
        with sqlite3.connect(self.store._db_path) as conn:
            rows = conn.execute("SELECT confirmed FROM phg_checkpoints").fetchall()
        self.assertGreater(len(rows), 0)
        self.assertEqual(rows[-1][0], 1)

    def test_reverted_receipt_stores_confirmed_false(self):
        self._setup_device_at_boundary()
        chain = MagicMock()
        chain.commit_phg_checkpoint = AsyncMock(return_value="0xreverted")
        chain.wait_for_receipt = AsyncMock(return_value={"status": 0})
        batcher = self._make_batcher(chain)
        records = [self._make_nominal_record()]
        self._run(batcher._maybe_commit_phg_checkpoints(records))
        with sqlite3.connect(self.store._db_path) as conn:
            rows = conn.execute("SELECT confirmed FROM phg_checkpoints").fetchall()
        self.assertGreater(len(rows), 0)
        self.assertEqual(rows[-1][0], 0)

    def test_no_commit_when_no_tx_hash(self):
        """When commit_phg_checkpoint returns None, no row stored."""
        self._setup_device_at_boundary()
        chain = MagicMock()
        chain.commit_phg_checkpoint = AsyncMock(return_value=None)
        chain.wait_for_receipt = AsyncMock(return_value={"status": 1})
        batcher = self._make_batcher(chain)
        records = [self._make_nominal_record()]
        self._run(batcher._maybe_commit_phg_checkpoints(records))
        with sqlite3.connect(self.store._db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM phg_checkpoints").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
