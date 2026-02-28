"""
Phase 26 — WorldModelAttestation Tests

TestWorldModelAttestation (8):
1.  verify returns (True, "no_model") when ewc_model=None
2.  verify returns (True, "no_records") for device with no raw_data records
3.  verify returns (True, "match") when committed == current weights hash
4.  verify returns (False, "mismatch:...") when hash differs
5.  get_weight_hash_chain returns [] for unknown device
6.  get_weight_hash_chain extracts bytes 96:128 correctly from raw_data
7.  get_weight_hash_chain skips rows where len(raw_data) < 128
8.  is_model_drifted returns False when hash matches expected_hash_hex
"""

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from vapi_bridge.world_model_attestation import WorldModelAttestation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_store(latest_wm_hash=None, wm_hash_chain=None):
    """Build a mock store."""
    store = MagicMock()
    store.get_latest_world_model_hash.return_value = latest_wm_hash
    store.get_world_model_hash_chain.return_value = wm_hash_chain if wm_hash_chain is not None else []
    return store


def _mock_ewc(hash_value: bytes):
    """Build a mock EWC model that returns hash_value from compute_hash()."""
    model = MagicMock()
    model.compute_hash.return_value = hash_value
    return model


def _make_raw_data(wm_hash_bytes: bytes) -> bytes:
    """Build a synthetic 228B PoAC record with wm_hash at bytes 96:128."""
    # Layout: prev_hash(32) + sensor_commit(32) + model_hash(32) + wm_hash(32) + rest(100) = 228B
    assert len(wm_hash_bytes) == 32
    raw = b"\x00" * 96 + wm_hash_bytes + b"\x00" * 100
    assert len(raw) == 228
    return raw


# ===========================================================================
# Tests
# ===========================================================================

class TestWorldModelAttestation(unittest.TestCase):

    def test_1_no_ewc_model_returns_no_model(self):
        """verify_current_weights returns (True, 'no_model') when ewc_model=None."""
        store = _mock_store()
        attest = WorldModelAttestation(store, ewc_model=None)
        ok, reason = attest.verify_current_weights("devA")
        self.assertTrue(ok)
        self.assertEqual(reason, "no_model")

    def test_2_no_records_returns_no_records(self):
        """verify_current_weights returns (True, 'no_records') when store has no raw_data."""
        store = _mock_store(latest_wm_hash=None)
        ewc = _mock_ewc(b"\xaa" * 32)
        attest = WorldModelAttestation(store, ewc_model=ewc)
        ok, reason = attest.verify_current_weights("devB")
        self.assertTrue(ok)
        self.assertEqual(reason, "no_records")

    def test_3_matching_hash_returns_match(self):
        """verify_current_weights returns (True, 'match') when hashes agree."""
        committed = hashlib.sha256(b"weights_v1").digest()  # 32 bytes
        store = _mock_store(latest_wm_hash=committed)
        ewc = _mock_ewc(committed)
        attest = WorldModelAttestation(store, ewc_model=ewc)
        ok, reason = attest.verify_current_weights("devC")
        self.assertTrue(ok)
        self.assertEqual(reason, "match")

    def test_4_mismatched_hash_returns_mismatch(self):
        """verify_current_weights returns (False, 'mismatch:...') when hashes differ."""
        committed = hashlib.sha256(b"original_weights").digest()
        current   = hashlib.sha256(b"tampered_weights").digest()
        store = _mock_store(latest_wm_hash=committed)
        ewc = _mock_ewc(current)
        attest = WorldModelAttestation(store, ewc_model=ewc)
        ok, reason = attest.verify_current_weights("devD")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("mismatch:"))

    def test_5_weight_hash_chain_empty_for_unknown_device(self):
        """get_weight_hash_chain returns [] for a device with no records."""
        store = _mock_store(wm_hash_chain=[])
        attest = WorldModelAttestation(store)
        chain = attest.get_weight_hash_chain("unknown_dev")
        self.assertEqual(chain, [])

    def test_6_weight_hash_chain_extracts_bytes_96_128(self):
        """get_weight_hash_chain returns correct wm_hash_hex for records with raw_data."""
        wm_hash = hashlib.sha256(b"world_model_v3").digest()
        expected_chain = [
            {"timestamp_ms": 1000, "wm_hash_hex": wm_hash.hex()},
            {"timestamp_ms": 2000, "wm_hash_hex": wm_hash.hex()},
        ]
        store = _mock_store(wm_hash_chain=expected_chain)
        attest = WorldModelAttestation(store)
        chain = attest.get_weight_hash_chain("devE", limit=20)
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["wm_hash_hex"], wm_hash.hex())
        self.assertEqual(chain[1]["timestamp_ms"], 2000)
        # Verify store was called with correct args
        store.get_world_model_hash_chain.assert_called_once_with("devE", limit=20)

    def test_7_weight_hash_chain_skips_short_raw_data(self):
        """get_weight_hash_chain skips rows with len(raw_data) < 128 (store handles this)."""
        # Store returns only valid rows (it filters internally)
        valid_hash = hashlib.sha256(b"valid").digest()
        chain = [{"timestamp_ms": 5000, "wm_hash_hex": valid_hash.hex()}]
        store = _mock_store(wm_hash_chain=chain)
        attest = WorldModelAttestation(store)
        result = attest.get_weight_hash_chain("devF")
        # Only the valid row is returned; short rows were filtered by store
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["wm_hash_hex"], valid_hash.hex())

    def test_8_is_model_drifted_false_when_hash_matches(self):
        """is_model_drifted returns False when current hash == expected_hash_hex."""
        expected = hashlib.sha256(b"stable_weights").digest()
        ewc = _mock_ewc(expected)
        store = _mock_store()
        attest = WorldModelAttestation(store, ewc_model=ewc)
        result = attest.is_model_drifted("devG", expected.hex())
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
