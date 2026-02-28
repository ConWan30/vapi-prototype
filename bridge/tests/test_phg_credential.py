"""
Phase 28 — PHGCredential Store and Batcher Tests

TestPHGCredentialStore (4):
1.  get_latest_pitl_proof returns None for unknown device
2.  get_latest_pitl_proof returns most recent row after store_pitl_proof
3.  store_credential_mint + get_credential_mint roundtrip
4.  store_credential_mint INSERT OR IGNORE — duplicate device_id silently ignored

TestPHGCredentialBatcher (4):
5.  batcher mints credential after successful checkpoint commit when PITL proof exists
6.  batcher skips credential mint when no PITL proof for device
7.  batcher skips credential mint when credential already recorded in store
8.  credential mint failure (chain raises) — non-fatal, checkpoint unaffected
"""

import sys
import tempfile
import os
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy deps before any bridge imports
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from vapi_bridge.store import Store


def _make_store() -> Store:
    tmpdir = tempfile.mkdtemp()
    return Store(os.path.join(tmpdir, "test_cred.db"))


def _insert_device(store: Store, device_id: str) -> None:
    with store._conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO devices "
            "(device_id, pubkey_hex, first_seen, last_seen, records_verified) "
            "VALUES (?, 'unknown', ?, ?, 0)",
            (device_id, time.time(), time.time()),
        )


# ===========================================================================
# TestPHGCredentialStore
# ===========================================================================

class TestPHGCredentialStore(unittest.TestCase):

    def test_1_get_latest_pitl_proof_returns_none_for_unknown_device(self):
        """get_latest_pitl_proof returns None when device has no PITL proof."""
        store = _make_store()
        result = store.get_latest_pitl_proof("unknown_device_xyz")
        self.assertIsNone(result)

    def test_2_get_latest_pitl_proof_returns_most_recent(self):
        """get_latest_pitl_proof returns the most recent row after store_pitl_proof."""
        store = _make_store()
        dev = "aabbccdd" * 8  # 64-char hex device id
        # Insert two proofs; expect second (most recent) returned
        store.store_pitl_proof(dev, "0xnull_1", "0xfc_1", 600)
        store.store_pitl_proof(dev, "0xnull_2", "0xfc_2", 750)
        result = store.get_latest_pitl_proof(dev)
        self.assertIsNotNone(result)
        self.assertEqual(result["device_id"], dev)
        self.assertEqual(result["nullifier_hash"], "0xnull_2")
        self.assertEqual(result["humanity_prob_int"], 750)

    def test_3_store_credential_mint_and_get_roundtrip(self):
        """store_credential_mint + get_credential_mint roundtrip."""
        store = _make_store()
        dev = "ccddee" * 10  # 60-char hex
        store.store_credential_mint(dev, credential_id=42, tx_hash="0xdeadbeef")
        result = store.get_credential_mint(dev)
        self.assertIsNotNone(result)
        self.assertEqual(result["device_id"], dev)
        self.assertEqual(result["credential_id"], 42)
        self.assertEqual(result["tx_hash"], "0xdeadbeef")
        self.assertGreater(result["minted_at"], 0)

    def test_4_store_credential_mint_insert_or_ignore_duplicate(self):
        """store_credential_mint INSERT OR IGNORE — duplicate device_id silently ignored."""
        store = _make_store()
        dev = "ff" * 32
        store.store_credential_mint(dev, credential_id=1, tx_hash="0xfirst")
        # Second call with same device_id should NOT raise and should NOT overwrite
        store.store_credential_mint(dev, credential_id=2, tx_hash="0xsecond")
        result = store.get_credential_mint(dev)
        # Only the first row should exist
        self.assertEqual(result["credential_id"], 1)
        self.assertEqual(result["tx_hash"], "0xfirst")


# ===========================================================================
# TestPHGCredentialBatcher
# ===========================================================================

class _MockChain:
    """Minimal chain mock for batcher tests."""
    def __init__(self, mint_returns="0xdeadbeef1234"):
        self._mint_returns = mint_returns
        self.mint_calls = []
        self.commit_checkpoint_calls = []

    async def commit_phg_checkpoint(self, dev_id, score, count, bio_hash):
        self.commit_checkpoint_calls.append(dev_id)
        return "0xcheckpoint_tx"

    async def wait_for_receipt(self, tx_hash, timeout=60):
        return {"status": 1}

    async def mint_phg_credential(self, dev_id, nullifier, commitment, hp_int):
        self.mint_calls.append((dev_id, nullifier, commitment, hp_int))
        if isinstance(self._mint_returns, Exception):
            raise self._mint_returns
        return self._mint_returns


