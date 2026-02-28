pragma circom 2.0.0;

/*
 * VAPI TeamProof — Groth16 ZK circuit over BN254
 * ─────────────────────────────────────────────
 * Proves that all active team members submitted VAPI PoAC records whose
 * inference_result byte is NOT in the cheat range [0x28, 0x2A] = [40, 42],
 * without revealing the individual inference codes on-chain.
 *
 * Circuit parameters:
 *   MAX_MEMBERS = 6  (VAPI TeamProofAggregator: 2–6 members)
 *   Tree height  = 3 (8-leaf balanced Poseidon Merkle tree)
 *   Estimated constraints: ~3,700  →  powers-of-tau 2^12 sufficient
 *
 * Public inputs (verified by Solidity _verifyZKProof):
 *   poseidonMerkleRoot  — Poseidon root of 8-leaf commitment tree
 *   nullifierHash       — Poseidon(merkleRoot, identitySecrets[0], epoch)
 *   memberCount         — Active member count, must be in [2, MAX_MEMBERS]
 *   epoch               — Time-domain tag (block.number / EPOCH_BLOCKS on-chain)
 *
 * Private inputs (known only to the prover / bridge):
 *   inferenceResults[6] — 1-byte VAPI inference codes (secret)
 *   identitySecrets[6]  — Poseidon(deviceId) for each member
 *   activeFlags[6]      — 1 = slot active, 0 = padding slot
 *
 * Constraint groups:
 *   C1. memberCount ∈ [2, MAX_MEMBERS]
 *   C2. activeFlags ∈ {0,1};  sum(activeFlags) === memberCount
 *   C3. inferenceResults[i] ∉ [40, 42] for every active slot i
 *   C4. Poseidon Merkle root of leaf commitments === poseidonMerkleRoot
 *   C5. nullifierHash === Poseidon(poseidonMerkleRoot, identitySecrets[0], epoch)
 *
 * Leaf commitment design (C4):
 *   leaf[i] = Poseidon(effectiveInf, effectiveSecret, activeFlag)
 *   Inactive slots (activeFlag=0) → Poseidon(0, 0, 0)  — deterministic padding
 *   Padding leaves [6,7] → same Poseidon(0, 0, 0) constant
 *
 * Nullifier design (C5):
 *   Anchored to poseidonMerkleRoot (commits to this specific batch of records)
 *   Anchored to identitySecrets[0] (team leader; prevents another party from
 *   submitting the same proof without knowing member 0's device identity)
 *   Anchored to epoch (prevents reuse of the same proof in a later time window)
 *
 * Trusted setup: Hermez perpetual powers-of-tau (pot12, BN128)
 *   https://hermez.s3-eu-west-1.amazonaws.com/powersOfTau28_hez_final_12.ptau
 *
 * Build:
 *   cd contracts/circuits && npm install && bash setup.sh
 */

include "../../node_modules/circomlib/circuits/poseidon.circom";
include "../../node_modules/circomlib/circuits/comparators.circom";

// ─────────────────────────────────────────────────────────────────────────────
// IsNotCheatCode
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
// PoseidonPair
// Compute Poseidon(left, right) for one Merkle tree level.
// ─────────────────────────────────────────────────────────────────────────────
template PoseidonPair() {
    signal input left;
    signal input right;
    signal output out;

    component h = Poseidon(2);
    h.inputs[0] <== left;
    h.inputs[1] <== right;
    out <== h.out;
}

// ─────────────────────────────────────────────────────────────────────────────
// PoseidonMerkleRoot8
// Balanced binary Merkle tree: 8 leaves, height = 3.
// Level 0 (leaves) → level 1 (4 nodes) → level 2 (2 nodes) → root.
// ─────────────────────────────────────────────────────────────────────────────
template PoseidonMerkleRoot8() {
    signal input leaves[8];
    signal output root;

    // Level 0 → Level 1
    component n1[4];
    for (var i = 0; i < 4; i++) {
        n1[i] = PoseidonPair();
        n1[i].left  <== leaves[2 * i];
        n1[i].right <== leaves[2 * i + 1];
    }

    // Level 1 → Level 2
    component n2[2];
    for (var i = 0; i < 2; i++) {
        n2[i] = PoseidonPair();
        n2[i].left  <== n1[2 * i].out;
        n2[i].right <== n1[2 * i + 1].out;
    }

    // Level 2 → Root
    component n3 = PoseidonPair();
    n3.left  <== n2[0].out;
    n3.right <== n2[1].out;
    root <== n3.out;
}

