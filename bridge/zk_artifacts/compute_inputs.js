#!/usr/bin/env node
/**
 * compute_inputs.js — Phase 14C ZK circuit input computation
 *
 * Called by bridge/zk_prover.py to compute Poseidon-based public circuit inputs
 * (poseidonMerkleRoot, nullifierHash) from raw private inputs.  The computation
 * mirrors the constraints in contracts/circuits/TeamProof.circom exactly.
 *
 * Usage:
 *   node compute_inputs.js <private_inputs.json>
 *
 * Input JSON (private_inputs.json):
 *   {
 *     "inferenceResults": [int x 6],       // 8-bit VAPI inference codes
 *     "identitySecrets":  [string x 6],    // Poseidon(deviceId) as decimal strings
 *     "activeFlags":      [0|1 x 6],
 *     "memberCount":      int,
 *     "epoch":            int
 *   }
 *
 * Output JSON (stdout) — full circuit input for snarkjs fullprove:
 *   {
 *     "poseidonMerkleRoot": "...",  // decimal string
 *     "nullifierHash":      "...",
 *     "memberCount":        "...",
 *     "epoch":              "...",
 *     "inferenceResults":   ["..." x 6],
 *     "identitySecrets":    ["..." x 6],
 *     "activeFlags":        ["..." x 6]
 *   }
 *
 * Requires: npm install (circomlibjs in node_modules/ next to this file)
 */

"use strict";

const { buildPoseidon } = require("circomlibjs");
const fs               = require("fs");

const MAX_MEMBERS = 6;

async function main() {
    const args = process.argv.slice(2);
    if (args.length !== 1) {
        process.stderr.write("Usage: node compute_inputs.js <private_inputs.json>\n");
        process.exit(1);
    }

    let raw;
    try {
        raw = JSON.parse(fs.readFileSync(args[0], "utf8"));
    } catch (e) {
        process.stderr.write(`Failed to read/parse ${args[0]}: ${e}\n`);
        process.exit(1);
    }

    const poseidon = await buildPoseidon();
    const F        = poseidon.F;   // BN254 scalar field arithmetic

    // Unpack inputs
    const inferenceResults = raw.inferenceResults.map(Number);
    const identitySecrets  = raw.identitySecrets.map(BigInt);
    const activeFlags      = raw.activeFlags.map(Number);
    const memberCount      = Number(raw.memberCount);
    const epoch            = Number(raw.epoch);

    // Effective values (mask inactive slots → 0)
    const effectiveInf = inferenceResults.map((v, i) => BigInt(v) * BigInt(activeFlags[i]));
    const effectiveSec = identitySecrets.map((v, i)  => v * BigInt(activeFlags[i]));

    // C4: leaf[i] = Poseidon(effectiveInf[i], effectiveSec[i], activeFlags[i])
    const leaves = [];
    for (let i = 0; i < MAX_MEMBERS; i++) {
        const h = poseidon([effectiveInf[i], effectiveSec[i], BigInt(activeFlags[i])]);
        leaves.push(F.toObject(h));
    }

    // Padding leaves [6, 7] = Poseidon(0, 0, 0)
    const padHash = F.toObject(poseidon([0n, 0n, 0n]));
    leaves.push(padHash, padHash);   // indices 6 and 7

    // 8-leaf Poseidon Merkle tree (height = 3)
    function pair(a, b) {
        return F.toObject(poseidon([a, b]));
    }

    const l1 = [];
    for (let i = 0; i < 4; i++) l1.push(pair(leaves[2 * i], leaves[2 * i + 1]));

    const l2 = [pair(l1[0], l1[1]), pair(l1[2], l1[3])];
    const poseidonMerkleRoot = pair(l2[0], l2[1]);

    // C5: nullifierHash = Poseidon(poseidonMerkleRoot, identitySecrets[0], epoch)
    const nullifierHash = F.toObject(
        poseidon([poseidonMerkleRoot, identitySecrets[0], BigInt(epoch)])
    );

    // Emit full circuit input JSON (all values as decimal strings — snarkjs requirement)
    const out = {
        poseidonMerkleRoot: poseidonMerkleRoot.toString(),
        nullifierHash:      nullifierHash.toString(),
        memberCount:        memberCount.toString(),
        epoch:              epoch.toString(),
        inferenceResults:   inferenceResults.map(String),
        identitySecrets:    identitySecrets.map(String),
        activeFlags:        activeFlags.map(String),
    };

    process.stdout.write(JSON.stringify(out, null, 2) + "\n");
}

main().catch(err => {
    process.stderr.write(String(err) + "\n");
    process.exit(1);
});
