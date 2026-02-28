"""
Phase 15 — ZKProver real-path integration tests.

These tests require real ZK artifacts (WASM + zkey + vkey + circomlibjs).
They are skipped automatically when artifacts are absent (CI, fresh checkout).

To activate (one-time setup):
  cd contracts/circuits
  npm install
  bash setup.sh
  cp TeamProof_js/TeamProof.wasm  ../../bridge/zk_artifacts/
  cp TeamProof_final.zkey          ../../bridge/zk_artifacts/   # KEEP SECRET
  cp verification_key.json         ../../bridge/zk_artifacts/
  cd ../../bridge/zk_artifacts && npm install

  export VAPI_ZK_WASM_PATH=bridge/zk_artifacts/TeamProof.wasm
  export VAPI_ZK_ZKEY_PATH=bridge/zk_artifacts/TeamProof_final.zkey
  export VAPI_ZK_VKEY_PATH=bridge/zk_artifacts/verification_key.json

  python -m pytest tests/test_zk_prover_real.py -v

Tests:
  TestZKProverRealPath (5 tests — skipped unless ZK_ARTIFACTS_AVAILABLE=True)
    test_real_generate_proof_returns_256_bytes
    test_real_verify_proof_roundtrip
    test_real_verify_fails_tampered_proof
    test_real_different_inputs_produce_different_roots
    test_real_wrong_epoch_fails_verification
"""

import sys
import types
import unittest
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

# ── Import under test ────────────────────────────────────────────────────────
from zk_prover import ZKProver, ZK_ARTIFACTS_AVAILABLE, PROOF_SIZE


# ---------------------------------------------------------------------------
# TestZKProverRealPath
# ---------------------------------------------------------------------------

@unittest.skipUnless(
    ZK_ARTIFACTS_AVAILABLE,
    "ZK artifacts absent — skipped. "
    "Run setup.sh, copy artifacts to bridge/zk_artifacts/, then npm install there.",
)
class TestZKProverRealPath(unittest.TestCase):
    """
    Integration tests using real Groth16 proof generation via snarkjs.

    Each proof takes ~10-30s on a modern laptop. These tests are excluded from
    the standard CI suite and are only executed when ZK_ARTIFACTS_AVAILABLE=True.
    """

    def setUp(self):
        self.prover = ZKProver()
        self.assertTrue(
            self.prover._available,
            "ZK_ARTIFACTS_AVAILABLE=True but prover._available=False — "
            "check VAPI_ZK_WASM_PATH / VAPI_ZK_ZKEY_PATH environment variables.",
        )

    def _params(self, inf_a=0x21, inf_b=0x22, epoch=1):
        """Build standard 2-member test parameters."""
        return dict(
            inference_results=[inf_a, inf_b, 0, 0, 0, 0],
            identity_secrets=[999, 12345, 0, 0, 0, 0],
            active_flags=[1, 1, 0, 0, 0, 0],
            member_count=2,
            epoch=epoch,
        )

    def test_real_generate_proof_returns_256_bytes(self):
        """Real proof generation must return (bytes[256], nonzero_int, nonzero_int)."""
        proof, root, nullifier = self.prover.generate_proof(**self._params())

        self.assertIsInstance(proof, bytes)
        self.assertEqual(
            len(proof), PROOF_SIZE,
            f"Expected {PROOF_SIZE}-byte proof, got {len(proof)}",
        )
        self.assertIsInstance(root, int)
        self.assertIsInstance(nullifier, int)
        self.assertGreater(root, 0, "Poseidon merkle root must be nonzero")
        self.assertGreater(nullifier, 0, "Nullifier hash must be nonzero")

    def test_real_verify_proof_roundtrip(self):
        """A real proof produced by generate_proof() must pass verify_proof()."""
        proof, root, nullifier = self.prover.generate_proof(**self._params())
        ok = self.prover.verify_proof(
            proof, root, nullifier, member_count=2, epoch=1
        )
        self.assertTrue(ok, "Real Groth16 proof failed cryptographic verification")

    def test_real_verify_fails_tampered_proof(self):
        """Flipping a byte in a real proof must cause verify_proof() to return False."""
        proof, root, nullifier = self.prover.generate_proof(**self._params())

        # Flip one byte in the G1 'a' component
        tampered = bytearray(proof)
        tampered[10] ^= 0xFF
        ok = self.prover.verify_proof(
            bytes(tampered), root, nullifier, member_count=2, epoch=1
        )
        self.assertFalse(ok, "Tampered proof should not verify")

    def test_real_different_inputs_produce_different_roots(self):
        """Different inference codes must yield different Poseidon Merkle roots."""
        _, root_a, _ = self.prover.generate_proof(**self._params(0x21, 0x22))
        _, root_b, _ = self.prover.generate_proof(**self._params(0x25, 0x26))
        self.assertNotEqual(
            root_a, root_b,
            "Different inference inputs must produce different Poseidon roots",
        )

    def test_real_wrong_epoch_fails_verification(self):
        """Verifying a proof with a mismatched epoch must return False."""
        proof, root, nullifier = self.prover.generate_proof(**self._params(epoch=1))

        # epoch=999 is a different public input — proof does not commit to it
        ok = self.prover.verify_proof(
            proof, root, nullifier, member_count=2, epoch=999
        )
        self.assertFalse(ok, "Proof verified with wrong epoch — nullifier/root mismatch expected")


if __name__ == "__main__":
    unittest.main()
