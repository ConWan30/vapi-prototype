"""
Phase 14C — ZKProver unit tests.

Tests cover:
  TestZKArtifactsFlag   (1)  — ZK_ARTIFACTS_AVAILABLE is False in test env
  TestZKProverMockPath  (5)  — generate_proof / verify_proof with mock fallback
  TestProofEncoding     (3)  — _encode_proof / _decode_proof roundtrip
"""

import sys
import struct
import hashlib
import unittest
import types
from pathlib import Path

# ── Stub heavy dependencies (web3, eth_account) before any bridge imports ──
sys.path.insert(0, str(Path(__file__).parents[1]))

for _mod_name in ("web3", "web3.exceptions", "eth_account"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

_web3_exc = sys.modules["web3.exceptions"]
for _attr in ("ContractLogicError", "TransactionNotFound"):
    if not hasattr(_web3_exc, _attr):
        setattr(_web3_exc, _attr, type(_attr, (Exception,), {}))
_web3_mod = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3_mod, _attr):
        setattr(_web3_mod, _attr, type(_attr, (), {})())
_eth_acc = sys.modules["eth_account"]
if not hasattr(_eth_acc, "Account"):
    _eth_acc.Account = type("Account", (), {})()

# ── Import under test ───────────────────────────────────────────────────────
from zk_prover import (
    ZKProver,
    ZK_ARTIFACTS_AVAILABLE,
    PROOF_SIZE,
    _encode_proof,
    _decode_proof,
    _verify_mock_structure,
)


# ---------------------------------------------------------------------------
# TestZKArtifactsFlag
# ---------------------------------------------------------------------------

class TestZKArtifactsFlag(unittest.TestCase):
    """ZK_ARTIFACTS_AVAILABLE must be False in the test environment (no setup.sh run)."""

    def test_artifacts_not_available_in_test_env(self):
        """setup.sh has not been run → ZK_ARTIFACTS_AVAILABLE == False."""
        self.assertFalse(
            ZK_ARTIFACTS_AVAILABLE,
            "Expected ZK_ARTIFACTS_AVAILABLE=False (no circom artifacts installed). "
            "If you ran setup.sh and set VAPI_ZK_WASM_PATH etc., unset them for tests.",
        )


# ---------------------------------------------------------------------------
# TestZKProverMockPath
# ---------------------------------------------------------------------------

class TestZKProverMockPath(unittest.TestCase):
    """ZKProver falls back to mock proof when ZK artifacts are unavailable."""

    def setUp(self):
        self.prover = ZKProver()

    def test_instantiates_without_error(self):
        """ZKProver() must not raise even when artifacts are absent."""
        prover = ZKProver()
        self.assertIsNotNone(prover)
        self.assertFalse(prover._available)

    def test_generate_proof_returns_256_byte_tuple(self):
        """Mock path generate_proof() must return (bytes[256], int, int)."""
        proof, root, nullifier = self.prover.generate_proof(
            inference_results=[0x21, 0x22, 0x23, 0, 0, 0],
            identity_secrets=[1000, 2000, 3000, 0, 0, 0],
            active_flags=[1, 1, 1, 0, 0, 0],
            member_count=3,
            epoch=7,
        )
        self.assertEqual(len(proof), PROOF_SIZE)
        self.assertIsInstance(proof, bytes)
        self.assertIsInstance(root, int)
        self.assertIsInstance(nullifier, int)

    def test_generate_proof_root_nonzero(self):
        """Mock root must be a non-zero integer (derived from inference inputs)."""
        _, root, _ = self.prover.generate_proof(
            inference_results=[0x21, 0x22, 0, 0, 0, 0],
            identity_secrets=[100, 200, 0, 0, 0, 0],
            active_flags=[1, 1, 0, 0, 0, 0],
            member_count=2,
            epoch=1,
        )
        self.assertNotEqual(root, 0)

    def test_verify_proof_returns_true_for_valid_mock(self):
        """verify_proof() must return True for a proof produced by generate_proof()."""
        proof, root, nullifier = self.prover.generate_proof(
            inference_results=[0x21, 0x25, 0, 0, 0, 0],
            identity_secrets=[42, 99, 0, 0, 0, 0],
            active_flags=[1, 1, 0, 0, 0, 0],
            member_count=2,
            epoch=10,
        )
        ok = self.prover.verify_proof(proof, root, nullifier, member_count=2, epoch=10)
        self.assertTrue(ok)

    def test_verify_proof_returns_false_for_wrong_length(self):
        """verify_proof() must reject bytes that are not exactly 256 bytes."""
        self.assertFalse(
            self.prover.verify_proof(b"\xab" * 64, 1, 1, member_count=2, epoch=0)
        )

    def test_mock_root_changes_with_different_inferences(self):
        """Different inference inputs must produce different mock roots."""
        _, root_a, _ = self.prover.generate_proof(
            inference_results=[0x21, 0x22, 0, 0, 0, 0],
            identity_secrets=[100, 200, 0, 0, 0, 0],
            active_flags=[1, 1, 0, 0, 0, 0],
            member_count=2, epoch=0,
        )
        _, root_b, _ = self.prover.generate_proof(
            inference_results=[0x25, 0x26, 0, 0, 0, 0],  # different inferences
            identity_secrets=[100, 200, 0, 0, 0, 0],
            active_flags=[1, 1, 0, 0, 0, 0],
            member_count=2, epoch=0,
        )
        self.assertNotEqual(root_a, root_b)