// ─────────────────────────────────────────────────────────────────────────────
// TeamProof(MAX_MEMBERS)
// Main circuit. Instantiated with MAX_MEMBERS = 6.
// ─────────────────────────────────────────────────────────────────────────────
template TeamProof(MAX_MEMBERS) {

    // ── Public inputs ──────────────────────────────────────────────────────
    signal input poseidonMerkleRoot;   // Poseidon commitment tree root
    signal input nullifierHash;        // Anti-replay; stored on-chain after use
    signal input memberCount;          // Active members: 2 ≤ memberCount ≤ MAX_MEMBERS
    signal input epoch;                // Domain tag: block.number / EPOCH_BLOCKS

    // ── Private inputs ─────────────────────────────────────────────────────
    signal input inferenceResults[MAX_MEMBERS];  // 1-byte VAPI inference codes
    signal input identitySecrets[MAX_MEMBERS];   // Poseidon(deviceId) per member
    signal input activeFlags[MAX_MEMBERS];        // 1=active slot, 0=padding

    // ══ C1: memberCount ∈ [2, MAX_MEMBERS] ═══════════════════════════════
    // 4-bit comparators cover [0, 15]; MAX_MEMBERS=6 fits.
    component mcMin = GreaterEqThan(4);
    mcMin.in[0] <== memberCount;
    mcMin.in[1] <== 2;
    mcMin.out === 1;

    component mcMax = LessEqThan(4);
    mcMax.in[0] <== memberCount;
    mcMax.in[1] <== MAX_MEMBERS;
    mcMax.out === 1;

    // ══ C2: activeFlags ∈ {0,1};  sum == memberCount ═════════════════════
    signal flagAcc[MAX_MEMBERS + 1];
    flagAcc[0] <== 0;
    for (var i = 0; i < MAX_MEMBERS; i++) {
        // Binary constraint: f * (1 - f) == 0
        activeFlags[i] * (1 - activeFlags[i]) === 0;
        // Running sum
        flagAcc[i + 1] <== flagAcc[i] + activeFlags[i];
    }
    flagAcc[MAX_MEMBERS] === memberCount;

    // ══ C3: no cheat codes in active slots ════════════════════════════════
    // Mask inactive slots to 0: effectiveInf = inferenceResult * activeFlag.
    // When activeFlag=0: effectiveInf=0; and 0 < 40, so IsNotCheatCode passes. ✓
    component cheat[MAX_MEMBERS];
    signal effectiveInf[MAX_MEMBERS];
    for (var i = 0; i < MAX_MEMBERS; i++) {
        effectiveInf[i] <== inferenceResults[i] * activeFlags[i];
        cheat[i] = IsNotCheatCode();
        cheat[i].inference <== effectiveInf[i];
    }

    // ══ C4: Poseidon Merkle root of commitments ═══════════════════════════
    // leaf[i] = Poseidon(effectiveInf[i], effectiveSec[i], activeFlags[i])
    // Inactive slot: Poseidon(0, 0, 0) — deterministic, same as padding leaves.
    component leafH[MAX_MEMBERS];
    signal effectiveSec[MAX_MEMBERS];
    signal leaves8[8];

    for (var i = 0; i < MAX_MEMBERS; i++) {
        effectiveSec[i] <== identitySecrets[i] * activeFlags[i];
        leafH[i] = Poseidon(3);
        leafH[i].inputs[0] <== effectiveInf[i];
        leafH[i].inputs[1] <== effectiveSec[i];
        leafH[i].inputs[2] <== activeFlags[i];
        leaves8[i] <== leafH[i].out;
    }

    // Padding leaves [6, 7]: Poseidon(0, 0, 0) — same as inactive member leaves
    component padH = Poseidon(3);
    padH.inputs[0] <== 0;
    padH.inputs[1] <== 0;
    padH.inputs[2] <== 0;
    leaves8[6] <== padH.out;
    leaves8[7] <== padH.out;

    component tree = PoseidonMerkleRoot8();
    for (var i = 0; i < 8; i++) {
        tree.leaves[i] <== leaves8[i];
    }
    tree.root === poseidonMerkleRoot;

    // ══ C5: nullifier = Poseidon(merkleRoot, identitySecrets[0], epoch) ════
    // Team leader = member 0 (always active: memberCount ≥ 2, flagAcc ensures
    // at least two flags are 1, and by convention slot 0 is the leader slot).
    // The nullifier binds this proof to:
    //   - poseidonMerkleRoot: this specific batch of records
    //   - identitySecrets[0]: only the leader's device can produce this proof
    //   - epoch: prevents reuse in future time windows
    component nullH = Poseidon(3);
    nullH.inputs[0] <== poseidonMerkleRoot;
    nullH.inputs[1] <== identitySecrets[0];
    nullH.inputs[2] <== epoch;
    nullH.out === nullifierHash;
}

// Instantiate: VAPI supports teams of 2–6 members.
component main {public [poseidonMerkleRoot, nullifierHash, memberCount, epoch]} = TeamProof(6);
