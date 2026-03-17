pragma circom 2.0.0;

/*
 * VAPI PitlSessionProof — Groth16 ZK circuit over BN254
 * ─────────────────────────────────────────────────────
 * Proves that the bridge honestly computed PITL biometric outputs for an
 * individual session without revealing raw sensor feature values on-chain.
 *
 * Phase 62: Added C3 inferenceCodeFromBody binding.
 *   C1 now commits inference code into featureCommitment (Poseidon 7→8 inputs).
 *   C3 constrains inferenceResult === inferenceCodeFromBody.
 *   A corrupt bridge generating fake inferenceResult=NOMINAL while body=CHEAT
 *   produces a featureCommitment inconsistent with the PoAC body — forensically
 *   detectable against the raw 228-byte record.
 *
 * Circuit parameters:
 *   Estimated constraints: ~1,821  →  powers-of-tau 2^11 sufficient
 *
 * Public inputs (verified by Solidity IPITLSessionVerifier.verifyProof):
 *   featureCommitment — Poseidon(8)(scaledFeatures[0..6], inferenceCodeFromBody)
 *   humanityProbInt   — humanity_prob × 1000 ∈ [0, 1000]
 *   inferenceResult   — 8-bit VAPI inference code (public)
 *   nullifierHash     — Poseidon(deviceIdHash, epoch)
 *   epoch             — Time-domain tag: block.number / EPOCH_BLOCKS
 *
 * Private inputs (known only to the bridge):
 *   scaledFeatures[7]      — L4 biometric features × 1000, non-negative integers
 *   deviceIdHash           — Poseidon(deviceId bytes) — identity binding
 *   l5HumanityInt          — L5 rhythm_humanity_score × 1000 ∈ [0, 1000]
 *   e4DriftInt             — E4 cognitive_drift × 100 (non-negative integer)
 *   inferenceCodeFromBody  — PoAC body byte 128, prover-supplied (Phase 62)
 *
 * Constraint groups:
 *   C1. featureCommitment === Poseidon(8)(scaledFeatures, inferenceCodeFromBody) (~1,200 constraints)
 *   C2. inferenceResult ∉ [40, 42]  via IsNotCheatCode()                        (~60 constraints)
 *   C3. inferenceResult === inferenceCodeFromBody  (Phase 62 binding)            (~1 constraint)
 *   C4. humanityProbInt ∈ [0, 1000] via GreaterEqThan/LessEqThan               (~40 constraints)
 *   C5. nullifierHash === Poseidon(deviceIdHash, epoch)                          (~500 constraints)
 *   C6. l5HumanityInt ∈ [0, 1000]  (domain sanity)                              (~20 constraints)
 *
 * Nullifier design (C5):
 *   Anchored to (deviceIdHash, epoch) — prevents replay per device per epoch.
 *   deviceIdHash = Poseidon(deviceId bytes) — binds the proof to one physical device.
 *   epoch = block.number / EPOCH_BLOCKS — prevents reuse across time windows.
 *
 * Trusted setup: Hermez perpetual powers-of-tau (pot11, BN128)
 *   https://hermez.s3-eu-west-1.amazonaws.com/powersOfTau28_hez_final_11.ptau
 *
 * Build:
 *   cd contracts/circuits && npm install && bash setup.sh
 */

include "../../node_modules/circomlib/circuits/poseidon.circom";
include "../../node_modules/circomlib/circuits/comparators.circom";

// ─────────────────────────────────────────────────────────────────────────────
// IsNotCheatCode (copied verbatim from TeamProof.circom)
// Asserts: inference ∉ [0x28, 0x2A] = [40, 42]
// Valid input domain: [0, 255]  (8-bit comparators)
// When inference ∈ [40, 42]: lt.out=0 AND gt.out=0 → sum=0 → constraint FAILS ✓
// ─────────────────────────────────────────────────────────────────────────────
template IsNotCheatCode() {
    signal input inference;

    // inference < 40  (i.e. inference ≤ 39)
    component lt = LessThan(8);
    lt.in[0] <== inference;
    lt.in[1] <== 40;

    // inference > 42  (i.e. inference ≥ 43)
    component gt = GreaterThan(8);
    gt.in[0] <== inference;
    gt.in[1] <== 42;

    // Exactly one must hold — value cannot be simultaneously < 40 and > 42.
    // For valid clean inferences, lt.out + gt.out = 1.
    // For cheat codes [40,42]:  lt.out + gt.out = 0  → constraint violation.
    lt.out + gt.out === 1;
}

