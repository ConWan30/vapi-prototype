"""
Phase 62 — ZK Circuit Constraint Tests (6 tests)

Validates the Phase 62 circuit changes:
  - C1 now commits inferenceCodeFromBody into featureCommitment (Poseidon 7→8)
  - C3: inferenceResult === inferenceCodeFromBody constraint
  - Prover private_in includes inferenceCodeFromBody
  - Different inference codes → different featureCommitments (forensic binding)

These tests run in mock-proof mode (no real ZK artifacts required).
test_3 skips gracefully when zk_artifacts are absent (normal CI state).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

for _mod in ["web3", "web3.exceptions", "eth_account", "eth_account.signers.local"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from vapi_bridge.pitl_prover import (
    PITLProver,
    PITL_ZK_ARTIFACTS_AVAILABLE,
    FEATURE_KEYS,
    PROOF_SIZE,
    ZK_ARTIFACTS_DIR,
)

_DEVICE = "cc" * 32
_DEFAULT = {k: float(i + 1) * 0.15 for i, k in enumerate(FEATURE_KEYS)}


def _mock_prover() -> PITLProver:
    return PITLProver(
        wasm_path="/nonexistent/PitlSessionProof.wasm",
        zkey_path="/nonexistent/PitlSessionProof_final.zkey",
    )


def _proof(features=None, infer=0x20, epoch=50):
    p = _mock_prover()
    return p.generate_proof(features or _DEFAULT, _DEVICE, 0.75, 0.1, infer, epoch)


# ===========================================================================
# Tests
# ===========================================================================

class TestPhase62ZKConstraint(unittest.TestCase):

    def test_1_c3_constraint_binds_inference_to_commitment(self):
        """C3 binding: same features but different inferenceResult yields different commitment.

        Phase 62 C1 includes inferenceCodeFromBody in the Poseidon preimage.
        Changing inferenceResult (== inferenceCodeFromBody for honest prover)
        changes featureCommitment — making fraud forensically detectable.
        """
        _, fc_nominal, _, _ = _proof(infer=0x20)
        _, fc_cheat,   _, _ = _proof(infer=0x28)
        self.assertNotEqual(fc_nominal, fc_cheat,
                            "C3 violation: different inference codes must produce different commitments")

    def test_2_modified_c1_includes_inference_code(self):
        """C1 Poseidon now has 8 inputs (was 7); the 8th input is inferenceCodeFromBody.

        Verifies by checking that the circom source file contains 'inferenceCodeFromBody'
        and 'Poseidon(8)' — the circuit modification introduced in Phase 62.
        """
        circom_path = (
            Path(__file__).parents[2] / "contracts" / "circuits" / "PitlSessionProof.circom"
        )
        if not circom_path.exists():
            self.skipTest("circom source not found")
        src = circom_path.read_text(encoding="utf-8")
        self.assertIn("inferenceCodeFromBody", src, "Phase 62 private input missing from circom")
        self.assertIn("Poseidon(8)", src, "C1 must use Poseidon(8) after Phase 62")
        self.assertIn("inferenceResult === inferenceCodeFromBody", src,
                      "C3 equality constraint missing from circom")

    def test_3_artifacts_load_with_new_vkey(self):
        """After ceremony, vkey must still have nPublic=5 (public input count unchanged).

        Skips if artifacts are not present (normal pre-ceremony state).
        """
        vkey_path = ZK_ARTIFACTS_DIR / "PitlSession_verification_key.json"
        if not vkey_path.exists():
            self.skipTest("ZK artifacts not present — run ceremony first")
        import json
        vkey = json.loads(vkey_path.read_text())
        n_public = vkey.get("nPublic")
        self.assertEqual(n_public, 5,
                         f"nPublic must remain 5 after Phase 62 ceremony, got {n_public}")

    def test_4_proof_generation_nominal_succeeds(self):
        """Mock proof generates successfully for NOMINAL inference (0x20)."""
        proof, fc, hp, null = _proof(infer=0x20)
        self.assertEqual(len(proof), PROOF_SIZE)
        self.assertGreater(fc, 0)
        self.assertGreater(null, 0)
        self.assertGreaterEqual(hp, 0)
        self.assertLessEqual(hp, 1000)

    def test_5_prover_input_dict_has_inference_code_from_body(self):
        """_real_proof builds private_in with inferenceCodeFromBody key (Phase 62).

        For an honest bridge: inferenceCodeFromBody == inferenceResult.
        This satisfies C3 (inferenceResult === inferenceCodeFromBody).
        """
        import inspect
        prover = _mock_prover()
        src = inspect.getsource(prover._real_proof)
        self.assertIn("inferenceCodeFromBody", src,
                      "_real_proof must include inferenceCodeFromBody in private_in dict")

    def test_6_two_proofs_same_features_different_inference_have_different_commitments(self):
        """Documents the Phase 62 forensic detectability invariant.

        Two proofs: same L4 features, different inferenceResult (0x20 vs 0x2B).
        Phase 62 C1 ensures their featureCommitments differ, so any attempt to
        generate a NOMINAL-coded proof while the PoAC body encodes an advisory
        code is detectable by comparing featureCommitment to the raw 228-byte body.
        """
        feats = {k: 1.0 for k in FEATURE_KEYS}
        _, fc_nominal,   _, _ = _proof(features=feats, infer=0x20)
        _, fc_advisory, _, _ = _proof(features=feats, infer=0x2B)
        self.assertNotEqual(fc_nominal, fc_advisory,
                            "Different inference codes must yield different commitments")


if __name__ == "__main__":
    unittest.main()
