#!/usr/bin/env node
/**
 * compute_inputs_passport.js — Phase 56 TournamentPassport circuit input computation
 *
 * Called by bridge/vapi_bridge/passport_prover.py to compute Poseidon-based
 * public circuit inputs for TournamentPassport.circom.
 *
 * Usage:
 *   node compute_inputs_passport.js <private_inputs_passport.json>
 *
 * Input JSON:
 *   {
 *     "deviceSecret":        "64-char-hex",   // 64-char hex device secret (no 0x prefix)
 *     "sessionNullifiers":   [string x 5],    // 5 nullifier hashes as decimal strings
 *     "sessionHumanities":   [int x 5],       // 5 humanity scores × 1000 ∈ [0,1000]
 *     "ioidTokenId":         int,             // ioID token ID (0 for testnet mock)
 *     "minHumanityInt":      int,             // min(sessionHumanities)
 *     "epoch":               int              // block epoch or session batch epoch
 *   }
 *
 * Output JSON (stdout) — full circuit input for snarkjs fullprove:
 *   {
 *     "deviceIdHash":      "...",   // Poseidon([BigInt("0x"+deviceSecret)])
 *     "ioidTokenId":       "...",
 *     "passportHash":      "...",   // Poseidon(sessionNullifiers[0..4])
 *     "minHumanityInt":    "...",
 *     "epoch":             "...",
 *     "sessionNullifiers": ["..." x 5],
 *     "sessionHumanities": ["..." x 5],
 *     "deviceSecret":      "..."    // BigInt("0x"+deviceSecret)
 *   }
 *
 * C1: passportHash = Poseidon(sessionNullifiers[0..4])  — verified here
 * C4: deviceIdHash = Poseidon(deviceSecret)             — verified here
 *
 * Requires: npm install (circomlibjs in node_modules/ next to this file)
 */

"use strict";

const { buildPoseidon } = require("circomlibjs");
const fs               = require("fs");

async function main() {
    const args = process.argv.slice(2);
    if (args.length !== 1) {
        process.stderr.write("Usage: node compute_inputs_passport.js <private_inputs_passport.json>\n");
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
    const deviceSecretHex   = String(raw.deviceSecret).replace(/^0x/, "");
    const sessionNullifiers = raw.sessionNullifiers.map(BigInt);
    const sessionHumanities = raw.sessionHumanities.map(Number);
    const ioidTokenId       = Number(raw.ioidTokenId || 0);
    const minHumanityInt    = Number(raw.minHumanityInt);
    const epoch             = Number(raw.epoch || 0);

    if (sessionNullifiers.length !== 5) {
        process.stderr.write(`Expected 5 sessionNullifiers, got ${sessionNullifiers.length}\n`);
        process.exit(1);
    }
    if (sessionHumanities.length !== 5) {
        process.stderr.write(`Expected 5 sessionHumanities, got ${sessionHumanities.length}\n`);
        process.exit(1);
    }

    // C4: deviceIdHash = Poseidon([deviceSecret as BigInt])
    const deviceSecretBig = BigInt("0x" + deviceSecretHex);
    const deviceIdHash    = F.toObject(poseidon([deviceSecretBig]));

    // C1: passportHash = Poseidon(sessionNullifiers[0..4])
    const passportHash = F.toObject(poseidon(sessionNullifiers));

    // Validate minHumanityInt is correct lower bound
    const actualMin = Math.min(...sessionHumanities);
    if (minHumanityInt > actualMin) {
        process.stderr.write(
            `minHumanityInt ${minHumanityInt} exceeds actual minimum ${actualMin}\n`
        );
        process.exit(1);
    }

    // Emit full circuit input JSON (decimal strings — snarkjs requirement)
    const out = {
        // Public signals (circuit main declaration order)
        deviceIdHash:      deviceIdHash.toString(),
        ioidTokenId:       ioidTokenId.toString(),
        passportHash:      passportHash.toString(),
        minHumanityInt:    minHumanityInt.toString(),
        epoch:             epoch.toString(),
        // Private witnesses
        sessionNullifiers: sessionNullifiers.map(String),
        sessionHumanities: sessionHumanities.map(String),
        deviceSecret:      deviceSecretBig.toString(),
    };

    process.stdout.write(JSON.stringify(out, null, 2) + "\n");
}

main().catch(err => {
    process.stderr.write(String(err) + "\n");
    process.exit(1);
});
