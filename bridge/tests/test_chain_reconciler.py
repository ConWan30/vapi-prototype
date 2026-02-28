"""
Phase 25 — ChainReconciler Tests

Tests cover:
- store.mark_checkpoint_confirmed() updates confirmed column
- store.get_unconfirmed_checkpoints() age filter (includes old, excludes recent/confirmed)
- _reconcile_cycle marks confirmed for matching on-chain events
- _reconcile_cycle is non-fatal when getLogs/block_number raises
- No-op when no unconfirmed checkpoints exist
"""

import asyncio
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

from vapi_bridge.store import Store
from vapi_bridge.chain_reconciler import ChainReconciler


def _make_store(tmp_path):
    return Store(str(tmp_path / "test.db"))


def _insert_checkpoint(store, tx_hash, device_id="devA", confirmed=0, age_s=0):
    """Insert a phg_checkpoint row using store.store_phg_checkpoint()."""
    # Ensure device exists first
    with sqlite3.connect(store._db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO devices "
            "(device_id, pubkey_hex, first_seen, last_seen) VALUES (?,?,?,?)",
            (device_id, "pub", time.time(), time.time()),
        )
        conn.commit()
    # Use store method with correct signature
    store.store_phg_checkpoint(
        device_id,
        phg_score=10,
        record_count=5,
        bio_hash_hex="00" * 32,
        tx_hash=tx_hash,
        cumulative_score=10,
        confirmed=bool(confirmed),
    )
    # Adjust committed_at for age testing
    if age_s:
        committed_at = time.time() - age_s
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                "UPDATE phg_checkpoints SET committed_at=? WHERE tx_hash=?",
                (committed_at, tx_hash),
            )
            conn.commit()


def _make_chain_mock(block_number=100, events=None):
    """Build a chain mock with awaitable block_number."""
    async def _bn():
        return block_number

    chain = MagicMock()
    chain._w3 = MagicMock()
    chain._w3.eth.block_number = _bn()  # coroutine object — can only be awaited once
    chain.get_phg_checkpoint_events = AsyncMock(return_value=events or [])
    chain.wait_for_receipt = AsyncMock(return_value={"status": 1})
    return chain


# ===========================================================================
# 1. Store methods
# ===========================================================================

class TestReconcilerStoreMethods(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.store = _make_store(Path(self._tmpdir))

    def test_mark_checkpoint_confirmed_updates_row(self):
        _insert_checkpoint(self.store, "0xabc", confirmed=0)
        self.store.mark_checkpoint_confirmed("0xabc")
        with sqlite3.connect(self.store._db_path) as conn:
            row = conn.execute(
                "SELECT confirmed FROM phg_checkpoints WHERE tx_hash=?", ("0xabc",)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)

    def test_mark_checkpoint_confirmed_noop_for_unknown_tx(self):
        """Should not raise for unknown tx_hash."""
        self.store.mark_checkpoint_confirmed("0xunknown")  # no-op, no crash

    def test_get_unconfirmed_checkpoints_returns_old_unconfirmed(self):
        _insert_checkpoint(self.store, "0xold", device_id="devOld", confirmed=0, age_s=400)
        result = self.store.get_unconfirmed_checkpoints(age_s=300)
        tx_hashes = [r["tx_hash"] for r in result]
        self.assertIn("0xold", tx_hashes)

    def test_get_unconfirmed_checkpoints_excludes_recent(self):
        _insert_checkpoint(self.store, "0xrecent", device_id="devRcnt", confirmed=0, age_s=10)
        result = self.store.get_unconfirmed_checkpoints(age_s=300)
        tx_hashes = [r["tx_hash"] for r in result]
        self.assertNotIn("0xrecent", tx_hashes)

    def test_get_unconfirmed_checkpoints_excludes_confirmed(self):
        _insert_checkpoint(self.store, "0xconf", device_id="devConf", confirmed=1, age_s=400)
        result = self.store.get_unconfirmed_checkpoints(age_s=300)
        tx_hashes = [r["tx_hash"] for r in result]
        self.assertNotIn("0xconf", tx_hashes)


# ===========================================================================
# 2. ChainReconciler reconcile cycle
# ===========================================================================

class TestChainReconcilerCycle(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.store = _make_store(Path(self._tmpdir))

    def _run(self, coro):
        return asyncio.run(coro)

    def test_reconcile_noop_when_no_unconfirmed(self):
        """No events, no unconfirmed: _reconcile_cycle runs without error."""
        chain = _make_chain_mock(block_number=100, events=[])
        reconciler = ChainReconciler(self.store, chain, poll_interval=999.0)
        self._run(reconciler._reconcile_cycle())  # must not raise

    def test_block_number_error_is_non_fatal(self):
        """If block_number fetch fails, _reconcile_cycle returns early without crash."""
        async def _failing_bn():
            raise Exception("connection refused")

        chain = MagicMock()
        chain._w3 = MagicMock()
        chain._w3.eth.block_number = _failing_bn()
        chain.get_phg_checkpoint_events = AsyncMock(return_value=[])
        reconciler = ChainReconciler(self.store, chain, poll_interval=999.0)
        self._run(reconciler._reconcile_cycle())  # must not raise

    def test_getlogs_error_is_non_fatal(self):
        """get_phg_checkpoint_events failure should be caught; reconciler does not crash."""
        chain = _make_chain_mock(block_number=100)
        chain.get_phg_checkpoint_events = AsyncMock(side_effect=Exception("RPC error"))
        reconciler = ChainReconciler(self.store, chain, poll_interval=999.0)
        self._run(reconciler._reconcile_cycle())  # must not raise

    def test_reconcile_marks_matched_tx_confirmed(self):
        """When event tx_hash matches a stored checkpoint, it should be marked confirmed."""
        tx_hex = "abcd" + "00" * 30  # 32-byte hex without 0x prefix
        _insert_checkpoint(self.store, "0x" + tx_hex, device_id="devMatch", confirmed=0)
        event = {"transactionHash": bytes.fromhex(tx_hex)}
        chain = _make_chain_mock(block_number=50, events=[event])
        reconciler = ChainReconciler(self.store, chain, poll_interval=999.0)
        self._run(reconciler._reconcile_cycle())
        with sqlite3.connect(self.store._db_path) as conn:
            row = conn.execute(
                "SELECT confirmed FROM phg_checkpoints WHERE tx_hash=?",
                ("0x" + tx_hex,)
            ).fetchone()
        if row:
            self.assertEqual(row[0], 1)

    def test_last_block_advances_after_cycle(self):
        """After a successful cycle, _last_block should advance to current_block."""
        chain = _make_chain_mock(block_number=200, events=[])
        reconciler = ChainReconciler(self.store, chain, poll_interval=999.0)
        self._run(reconciler._reconcile_cycle())
        self.assertEqual(reconciler._last_block, 200)


if __name__ == "__main__":
    unittest.main()
