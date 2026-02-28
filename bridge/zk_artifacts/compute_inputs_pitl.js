#!/usr/bin/env node
/**
 * compute_inputs_pitl.js — Phase 26 PITL circuit input computation
 *
 * Called by bridge/vapi_bridge/pitl_prover.py to compute Poseidon-based public
 * circuit inputs for PitlSessionProof.circom.
 *
 * Usage:
 *   node compute_inputs_pitl.js <private_inputs_pitl.json>
 *
 * Input JSON:
 *   {
 *     "scaledFeatures":  [string x 7],  // L4 features × 1000 as non-negative ints
 *     "deviceId":        "64-char-hex", // 64-char hex device ID (no 0x prefix)
 *     "l5HumanityInt":   int,           // L5 rhythm_humanity_score × 1000 ∈ [0,1000]
 *     "e4DriftInt":      int,           // E4 cognitive_drift × 100
 *     "inferenceResult": int,           // 8-bit VAPI inference code
 *     "humanityProbInt": int,           // humanity_prob × 1000 ∈ [0,1000]
 *     "epoch":           int
 *   }
 *
 * Output JSON (stdout) — full circuit input for snarkjs fullprove:
 *   {
 *     "featureCommitment": "...",       // Poseidon(scaledFeatures[0..6])
 *     "humanityProbInt":   "...",
 *     "inferenceResult":   "...",
 *     "nullifierHash":     "...",       // Poseidon(deviceIdHash, epoch)
 *     "epoch":             "...",
 *     "scaledFeatures":    ["..." x 7],
 *     "deviceIdHash":      "...",       // Poseidon([BigInt("0x"+deviceId)])
 *     "l5HumanityInt":     "...",
 *     "e4DriftInt":        "..."
 *   }
 *
 * Requires: npm install (circomlibjs in node_modules/ next to this file)
 */

"use strict";

const { buildPoseidon } = require("circomlibjs");
const fs               = require("fs");

async function main() {
    const args = process.argv.slice(2);
    if (args.length !== 1) {
        process.stderr.write("Usage: node compute_inputs_pitl.js <private_inputs_pitl.json>\n");
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
    const F        = poseidon.F;  // BN254 scalar field arithmetic

    // Unpack inputs
    const scaledFeatures  = raw.scaledFeatures.map(BigInt);
    const deviceId        = String(raw.deviceId).replace(/^0x/, "");
    const l5HumanityInt   = Number(raw.l5HumanityInt);
    const e4DriftInt      = Number(raw.e4DriftInt);
    const inferenceResult = Number(raw.inferenceResult);
    const humanityProbInt = Number(raw.humanityProbInt);
    const epoch           = Number(raw.epoch);

    // deviceIdHash = Poseidon([BigInt("0x" + deviceId)])
    // The deviceId is a 64-char hex string; interpret as a single large integer.
    const deviceIdInt  = BigInt("0x" + deviceId);
    const deviceIdHash = F.toObject(poseidon([deviceIdInt]));

    // featureCommitment = Poseidon(scaledFeatures[0..6])
    const featureCommitment = F.toObject(poseidon(scaledFeatures));

    // nullifierHash = Poseidon([deviceIdHash, BigInt(epoch)])
    const nullifierHash = F.toObject(poseidon([deviceIdHash, BigInt(epoch)]));

    // Emit full circuit input JSON (decimal strings — snarkjs requirement)
    const out = {
        featureCommitment: featureCommitment.toString(),
        humanityProbInt:   humanityProbInt.toString(),
        inferenceResult:   inferenceResult.toString(),
        nullifierHash:     nullifierHash.toString(),
        epoch:             epoch.toString(),
        scaledFeatures:    scaledFeatures.map(String),
        deviceIdHash:      deviceIdHash.toString(),
        l5HumanityInt:     l5HumanityInt.toString(),
        e4DriftInt:        e4DriftInt.toString(),
    };

    process.stdout.write(JSON.stringify(out, null, 2) + "\n");
}

main().catch(err => {
    process.stderr.write(String(err) + "\n");
    process.exit(1);
});
