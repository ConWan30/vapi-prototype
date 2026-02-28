#!/usr/bin/env node
/**
 * contracts/circuits/scripts/test_proof.js
 * Phase 15 — Smoke test: TeamProof ZK circuit build verification.
 *
 * Verifies that:
 *   1. All required build artifacts exist and are non-empty (WASM, zkey, vkey)
 *   2. A valid Groth16 proof can be generated for a 2-member test team
 *   3. The generated proof passes cryptographic verification
 *
 * Called by setup.sh step after trusted setup completes:
 *   node scripts/test_proof.js
 * Also callable via: npm run test-proof (from contracts/circuits/)
 *
 * Exits 0 on success, 1 on any failure.
 */

"use strict";

const fs   = require("fs");
const path = require("path");

// ── Artifact paths (relative to contracts/circuits/) ─────────────────────────
const CIRCUITS_DIR = path.join(__dirname, "..");
const WASM_PATH    = path.join(CIRCUITS_DIR, "TeamProof_js", "TeamProof.wasm");
const ZKEY_PATH    = path.join(CIRCUITS_DIR, "TeamProof_final.zkey");
const VKEY_PATH    = path.join(CIRCUITS_DIR, "verification_key.json");

// ── Test inputs — valid 2-member team, safe inference codes ──────────────────
// inferenceResults must NOT be in [0x28, 0x29, 0x2A] (cheat codes blocked by C3)
const TEST_INFERENCE  = [0x21, 0x22, 0, 0, 0, 0];   // STANDARD_PLAY, HIGH_VELOCITY
const TEST_SECRETS    = [999n, 12345n, 0n, 0n, 0n, 0n];
const TEST_FLAGS      = [1, 1, 0, 0, 0, 0];
const TEST_MEMBER_CNT = 2;
const TEST_EPOCH      = 1;