def _make_batcher_with_store(store, chain):
    """Import Batcher and wire it with store + chain."""
    from vapi_bridge.batcher import Batcher
    from vapi_bridge.config import Config
    cfg = Config.__new__(Config)
    object.__setattr__(cfg, "phg_checkpoint_interval", 1)
    b = Batcher.__new__(Batcher)
    b._cfg = cfg
    b._store = store
    b._chain = chain
    return b


class TestPHGCredentialBatcher(unittest.TestCase):

    def _make_nominal_record(self, dev_id: str):
        """Create a minimal PoACRecord-like object."""
        rec = MagicMock()
        rec.device_id_hex = dev_id
        rec.inference_result = 0x20  # NOMINAL
        return rec

    def _setup_checkpoint_data(self, store, dev_id):
        """Insert device + set records_verified=1 so checkpoint triggers."""
        _insert_device(store, dev_id)
        with store._conn() as conn:
            conn.execute(
                "UPDATE devices SET records_verified=1 WHERE device_id=?", (dev_id,)
            )

    def test_5_batcher_mints_credential_when_pitl_proof_exists(self):
        """Batcher mints credential after successful checkpoint commit when PITL proof exists."""
        import asyncio
        store = _make_store()
        dev = "aa" * 32
        self._setup_checkpoint_data(store, dev)
        # Store a PITL proof
        store.store_pitl_proof(dev, "0xnull_abc", "0xfc_abc", 800)
        chain = _MockChain(mint_returns="0xmint_tx_123")

        async def run():
            b = _make_batcher_with_store(store, chain)
            records = [self._make_nominal_record(dev)]
            await b._maybe_commit_phg_checkpoints(records)

        asyncio.run(run())

        # Credential should be minted and stored
        cred = store.get_credential_mint(dev)
        self.assertIsNotNone(cred)
        self.assertEqual(cred["tx_hash"], "0xmint_tx_123")
        self.assertEqual(len(chain.mint_calls), 1)
        self.assertEqual(chain.mint_calls[0][0], dev)

    def test_6_batcher_skips_credential_when_no_pitl_proof(self):
        """Batcher skips credential mint when device has no PITL proof."""
        import asyncio
        store = _make_store()
        dev = "bb" * 32
        self._setup_checkpoint_data(store, dev)
        chain = _MockChain()

        async def run():
            b = _make_batcher_with_store(store, chain)
            records = [self._make_nominal_record(dev)]
            await b._maybe_commit_phg_checkpoints(records)

        asyncio.run(run())
        # No credential minted (no PITL proof)
        self.assertIsNone(store.get_credential_mint(dev))
        self.assertEqual(len(chain.mint_calls), 0)

    def test_7_batcher_skips_credential_when_already_minted(self):
        """Batcher skips credential mint when credential already recorded in store."""
        import asyncio
        store = _make_store()
        dev = "cc" * 32
        self._setup_checkpoint_data(store, dev)
        store.store_pitl_proof(dev, "0xnull_cc", "0xfc_cc", 700)
        # Pre-record that credential was already minted
        store.store_credential_mint(dev, credential_id=99, tx_hash="0xexisting_tx")
        chain = _MockChain()

        async def run():
            b = _make_batcher_with_store(store, chain)
            records = [self._make_nominal_record(dev)]
            await b._maybe_commit_phg_checkpoints(records)

        asyncio.run(run())
        # mint_phg_credential should NOT be called again
        self.assertEqual(len(chain.mint_calls), 0)
        # Store still has the original credential
        cred = store.get_credential_mint(dev)
        self.assertEqual(cred["tx_hash"], "0xexisting_tx")

    def test_8_credential_mint_failure_is_non_fatal(self):
        """Credential mint failure (chain raises) — non-fatal, checkpoint unaffected."""
        import asyncio
        store = _make_store()
        dev = "dd" * 32
        self._setup_checkpoint_data(store, dev)
        store.store_pitl_proof(dev, "0xnull_dd", "0xfc_dd", 500)
        # Chain raises during mint
        chain = _MockChain(mint_returns=RuntimeError("chain down"))

        async def run():
            b = _make_batcher_with_store(store, chain)
            records = [self._make_nominal_record(dev)]
            # Should NOT raise
            await b._maybe_commit_phg_checkpoints(records)

        # Must not raise
        asyncio.run(run())
        # Checkpoint was committed (chain.commit_checkpoint_calls has entry)
        self.assertIn(dev, chain.commit_checkpoint_calls)
        # Credential not stored (mint failed)
        self.assertIsNone(store.get_credential_mint(dev))


if __name__ == "__main__":
    unittest.main()
