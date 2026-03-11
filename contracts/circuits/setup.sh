#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# contracts/circuits/setup.sh
# Phase 14C: TeamProof ZK Circuit — Trusted Setup + Build
#
# Prerequisites:
#   circom 2.x    — https://docs.circom.io/getting-started/installation/
#   node >= 18    — for snarkjs
#   curl          — for ptau download
#
# Usage:
#   cd contracts/circuits
#   npm install
#   bash setup.sh
#
# Outputs:
#   TeamProof.r1cs              — Rank-1 constraint system
#   TeamProof_js/               — WASM witness generator
#   TeamProof_js/TeamProof.wasm — Witness computation binary
#   TeamProof_final.zkey        — Groth16 proving key  (KEEP SECRET — contains toxic waste)
#   verification_key.json       — Groth16 verification key (public — used by verifier)
#   ../contracts/TeamProofVerifier.sol — Auto-generated Solidity Groth16 verifier
#
# Security note:
#   The contribution step below uses a single automated contribution. For mainnet
#   deployment, run a multi-party ceremony with at least 2 independent contributors.
#   The resulting .zkey must be published with its b2sum for auditability.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

CIRCUIT_NAME="TeamProof"
PTAU_FILE="hermez_hez_final_12.ptau"
PTAU_URL="https://hermez.s3-eu-west-1.amazonaws.com/powersOfTau28_hez_final_12.ptau"
PTAU_EXPECTED_LINES=1  # non-zero file check
VERIFIER_OUT="../contracts/TeamProofVerifier.sol"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " VAPI Phase 14C — TeamProof ZK Circuit Build"
echo " Circuit: Groth16/BN254 | MAX_MEMBERS=6 | est. ~3700 constraints"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ── Step 1: Compile circuit ──────────────────────────────────────────────────
echo "[1/7] Compiling TeamProof.circom → R1CS + WASM..."
circom ${CIRCUIT_NAME}.circom \
    --r1cs \
    --wasm \
    --sym  \
    -l node_modules
echo "      Constraint count:"
npx snarkjs r1cs info ${CIRCUIT_NAME}.r1cs | grep "# of Constraints"

# ── Step 2: Download Hermez perpetual ptau (Phase 1 — universal) ─────────────
if [ -f "$PTAU_FILE" ]; then
    echo "[2/7] Using cached Hermez ptau: $PTAU_FILE"
else
    echo "[2/7] Downloading Hermez powers-of-tau (pot12, BN128)..."
    echo "      URL: $PTAU_URL"
    curl -L --progress-bar "$PTAU_URL" -o "$PTAU_FILE"
    echo "      SHA-256: $(sha256sum $PTAU_FILE | cut -d' ' -f1)"
fi

# ── Step 3: Verify ptau is suitable ─────────────────────────────────────────
echo "[3/7] Verifying powers-of-tau..."
npx snarkjs powersoftau verify "$PTAU_FILE" || {
    echo "ERROR: ptau verification failed. Re-download and retry."
    exit 1
}

# ── Step 4: Phase 2 — circuit-specific Groth16 setup ─────────────────────────
echo "[4/7] Running Groth16 Phase 2 setup (circuit-specific zkey)..."
npx snarkjs groth16 setup \
    ${CIRCUIT_NAME}.r1cs \
    "$PTAU_FILE" \
    ${CIRCUIT_NAME}_0000.zkey

# ── Step 5: Contribute to Phase 2 ────────────────────────────────────────────
# NOTE: For mainnet, replace this with a multi-party ceremony.
# Each contributor provides entropy that invalidates the previous toxic waste.
echo "[5/7] Adding Phase 2 contribution..."
ENTROPY="vapi_phase14c_$(hostname)_$(date +%s%N)"
echo "$ENTROPY" | npx snarkjs zkey contribute \
    ${CIRCUIT_NAME}_0000.zkey \
    ${CIRCUIT_NAME}_final.zkey \
    --name="VAPI-Phase14C-AutoContrib" \
    -v
echo "      WARNING: Single-contributor setup. Multi-party ceremony required for mainnet."

# ── Step 6: Export verification key ─────────────────────────────────────────
echo "[6/7] Exporting verification key..."
npx snarkjs zkey export verificationkey \
    ${CIRCUIT_NAME}_final.zkey \
    verification_key.json
echo "      verification_key.json written."

# ── Step 7: Generate Solidity Groth16 verifier ───────────────────────────────
echo "[7/7] Generating Solidity Groth16 verifier → $VERIFIER_OUT"
npx snarkjs zkey export solidityverifier \
    ${CIRCUIT_NAME}_final.zkey \
    "$VERIFIER_OUT"
echo "      TeamProofVerifier.sol written."

# ── Integrity checksums ───────────────────────────────────────────────────────
echo ""
echo "── Artifact checksums ───────────────────────────────────────────"
sha256sum ${CIRCUIT_NAME}.r1cs
sha256sum ${CIRCUIT_NAME}_final.zkey
sha256sum verification_key.json
sha256sum "$VERIFIER_OUT"
echo "─────────────────────────────────────────────────────────────────"