# ---------------------------------------------------------------------------
# TestProofEncoding
# ---------------------------------------------------------------------------

class TestProofEncoding(unittest.TestCase):
    """_encode_proof / _decode_proof 256-byte wire format correctness."""

    def _make_dummy_proof(self):
        """Build a deterministic dummy proof.json with all distinct field elements."""
        return {
            "pi_a": ["0x" + "ab" * 32, "0x" + "cd" * 32, "1"],
            "pi_b": [
                ["0x" + "01" * 32, "0x" + "02" * 32],
                ["0x" + "03" * 32, "0x" + "04" * 32],
                ["1", "0"],
            ],
            "pi_c": ["0x" + "ef" * 32, "0x" + "12" * 32, "1"],
            "protocol": "groth16",
            "curve": "bn128",
        }

    def test_encode_returns_exactly_256_bytes(self):
        proof_json = self._make_dummy_proof()
        encoded = _encode_proof(proof_json)
        self.assertEqual(len(encoded), PROOF_SIZE)
        self.assertIsInstance(encoded, bytes)

    def test_encode_decode_roundtrip_preserves_all_elements(self):
        """_decode_proof(_encode_proof(x)) must recover all 8 field elements."""
        orig = self._make_dummy_proof()
        encoded = _encode_proof(orig)
        decoded = _decode_proof(encoded)

        # G1: pi_a[0], pi_a[1]
        self.assertEqual(decoded["pi_a"][0].lower(), orig["pi_a"][0].lower())
        self.assertEqual(decoded["pi_a"][1].lower(), orig["pi_a"][1].lower())

        # G2: pi_b[0][0], pi_b[0][1], pi_b[1][0], pi_b[1][1]
        self.assertEqual(decoded["pi_b"][0][0].lower(), orig["pi_b"][0][0].lower())
        self.assertEqual(decoded["pi_b"][0][1].lower(), orig["pi_b"][0][1].lower())
        self.assertEqual(decoded["pi_b"][1][0].lower(), orig["pi_b"][1][0].lower())
        self.assertEqual(decoded["pi_b"][1][1].lower(), orig["pi_b"][1][1].lower())

        # G1: pi_c[0], pi_c[1]
        self.assertEqual(decoded["pi_c"][0].lower(), orig["pi_c"][0].lower())
        self.assertEqual(decoded["pi_c"][1].lower(), orig["pi_c"][1].lower())

    def test_distinct_coordinates_occupy_distinct_regions(self):
        """Each 32-byte slot in the 256B wire format must hold its own field element."""
        orig = self._make_dummy_proof()
        encoded = _encode_proof(orig)

        def to_int(hex_str):
            return int(hex_str, 16)

        # Verify byte regions carry the right values (big-endian 32 bytes each)
        self.assertEqual(
            int.from_bytes(encoded[0:32],   "big"), to_int(orig["pi_a"][0])
        )
        self.assertEqual(
            int.from_bytes(encoded[32:64],  "big"), to_int(orig["pi_a"][1])
        )
        self.assertEqual(
            int.from_bytes(encoded[64:96],  "big"), to_int(orig["pi_b"][0][0])
        )
        self.assertEqual(
            int.from_bytes(encoded[96:128], "big"), to_int(orig["pi_b"][0][1])
        )
        self.assertEqual(
            int.from_bytes(encoded[128:160],"big"), to_int(orig["pi_b"][1][0])
        )
        self.assertEqual(
            int.from_bytes(encoded[160:192],"big"), to_int(orig["pi_b"][1][1])
        )
        self.assertEqual(
            int.from_bytes(encoded[192:224],"big"), to_int(orig["pi_c"][0])
        )
        self.assertEqual(
            int.from_bytes(encoded[224:256],"big"), to_int(orig["pi_c"][1])
        )


if __name__ == "__main__":
    unittest.main()
