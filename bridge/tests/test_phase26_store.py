"""
Phase 27 — Direct SQL-level tests for the 5 new Phase 26 store methods.

TestPhase26StoreMethods (10):
1.  get_pitl_history returns [] for device with no PITL records
2.  get_pitl_history returns rows in DESC order (newest first)
3.  get_pitl_history limit parameter is respected
4.  get_all_fingerprinted_devices returns device IDs from biometric_fingerprint_store
5.  store_pitl_proof inserts a row; second call same nullifier is ignored (INSERT OR IGNORE)
6.  get_latest_world_model_hash returns None for unknown device
7.  get_latest_world_model_hash extracts bytes[96:128] from 228-byte raw_data
8.  get_latest_world_model_hash returns None when raw_data < 128 bytes
9.  get_world_model_hash_chain returns rows in ASC order with correct wm_hash_hex
10. get_phg_checkpoints(device_id, limit) — stores 3, limit=2 returns 2 most recent
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from vapi_bridge.store import Store


def _make_store() -> tuple:
    """Return (store, tmpdir) with a fresh in-memory-equivalent SQLite on disk."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_phase26.db")
    store = Store(db_path)
    return store, tmpdir


def _insert_record_sql(store: Store, device_id: str, ts_ms: int,
                       pitl_drift: float | None = None,
                       pitl_humanity: float | None = None,
                       raw_data: bytes | None = None) -> None:
    """Directly INSERT a minimal record row for testing."""
    import hashlib, random
    rec_hash = hashlib.sha256(f"{device_id}{ts_ms}{random.random()}".encode()).hexdigest()
    with store._conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO devices
               (device_id, pubkey_hex, first_seen, last_seen, records_verified)
               VALUES (?, 'unknown', ?, ?, 0)""",
            (device_id, time.time(), time.time()),
        )
        conn.execute(
            """INSERT INTO records
               (record_hash, device_id, counter, timestamp_ms, inference, action_code,
                confidence, battery_pct, raw_data, created_at,
                pitl_l4_drift_velocity, pitl_humanity_prob)
               VALUES (?, ?, 1, ?, 32, 1, 200, 80, ?, ?, ?, ?)""",
            (rec_hash, device_id, ts_ms, raw_data,
             time.time(), pitl_drift, pitl_humanity),
        )


def _insert_fingerprint(store: Store, device_id: str) -> None:
    """Insert a row into biometric_fingerprint_store."""
    with store._conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO biometric_fingerprint_store
               (device_id, mean_json, var_json, n_sessions, updated_at)
               VALUES (?, ?, ?, 1, ?)""",
            (device_id, json.dumps([0.5] * 7), json.dumps([0.1] * 7), time.time()),
        )


# ===========================================================================
# TestPhase26StoreMethods
# ===========================================================================