# ── Quick smoke test: generate + verify one proof ─────────────────────────────
echo ""
echo "── Smoke test: generate proof with test inputs ──────────────────"
node scripts/test_proof.js && echo "SMOKE TEST PASSED" || echo "SMOKE TEST FAILED (check scripts/test_proof.js)"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " Phase 26: Compiling PitlSessionProof circuit"
echo "═══════════════════════════════════════════════════════════════"
echo ""

PITL_CIRCUIT="PitlSessionProof"
PITL_VERIFIER_OUT="../contracts/PitlSessionProofVerifier.sol"
# Re-use the same ptau from TeamProof (pot15 from run-ceremony.js is adequate;
# PitlSessionProof has ~1820 constraints which fits in 2^11 = 2048)
PITL_PTAU="${PTAU_FILE}"

echo "[PITL-1/5] Compiling PitlSessionProof.circom → R1CS + WASM..."
circom ${PITL_CIRCUIT}.circom \
    --r1cs \
    --wasm \
    --sym  \
    -l node_modules
echo "      Constraint count:"
npx snarkjs r1cs info ${PITL_CIRCUIT}.r1cs | grep "# of Constraints"
echo "      PitlSessionProof compiled (est. ~1820 constraints, fits 2^11 ptau)."
echo ""

echo "[PITL-2/5] Running Groth16 Phase 2 setup (PitlSessionProof circuit-specific zkey)..."
npx snarkjs groth16 setup \
    ${PITL_CIRCUIT}.r1cs \
    "$PITL_PTAU" \
    ${PITL_CIRCUIT}_0000.zkey

echo "[PITL-3/5] Adding Phase 2 contribution..."
PITL_ENTROPY="vapi_phase26_pitl_$(hostname)_$(date +%s%N)"
echo "$PITL_ENTROPY" | npx snarkjs zkey contribute \
    ${PITL_CIRCUIT}_0000.zkey \
    ${PITL_CIRCUIT}_final.zkey \
    --name="VAPI-Phase26-PitlAutoContrib" \
    -v
echo "      WARNING: Single-contributor setup. Multi-party ceremony required for mainnet."

echo "[PITL-4/5] Exporting PitlSessionProof verification key..."
npx snarkjs zkey export verificationkey \
    ${PITL_CIRCUIT}_final.zkey \
    ${PITL_CIRCUIT}_verification_key.json
echo "      PitlSessionProof_verification_key.json written."

echo "[PITL-5/5] Generating Solidity Groth16 verifier → $PITL_VERIFIER_OUT"
npx snarkjs zkey export solidityverifier \
    ${PITL_CIRCUIT}_final.zkey \
    "$PITL_VERIFIER_OUT"
echo "      PitlSessionProofVerifier.sol written."

# Integrity checksums for PITL artifacts
echo ""
echo "── PitlSessionProof artifact checksums ─────────────────────────"
sha256sum ${PITL_CIRCUIT}.r1cs
sha256sum ${PITL_CIRCUIT}_final.zkey
sha256sum ${PITL_CIRCUIT}_verification_key.json
sha256sum "$PITL_VERIFIER_OUT"
echo "─────────────────────────────────────────────────────────────────"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " Build complete."
echo ""
echo " Phase 14C (TeamProof) integration steps:"
echo "   1. TeamProofVerifier.sol is already at $VERIFIER_OUT"
echo "   2. Run: cd .. && npx hardhat compile"
echo "   3. Distribute proving artifacts to bridge:"
echo "        TeamProof_js/TeamProof.wasm   → bridge/zk_artifacts/"
echo "        TeamProof_final.zkey          → bridge/zk_artifacts/ (KEEP SECRET)"
echo "        verification_key.json         → bridge/zk_artifacts/"
echo "   4. Set env vars:"
echo "        VAPI_ZK_WASM_PATH=bridge/zk_artifacts/TeamProof.wasm"
echo "        VAPI_ZK_ZKEY_PATH=bridge/zk_artifacts/TeamProof_final.zkey"
echo "        VAPI_ZK_VKEY_PATH=bridge/zk_artifacts/verification_key.json"
echo ""
echo " Phase 26 (PitlSessionProof) integration steps:"
echo "   1. PitlSessionProofVerifier.sol is already at $PITL_VERIFIER_OUT"
echo "   2. Run: cd .. && npx hardhat compile"
echo "   3. Deploy verifier: npx hardhat run scripts/deploy-pitl-verifier.js --network iotex_testnet"
echo "   4. Wire: PITLSessionRegistry.setPITLVerifier(<deployed_address>)"
echo "   5. Distribute PITL proving artifacts to bridge:"
echo "        PitlSessionProof_js/PitlSessionProof.wasm → bridge/zk_artifacts/"
echo "        PitlSessionProof_final.zkey               → bridge/zk_artifacts/ (KEEP SECRET)"
echo "        PitlSessionProof_verification_key.json    → bridge/zk_artifacts/"
echo "═══════════════════════════════════════════════════════════════"