async function main() {
    console.log("══════════════════════════════════════════════════════════════");
    console.log(" VAPI Phase 15 — TeamProof ZK Smoke Test");
    console.log("══════════════════════════════════════════════════════════════");
    console.log("");

    // ── Step 1: Verify artifacts exist ───────────────────────────────────────
    console.log("[1/3] Checking build artifacts...");
    for (const [label, p] of [
        ["WASM         ", WASM_PATH],
        ["proving key  ", ZKEY_PATH],
        ["verif. key   ", VKEY_PATH],
    ]) {
        if (!fs.existsSync(p)) {
            fail(`Missing artifact: ${label}\n       Path: ${p}\n       Run: bash setup.sh`);
        }
        const sz = fs.statSync(p).size;
        if (sz === 0) {
            fail(`Empty artifact: ${label} (0 bytes)\n       Path: ${p}\n       Re-run: bash setup.sh`);
        }
        console.log(`      [OK] ${label}: ${(sz / 1024).toFixed(1)} KB`);
    }
    console.log("");

    // ── Step 2: Compute Poseidon-based circuit inputs ────────────────────────
    console.log("[2/3] Computing Poseidon circuit inputs...");
    let poseidon;
    try {
        const { buildPoseidon } = require("circomlibjs");
        poseidon = await buildPoseidon();
    } catch (err) {
        fail(`circomlibjs not found.\n       Run: npm install (in contracts/circuits/)\n       Details: ${err.message}`);
    }
    const F = poseidon.F;

    // Build 8 Poseidon leaves (6 member slots + 2 padding)
    const leaves = [];
    for (let i = 0; i < 6; i++) {
        const effInf = TEST_FLAGS[i] === 1 ? TEST_INFERENCE[i] : 0;
        const effSec = TEST_FLAGS[i] === 1 ? TEST_SECRETS[i]   : 0n;
        leaves.push(F.toObject(poseidon([BigInt(effInf), effSec, BigInt(TEST_FLAGS[i])])));
    }
    const padLeaf = F.toObject(poseidon([0n, 0n, 0n]));
    leaves.push(padLeaf);
    leaves.push(padLeaf);  // index 7

    // Reduce to Poseidon Merkle root (height-3: 8 → 4 → 2 → 1)
    let level = leaves.slice();
    while (level.length > 1) {
        const next = [];
        for (let i = 0; i < level.length; i += 2) {
            next.push(F.toObject(poseidon([level[i], level[i + 1]])));
        }
        level = next;
    }
    const poseidonMerkleRoot = level[0];
    const nullifierHash = F.toObject(
        poseidon([poseidonMerkleRoot, TEST_SECRETS[0], BigInt(TEST_EPOCH)])
    );

    const circuitInput = {
        inferenceResults:   TEST_INFERENCE.map(String),
        identitySecrets:    TEST_SECRETS.map(String),
        activeFlags:        TEST_FLAGS.map(String),
        memberCount:        String(TEST_MEMBER_CNT),
        epoch:              String(TEST_EPOCH),
        poseidonMerkleRoot: String(poseidonMerkleRoot),
        nullifierHash:      String(nullifierHash),
    };

    console.log(`      [OK] Poseidon root: ${poseidonMerkleRoot}`);
    console.log(`      [OK] Nullifier:     ${nullifierHash}`);
    console.log("");

    // ── Step 3: Generate + verify real Groth16 proof ─────────────────────────
    console.log("[3/3] Generating + verifying Groth16 proof (may take 10-30s)...");
    let snarkjs;
    try {
        snarkjs = require("snarkjs");
    } catch (err) {
        fail(`snarkjs not found.\n       Run: npm install (in contracts/circuits/)\n       Details: ${err.message}`);
    }

    let proof, publicSignals;
    try {
        ({ proof, publicSignals } = await snarkjs.groth16.fullProve(
            circuitInput,
            WASM_PATH,
            ZKEY_PATH,
        ));
    } catch (err) {
        fail(`Proof generation failed.\n       Details: ${err.message}`);
    }
    console.log("      [OK] Proof generated.");

    const vkey = JSON.parse(fs.readFileSync(VKEY_PATH, "utf8"));
    let verified;
    try {
        verified = await snarkjs.groth16.verify(vkey, publicSignals, proof);
    } catch (err) {
        fail(`Verification threw: ${err.message}`);
    }
    if (!verified) {
        fail("Proof did not verify against the verification key.");
    }
    console.log("      [OK] Proof verified.");
    console.log("");

    // ── Summary ──────────────────────────────────────────────────────────────
    console.log("══════════════════════════════════════════════════════════════");
    console.log(" SMOKE TEST PASSED");
    console.log("══════════════════════════════════════════════════════════════");
    console.log("");
    console.log(" Public signals (circuit outputs):");
    console.log(`   poseidonMerkleRoot : ${publicSignals[0]}`);
    console.log(`   nullifierHash      : ${publicSignals[1]}`);
    console.log(`   memberCount        : ${publicSignals[2]}`);
    console.log(`   epoch              : ${publicSignals[3]}`);
    console.log("");
    console.log(" Next steps:");
    console.log("   1. TeamProofVerifier.sol is at contracts/contracts/TeamProofVerifier.sol");
    console.log("   2. npx hardhat compile  (from contracts/)");
    console.log("   3. Copy artifacts to bridge:");
    console.log("        cp TeamProof_js/TeamProof.wasm  ../../bridge/zk_artifacts/");
    console.log("        cp TeamProof_final.zkey          ../../bridge/zk_artifacts/  # KEEP SECRET");
    console.log("        cp verification_key.json         ../../bridge/zk_artifacts/");
    console.log("   4. cd ../../bridge/zk_artifacts && npm install");
    console.log("   5. npx hardhat run scripts/deploy-verifier.js --network iotex_testnet");
    console.log("══════════════════════════════════════════════════════════════");
    process.exit(0);
}

function fail(msg) {
    console.error("");
    console.error(`FAIL: ${msg}`);
    console.error("");
    process.exit(1);
}

main().catch(err => {
    console.error("FAIL:", err.message || err);
    process.exit(1);
});