class TestPhase26StoreMethods(unittest.TestCase):

    def test_1_pitl_history_empty_for_unknown_device(self):
        """get_pitl_history returns [] for a device with no PITL-populated records."""
        store, _ = _make_store()
        result = store.get_pitl_history("unknown_device", limit=50)
        self.assertEqual(result, [])

    def test_2_pitl_history_desc_order(self):
        """get_pitl_history returns rows newest-first (DESC timestamp_ms)."""
        store, _ = _make_store()
        dev = "aabbcc"
        _insert_record_sql(store, dev, ts_ms=1000, pitl_drift=0.1, pitl_humanity=0.5)
        _insert_record_sql(store, dev, ts_ms=2000, pitl_drift=0.2, pitl_humanity=0.6)
        _insert_record_sql(store, dev, ts_ms=3000, pitl_drift=0.3, pitl_humanity=0.7)
        rows = store.get_pitl_history(dev, limit=10)
        self.assertEqual(len(rows), 3)
        # Newest first
        self.assertGreater(rows[0]["timestamp_ms"], rows[1]["timestamp_ms"])
        self.assertGreater(rows[1]["timestamp_ms"], rows[2]["timestamp_ms"])

    def test_3_pitl_history_limit_respected(self):
        """get_pitl_history limit parameter caps returned rows."""
        store, _ = _make_store()
        dev = "ddeeff"
        for i in range(5):
            _insert_record_sql(store, dev, ts_ms=(i + 1) * 1000,
                               pitl_drift=0.1, pitl_humanity=0.5)
        rows = store.get_pitl_history(dev, limit=3)
        self.assertEqual(len(rows), 3)

    def test_4_get_all_fingerprinted_devices(self):
        """get_all_fingerprinted_devices returns IDs from biometric_fingerprint_store."""
        store, _ = _make_store()
        _insert_fingerprint(store, "dev_alpha")
        _insert_fingerprint(store, "dev_beta")
        devices = store.get_all_fingerprinted_devices()
        self.assertIn("dev_alpha", devices)
        self.assertIn("dev_beta", devices)
        self.assertEqual(len(devices), 2)

    def test_5_store_pitl_proof_insert_or_ignore(self):
        """store_pitl_proof inserts a row; duplicate nullifier is ignored gracefully."""
        store, _ = _make_store()
        store.store_pitl_proof("dev_zk", "0xdeadbeef", "0xfc123", 750)
        # Second call with same nullifier must not raise
        store.store_pitl_proof("dev_zk", "0xdeadbeef", "0xfc999", 800)
        # Verify only one row exists for that nullifier
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM pitl_session_proofs WHERE nullifier_hash=?",
                ("0xdeadbeef",)
            ).fetchone()
        self.assertEqual(rows[0], 1)

    def test_6_get_latest_world_model_hash_none_for_unknown(self):
        """get_latest_world_model_hash returns None for device with no records."""
        store, _ = _make_store()
        result = store.get_latest_world_model_hash("ghost_device")
        self.assertIsNone(result)

    def test_7_get_latest_world_model_hash_extracts_bytes_96_128(self):
        """get_latest_world_model_hash extracts raw_data[96:128] from 228B record."""
        store, _ = _make_store()
        dev = "wm_device"
        wm_hash = b"\xab" * 32   # 32-byte world model hash at offset 96
        raw_data = b"\x00" * 96 + wm_hash + b"\x00" * 100  # = 228 bytes
        _insert_record_sql(store, dev, ts_ms=1000, raw_data=raw_data)
        result = store.get_latest_world_model_hash(dev)
        self.assertIsNotNone(result)
        self.assertEqual(result, wm_hash)

    def test_8_get_latest_world_model_hash_none_for_short_raw_data(self):
        """get_latest_world_model_hash returns None when raw_data is shorter than 128 bytes."""
        store, _ = _make_store()
        dev = "short_raw_device"
        raw_data = b"\xff" * 64  # only 64 bytes — too short
        _insert_record_sql(store, dev, ts_ms=1000, raw_data=raw_data)
        result = store.get_latest_world_model_hash(dev)
        self.assertIsNone(result)

    def test_9_get_world_model_hash_chain_asc_order(self):
        """get_world_model_hash_chain returns rows in ascending timestamp order."""
        store, _ = _make_store()
        dev = "chain_device"
        wm1 = b"\x11" * 32
        wm2 = b"\x22" * 32
        raw1 = b"\x00" * 96 + wm1 + b"\x00" * 100
        raw2 = b"\x00" * 96 + wm2 + b"\x00" * 100
        # Insert older record first (ts=1000), newer second (ts=2000)
        _insert_record_sql(store, dev, ts_ms=1000, raw_data=raw1)
        _insert_record_sql(store, dev, ts_ms=2000, raw_data=raw2)
        chain = store.get_world_model_hash_chain(dev, limit=10)
        self.assertEqual(len(chain), 2)
        # ASC order: oldest first
        self.assertLess(chain[0]["timestamp_ms"], chain[1]["timestamp_ms"])
        self.assertEqual(chain[0]["wm_hash_hex"], wm1.hex())
        self.assertEqual(chain[1]["wm_hash_hex"], wm2.hex())

    def test_10_get_phg_checkpoints_device_id_and_limit(self):
        """get_phg_checkpoints(device_id, limit) returns correct rows for that device only."""
        store, _ = _make_store()
        dev = "cp_device"
        other = "other_device"
        # Insert into devices table first
        with store._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen, records_verified) VALUES (?,?,?,?,0)",
                (dev, "unknown", time.time(), time.time())
            )
        now = time.time()
        for i in range(3):
            store.store_phg_checkpoint(
                dev, phg_score=10, record_count=i + 1,
                bio_hash_hex="aa" * 32, tx_hash="0x" + str(i),
                cumulative_score=(i + 1) * 10, confirmed=True,
            )
        # Verify limit=2 returns only 2 most recent (DESC committed_at)
        result = store.get_phg_checkpoints(dev, limit=2)
        self.assertEqual(len(result), 2)
        # Verify only dev's checkpoints returned (not other device's)
        for row in result:
            self.assertEqual(row["device_id"], dev)


if __name__ == "__main__":
    unittest.main()