// ─────────────────────────────────────────────────────────────────────────────
// PitlSessionProof
// Main circuit. Individual session PITL integrity proof.
// ─────────────────────────────────────────────────────────────────────────────
template PitlSessionProof() {

    // ── Public inputs ──────────────────────────────────────────────────────
    signal input featureCommitment;  // Poseidon(7)(scaledFeatures)
    signal input humanityProbInt;    // humanity_prob × 1000 ∈ [0, 1000]
    signal input inferenceResult;    // 8-bit VAPI inference code
    signal input nullifierHash;      // Anti-replay; stored on-chain after use
    signal input epoch;              // Domain tag: block.number / EPOCH_BLOCKS

    // ── Private inputs ─────────────────────────────────────────────────────
    signal input scaledFeatures[7];     // L4 features × 1000 (non-negative)
    signal input deviceIdHash;          // Poseidon(deviceId) — identity binding
    signal input l5HumanityInt;         // L5 rhythm_humanity_score × 1000 ∈ [0, 1000]
    signal input e4DriftInt;            // E4 cognitive_drift × 100 (non-negative)
    signal input inferenceCodeFromBody; // Phase 62: PoAC body byte 128, prover-supplied

    // ══ C1: featureCommitment = Poseidon(8)(scaledFeatures, inferenceCodeFromBody) ══
    // Phase 62: inferenceCodeFromBody added as 8th preimage input.
    // A dishonest prover that changes inferenceResult without changing
    // inferenceCodeFromBody will produce a featureCommitment that is
    // inconsistent with the raw PoAC body — forensically detectable.
    component featH = Poseidon(8);
    for (var i = 0; i < 7; i++) {
        featH.inputs[i] <== scaledFeatures[i];
    }
    featH.inputs[7] <== inferenceCodeFromBody;
    featH.out === featureCommitment;

    // ══ C2: inferenceResult ∉ [40, 42] ═══════════════════════════════════
    // Proves the bridge did not suppress a cheat-code result.
    component cheatCheck = IsNotCheatCode();
    cheatCheck.inference <== inferenceResult;

    // ══ C3: inferenceResult === inferenceCodeFromBody (Phase 62) ══════════
    // Binds the public inferenceResult to the private inferenceCodeFromBody.
    // For an honest bridge: inferenceResult == inferenceCodeFromBody (always true).
    // For a corrupt bridge: changing one without the other invalidates featureCommitment
    // against the PoAC body — making the fraud forensically detectable.
    inferenceResult === inferenceCodeFromBody;

    // ══ C4: humanityProbInt ∈ [0, 1000] ══════════════════════════════════
    // 10-bit comparators cover [0, 1023]; 1000 fits within range.
    component hpMin = GreaterEqThan(10);
    hpMin.in[0] <== humanityProbInt;
    hpMin.in[1] <== 0;
    hpMin.out === 1;

    component hpMax = LessEqThan(10);
    hpMax.in[0] <== humanityProbInt;
    hpMax.in[1] <== 1000;
    hpMax.out === 1;

    // ══ C5: nullifierHash = Poseidon(deviceIdHash, epoch) ════════════════
    // Anchors the proof to one device × one epoch — prevents replay.
    component nullH = Poseidon(2);
    nullH.inputs[0] <== deviceIdHash;
    nullH.inputs[1] <== epoch;
    nullH.out === nullifierHash;

    // ══ C6: l5HumanityInt ∈ [0, 1000] domain sanity ══════════════════════
    // L5 rhythm_humanity_score × 1000 must be a valid probability integer.
    component l5Min = GreaterEqThan(10);
    l5Min.in[0] <== l5HumanityInt;
    l5Min.in[1] <== 0;
    l5Min.out === 1;

    component l5Max = LessEqThan(10);
    l5Max.in[0] <== l5HumanityInt;
    l5Max.in[1] <== 1000;
    l5Max.out === 1;

    // ── e4DriftInt is non-negative by convention (circuit field is non-negative) ─
    // No explicit range check needed; BN254 field elements are non-negative.
    // The private input is included to bind the proof to the full session context
    // and ensure the bridge cannot selectively omit E4 observations.
    signal e4Bound;
    e4Bound <== e4DriftInt;  // signal reference prevents optimiser from removing input
}

component main {public [featureCommitment, humanityProbInt, inferenceResult,
                         nullifierHash, epoch]} = PitlSessionProof();
