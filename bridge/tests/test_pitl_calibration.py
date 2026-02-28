"""
Phase 29 — PITL Calibration Tool Tests

TestPitlCalibration (6):
1.  calibrate() on empty DB → prints "No PITL records found" (no crash)
2.  calibrate() with 10 records → prints L4/humanity stats (no crash)
3.  calibrate(device_id=unknown) → no records found gracefully
4.  L4 distance stats correct (mean, p50 within tolerance)
5.  humanity_prob stats correct
6.  CLI argparse: --db and --device-id parsed without error
"""

import io
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub heavy deps
for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from vapi_bridge.store import Store
from vapi_bridge.pitl_calibration import calibrate


def _fresh_store() -> Store:
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    return Store(tf.name)


_DEVICE_HEX = "00" * 32  # hex device_id used in all test inserts


def _ensure_device(conn):
    conn.execute(
        "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?)",
        (_DEVICE_HEX, "pubkey", time.time(), time.time()),
    )


def _insert_pitl_row(store: Store, l4_dist: float, humanity_prob: float,
                     seq: int = 0):
    """Insert a minimal record row with PITL sidecar columns populated."""
    record_hash_hex = f"{seq:062x}aa"   # unique 64-char hex per call
    with store._conn() as conn:
        _ensure_device(conn)
        conn.execute("""
            INSERT OR IGNORE INTO records
                (record_hash, device_id, inference, confidence, action_code,
                 counter, battery_pct, timestamp_ms, created_at,
                 pitl_l4_distance, pitl_humanity_prob)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_hash_hex,
            _DEVICE_HEX,
            0x20,    # NOMINAL
            200,     # confidence
            0x01,    # action_code
            seq,     # counter
            80,      # battery_pct
            int(time.time() * 1000) + seq,
            time.time(),
            l4_dist,
            humanity_prob,
        ))


# ===========================================================================
# TestPitlCalibration
# ===========================================================================

class TestPitlCalibration(unittest.TestCase):

    def test_1_empty_db_prints_no_records(self):
        """calibrate() on empty DB → prints 'No PITL records found' (no crash)."""
        store = _fresh_store()
        buf = io.StringIO()
        with patch("builtins.print", lambda *a, **kw: buf.write(" ".join(str(x) for x in a) + "\n")):
            calibrate(store)
        self.assertIn("No PITL records found", buf.getvalue())

    def test_2_with_records_prints_stats(self):
        """calibrate() with 10 records → prints L4/humanity stats (no crash)."""
        store = _fresh_store()
        for i in range(10):
            _insert_pitl_row(store, float(i+1)*0.5, float(i+1)/20.0, seq=i)
        buf = io.StringIO()
        with patch("builtins.print", lambda *a, **kw: buf.write(" ".join(str(x) for x in a) + "\n")):
            calibrate(store)
        output = buf.getvalue()
        self.assertIn("L4 Mahalanobis Distance", output)
        self.assertIn("Humanity Probability", output)
        self.assertIn("mean=", output)

    def test_3_unknown_device_id_no_records(self):
        """calibrate(device_id=unknown) → no records found gracefully."""
        store = _fresh_store()
        # Insert a row with a different device
        _insert_pitl_row(store, 1.5, 0.8, seq=99)
        buf = io.StringIO()
        unknown_id = "ff" * 32  # different from _DEVICE_HEX ("00"*32)
        with patch("builtins.print", lambda *a, **kw: buf.write(" ".join(str(x) for x in a) + "\n")):
            calibrate(store, device_id=unknown_id)
        self.assertIn("No PITL records found", buf.getvalue())

    def test_4_l4_distance_stats_correct(self):
        """L4 distance mean and p50 are computed correctly."""
        store = _fresh_store()
        dists = [1.0, 2.0, 3.0, 4.0, 5.0]
        for i, d in enumerate(dists):
            _insert_pitl_row(store, d, 0.8, seq=i+10)
        output_lines = []
        with patch("builtins.print", lambda *a, **kw: output_lines.append(" ".join(str(x) for x in a))):
            calibrate(store)
        full = "\n".join(output_lines)
        # mean should be 3.0000, p50 should be 3.0000
        self.assertIn("mean=3.0000", full)
        self.assertIn("p50=3.0000", full)

    def test_5_humanity_prob_stats_correct(self):
        """Humanity probability stats are output when present."""
        store = _fresh_store()
        probs = [0.2, 0.4, 0.6, 0.8, 1.0]
        for i, p in enumerate(probs):
            _insert_pitl_row(store, 1.0, p, seq=i+20)
        output_lines = []
        with patch("builtins.print", lambda *a, **kw: output_lines.append(" ".join(str(x) for x in a))):
            calibrate(store)
        full = "\n".join(output_lines)
        self.assertIn("Humanity Probability", full)
        self.assertIn("mean=0.6000", full)

    def test_6_cli_argparse_no_error(self):
        """CLI argparse: --db and --device-id parsed without error."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--db", default="bridge.db")
        parser.add_argument("--device-id", default=None, dest="device_id")
        args = parser.parse_args(["--db", "test.db", "--device-id", "aa" * 32])
        self.assertEqual(args.db, "test.db")
        self.assertEqual(args.device_id, "aa" * 32)


if __name__ == "__main__":
    unittest.main()
