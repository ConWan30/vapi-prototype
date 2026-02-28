"""
Phase 23 — ContinuityProver Tests

Tests for:
  - ScoreDelta bug fix (store.get_phg_checkpoint_data returns delta not cumulative)
  - biometric_fingerprint_store CRUD
  - ContinuityProver distance + proof hash
  - Store anti-replay (mark_device_claimed / is_device_claimed)
"""

import hashlib
import json
import struct
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub numpy for tests that don't actually need it via numpy-dependent imports
# ---------------------------------------------------------------------------
# We DO want real numpy for ContinuityProver tests, so we only stub if absent.
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from vapi_bridge.store import Store
from vapi_bridge.continuity_prover import ContinuityProver, FEATURE_KEYS, VAR_FLOOR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path):
    return Store(str(tmp_path / "test.db"))


def _insert_nominal_with_features(store, device_id, features: dict, count=5):
    """Insert synthetic NOMINAL records with L4 features into the store."""
    import sqlite3, time
    with sqlite3.connect(store._db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Ensure device exists
        conn.execute(
            "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) VALUES (?,?,?,?)",
            (device_id, "aabbcc", time.time(), time.time()),
        )
        for i in range(count):
            rh = f"{device_id[:8]}{i:08x}"
            conn.execute("""
                INSERT OR IGNORE INTO records
                    (record_hash, device_id, counter, timestamp_ms, inference,
                     action_code, confidence, battery_pct, status, created_at,
                     pitl_l4_features)
                VALUES (?,?,?,?,32,1,200,80,'verified',?,?)
            """, (rh, device_id, i, i * 1000, time.time(), json.dumps(features)))
        conn.commit()


# ===========================================================================
# 1. ScoreDelta Fix
# ===========================================================================

class TestScoreDeltaFix(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(Path(self._tmp))

    def _give_device_score(self, store, device_id, phg_score, nominal_records=10):
        """Manufacture a player profile by inserting NOMINAL records with known confidence."""
        import sqlite3, time, math
        # Each NOMINAL record with confidence=255 contributes CAST(255/255*10 AS INT)=10 PHG pts
        # So score = n * 10.  Choose confidence to get exactly phg_score.
        # Simplest: insert phg_score records with confidence=255 so each contributes 10 pts.
        # Actually phg_score = n * CAST(conf/255*10 as int). With conf=255 each gives 10.
        n = phg_score // 10
        conf = 255
        with sqlite3.connect(store._db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) VALUES (?,?,?,?)",
                (device_id, "aa", time.time(), time.time()),
            )
            for i in range(n):
                rh = f"hash{device_id[:4]}{i:06x}"
                conn.execute("""
                    INSERT OR IGNORE INTO records
                        (record_hash, device_id, counter, timestamp_ms, inference,
                         action_code, confidence, battery_pct, status, created_at)
                    VALUES (?,?,?,?,32,1,?,80,'verified',?)
                """, (rh, device_id, i, i * 1000, conf, time.time()))
            conn.commit()

    def test_delta_is_zero_on_first_checkpoint_when_score_is_zero(self):
        """Device with no records → phg_score=0 → delta=0."""
        import sqlite3, time
        with sqlite3.connect(self.store._db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO devices (device_id, pubkey_hex, first_seen, last_seen) VALUES (?,?,?,?)",
                ("devA", "aa", time.time(), time.time()),
            )
            conn.commit()
        result = self.store.get_phg_checkpoint_data("devA")
        self.assertIsNotNone(result)
        self.assertEqual(result["phg_score"], 0)

    def test_delta_equals_full_score_on_first_checkpoint(self):
        """First checkpoint: delta == cumulative score (no prior checkpoint)."""
        self._give_device_score(self.store, "devB", 50)
        result = self.store.get_phg_checkpoint_data("devB")
        self.assertIsNotNone(result)
        # cumulative_score=50, no prior checkpoint → delta=50
        self.assertEqual(result["phg_score"], 50)
        self.assertEqual(result["cumulative_score"], 50)

    def test_delta_is_incremental_on_second_checkpoint(self):
        """After first checkpoint stored (score=50), second returns delta=20 when total=70."""
        import time
        self._give_device_score(self.store, "devC", 70)
        # Simulate first checkpoint already committed at score=50
        self.store.store_phg_checkpoint("devC", 50, 5, "aabbcc", "0xdeadbeef",
                                        cumulative_score=50, confirmed=True)

        result = self.store.get_phg_checkpoint_data("devC")
        self.assertIsNotNone(result)
        # cumulative=70, last_committed=50 → delta=20
        self.assertEqual(result["phg_score"], 20)
        self.assertEqual(result["cumulative_score"], 70)

    def test_delta_correct_on_third_checkpoint(self):
        """3 sequential checkpoints produce correct deltas: 50, 20, 20 (not 50, 20, 70).

        Regression test for the Phase 22 bug where last_row["phg_score"] (the delta)
        was read as if it were the cumulative, causing checkpoint 3 to compute:
        delta = cumulative - prev_delta instead of delta = cumulative - prev_cumulative.
        """
        # Use prefix "devW" to avoid record-hash collision with other tests in class
        self._give_device_score(self.store, "devWalpha", 90)

        # Checkpoint 1: cumulative was 50 at commit time → delta=50
        self.store.store_phg_checkpoint("devWalpha", 50, 5, "bioW1", "txW1",
                                        cumulative_score=50, confirmed=True)
        # Checkpoint 2: cumulative was 70 at commit time → delta=20
        self.store.store_phg_checkpoint("devWalpha", 20, 10, "bioW2", "txW2",
                                        cumulative_score=70, confirmed=True)

        chk3 = self.store.get_phg_checkpoint_data("devWalpha")
        self.assertIsNotNone(chk3)
        # cumulative=90, last_committed_score=70 → delta=20
        # (the Phase 22 bug would give 90 - 20 = 70 instead)
        self.assertEqual(chk3["phg_score"], 20)
        self.assertEqual(chk3["cumulative_score"], 90)

    def test_delta_never_negative(self):
        """Delta is clamped to 0 when last_committed >= current score."""
        import time
        self._give_device_score(self.store, "devD", 30)
        # Simulate an over-committed checkpoint
        self.store.store_phg_checkpoint("devD", 100, 10, "ff", "0xtx",
                                        cumulative_score=100, confirmed=True)

        result = self.store.get_phg_checkpoint_data("devD")
        self.assertIsNotNone(result)
        self.assertEqual(result["phg_score"], 0)  # clamped


# ===========================================================================
# 2. BiometricFingerprintStore
# ===========================================================================

class TestBiometricFingerprintStore(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(Path(self._tmp))

    def test_store_and_retrieve_fingerprint_state(self):
        mean = {k: float(i) for i, k in enumerate(FEATURE_KEYS)}
        var  = {k: float(i + 1) for i, k in enumerate(FEATURE_KEYS)}
        self.store.store_fingerprint_state("devX", mean, var, n_sessions=7)

        retrieved_var = self.store.get_fingerprint_variance("devX")
        if HAS_NUMPY:
            import numpy as np
            self.assertIsNotNone(retrieved_var)
            self.assertEqual(len(retrieved_var), len(FEATURE_KEYS))
            # Values should match (sort_keys ensures consistent order)
            np.testing.assert_array_almost_equal(
                retrieved_var, [float(i + 1) for i in range(len(FEATURE_KEYS))]
            )
        else:
            # numpy stub — just check it didn't raise
            pass

    def test_get_fingerprint_variance_returns_none_for_unknown_device(self):
        result = self.store.get_fingerprint_variance("nonexistent_device")
        self.assertIsNone(result)

    def test_store_fingerprint_state_overwrites_on_update(self):
        mean1 = {k: 1.0 for k in FEATURE_KEYS}
        var1  = {k: 1.0 for k in FEATURE_KEYS}
        self.store.store_fingerprint_state("devY", mean1, var1, n_sessions=5)

        mean2 = {k: 2.0 for k in FEATURE_KEYS}
        var2  = {k: 2.0 for k in FEATURE_KEYS}
        self.store.store_fingerprint_state("devY", mean2, var2, n_sessions=10)

        retrieved = self.store.get_fingerprint_variance("devY")
        if HAS_NUMPY:
            import numpy as np
            np.testing.assert_array_almost_equal(retrieved, [2.0] * len(FEATURE_KEYS))


# ===========================================================================
# 3. ContinuityProver
# ===========================================================================

@unittest.skipUnless(HAS_NUMPY, "numpy required for ContinuityProver tests")
class TestContinuityProver(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(Path(self._tmp))
        self.prover = ContinuityProver(self.store, threshold=2.0)

        # Shared feature vectors
        self._fp_a = {k: float(i) for i, k in enumerate(FEATURE_KEYS)}
        self._fp_b = {k: float(i) + 10.0 for i, k in enumerate(FEATURE_KEYS)}

    def _insert_device_with_fp(self, device_id, features):
        _insert_nominal_with_features(self.store, device_id, features, count=5)

    def test_distance_returns_none_when_fingerprint_missing(self):
        dist = self.prover.compute_distance("ghost_a", "ghost_b")
        self.assertIsNone(dist)

    def test_distance_is_zero_for_identical_fingerprints(self):
        fp = {k: 1.0 for k in FEATURE_KEYS}
        self._insert_device_with_fp("devA", fp)
        self._insert_device_with_fp("devB", fp)
        dist = self.prover.compute_distance("devA", "devB")
        self.assertIsNotNone(dist)
        self.assertAlmostEqual(dist, 0.0, places=6)

    def test_distance_is_positive_for_different_fingerprints(self):
        self._insert_device_with_fp("devC", self._fp_a)
        self._insert_device_with_fp("devD", self._fp_b)
        dist = self.prover.compute_distance("devC", "devD")
        self.assertIsNotNone(dist)
        self.assertGreater(dist, 0.0)

    def test_should_attest_true_when_below_threshold(self):
        # Identical fingerprints → distance=0 < threshold=2.0
        fp = {k: 3.0 for k in FEATURE_KEYS}
        self._insert_device_with_fp("devE", fp)
        self._insert_device_with_fp("devF", fp)
        should, dist = self.prover.should_attest("devE", "devF")
        self.assertTrue(should)
        self.assertAlmostEqual(dist, 0.0, places=6)

    def test_proof_hash_is_32_bytes_and_deterministic(self):
        fp = {k: 1.0 for k in FEATURE_KEYS}
        self._insert_device_with_fp("devG", fp)
        self._insert_device_with_fp("devH", fp)
        h1 = self.prover.make_proof_hash("devG", "devH", 0.5)
        h2 = self.prover.make_proof_hash("devG", "devH", 0.5)
        self.assertEqual(len(h1), 32)
        self.assertEqual(h1, h2)

    def test_proof_hash_differs_for_different_distances(self):
        fp = {k: 1.0 for k in FEATURE_KEYS}
        self._insert_device_with_fp("devI", fp)
        self._insert_device_with_fp("devJ", fp)
        h1 = self.prover.make_proof_hash("devI", "devJ", 0.5)
        h2 = self.prover.make_proof_hash("devI", "devJ", 1.5)
        self.assertNotEqual(h1, h2)


# ===========================================================================
# 4. Anti-replay / continuity store
# ===========================================================================

class TestContinuityStoreAntiReplay(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.store = _make_store(Path(self._tmp))

    def test_mark_device_claimed_prevents_double_claim(self):
        self.store.mark_device_claimed("devOld", "devNew")
        self.assertTrue(self.store.is_device_claimed("devOld"))

    def test_unclaimed_device_not_claimed(self):
        self.assertFalse(self.store.is_device_claimed("fresh_device"))

    def test_mark_device_claimed_idempotent(self):
        # Calling twice should not raise
        self.store.mark_device_claimed("devK", "devL")
        self.store.mark_device_claimed("devK", "devL")  # INSERT OR IGNORE
        self.assertTrue(self.store.is_device_claimed("devK"))

    def test_continuity_prover_skips_when_no_fingerprint(self):
        """ContinuityProver.should_attest returns (False, None) for unconfigured devices."""
        store = self.store
        prover = ContinuityProver(store, threshold=2.0)
        should, dist = prover.should_attest("nodata_a", "nodata_b")
        self.assertFalse(should)
        self.assertIsNone(dist)


if __name__ == "__main__":
    unittest.main()
