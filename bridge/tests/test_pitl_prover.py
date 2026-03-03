"""
Phase 26 — PITLProver Tests

Tests cover:
- PITLProver instantiates without artifacts (mock mode, no error)
- generate_proof returns (bytes[256], int, int, int)
- feature_commitment deterministic for same features
- different features → different feature_commitment
- nullifier changes with different epoch
- nullifier changes with different device_id
- verify_proof returns True for freshly generated mock proof
- verify_proof returns False for tampered proof bytes
- PITL_ZK_ARTIFACTS_AVAILABLE is bool type
- inference_result 0x20 (NOMINAL) generates proof successfully in mock mode
"""

import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

from vapi_bridge.pitl_prover import (
    PITLProver,
    PITL_ZK_ARTIFACTS_AVAILABLE,
    FEATURE_KEYS,
    PROOF_SIZE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_FEATURES = {k: float(i + 1) * 0.1 for i, k in enumerate(FEATURE_KEYS)}
_DEVICE_A = "aa" * 32
_DEVICE_B = "bb" * 32


def _mock_prover() -> PITLProver:
    """Return a PITLProver forced into mock mode via non-existent artifact paths."""
    return PITLProver(
        wasm_path="/nonexistent/PitlSessionProof.wasm",
        zkey_path="/nonexistent/PitlSessionProof_final.zkey",
    )


def _make_proof(features=None, device_id=_DEVICE_A, l5=0.75, e4=0.2, infer=0x20, epoch=100):
    prover = _mock_prover()
    return prover.generate_proof(
        features or _DEFAULT_FEATURES, device_id, l5, e4, infer, epoch
    )


# ===========================================================================
# Tests
# ===========================================================================

class TestPITLProverMock(unittest.TestCase):

    def test_1_instantiates_without_artifacts(self):
        """PITLProver() must not raise even when artifacts are absent."""
        prover = PITLProver()
        self.assertIsNotNone(prover)

    def test_2_generate_proof_returns_correct_types(self):
        """generate_proof → (bytes[256], int, int, int)."""
        proof, fc, hp, null = _make_proof()
        self.assertIsInstance(proof, bytes)
        self.assertEqual(len(proof), PROOF_SIZE)
        self.assertIsInstance(fc, int)
        self.assertIsInstance(hp, int)
        self.assertIsInstance(null, int)

    def test_3_feature_commitment_deterministic(self):
        """Same features → same feature_commitment across two calls."""
        _, fc1, _, _ = _make_proof()
        _, fc2, _, _ = _make_proof()
        self.assertEqual(fc1, fc2)

    def test_4_different_features_different_commitment(self):
        """Different L4 feature values → different feature_commitment."""
        feats_a = {k: 1.0 for k in FEATURE_KEYS}
        feats_b = {k: 2.0 for k in FEATURE_KEYS}
        _, fc_a, _, _ = _make_proof(features=feats_a)
        _, fc_b, _, _ = _make_proof(features=feats_b)
        self.assertNotEqual(fc_a, fc_b)

    def test_5_nullifier_changes_with_epoch(self):
        """Different epoch → different nullifier_hash."""
        _, _, _, null1 = _make_proof(epoch=100)
        _, _, _, null2 = _make_proof(epoch=101)
        self.assertNotEqual(null1, null2)

    def test_6_nullifier_changes_with_device_id(self):
        """Different device_id → different nullifier_hash."""
        _, _, _, null_a = _make_proof(device_id=_DEVICE_A)
        _, _, _, null_b = _make_proof(device_id=_DEVICE_B)
        self.assertNotEqual(null_a, null_b)

    def test_7_verify_proof_true_for_fresh_proof(self):
        """verify_proof must return True for a freshly generated mock proof."""
        prover = _mock_prover()
        proof, fc, hp, null = prover.generate_proof(
            _DEFAULT_FEATURES, _DEVICE_A, 0.8, 0.1, 0x20, 42
        )
        ok = prover.verify_proof(proof, fc, hp, 0x20, null, 42)
        self.assertTrue(ok)

    def test_8_verify_proof_false_for_tampered_proof(self):
        """Flipping one byte in the proof bytes must cause verify_proof to return False."""
        prover = _mock_prover()
        proof, fc, hp, null = prover.generate_proof(
            _DEFAULT_FEATURES, _DEVICE_A, 0.7, 0.3, 0x20, 99
        )
        # Flip byte at position 0
        tampered = bytes([proof[0] ^ 0xFF]) + proof[1:]
        ok = prover.verify_proof(tampered, fc, hp, 0x20, null, 99)
        self.assertFalse(ok)

    def test_9_artifacts_available_is_bool(self):
        """PITL_ZK_ARTIFACTS_AVAILABLE must be a bool."""
        self.assertIsInstance(PITL_ZK_ARTIFACTS_AVAILABLE, bool)

    def test_10_nominal_inference_generates_proof(self):
        """inference_result=0x20 (NOMINAL) generates proof successfully in mock mode."""
        proof, fc, hp, null = _make_proof(infer=0x20)
        self.assertEqual(len(proof), PROOF_SIZE)
        self.assertGreater(fc, 0)
        self.assertGreater(null, 0)
        self.assertGreaterEqual(hp, 0)
        self.assertLessEqual(hp, 1000)


if __name__ == "__main__":
    unittest.main()
