/**
 * test_p256_precompile.js — IoTeX P256 Precompile Integration Test
 *
 * Validates that the IoTeX P256 precompile at address 0x0100 correctly verifies
 * ECDSA-P256 signatures with the exact 160-byte input layout used by
 * TieredDeviceRegistry._p256Verify().
 *
 * This is a LIVE TESTNET script — it requires:
 *   - IoTeX testnet RPC (https://babel-api.testnet.iotex.io)
 *   - @noble/curves installed: npm install @noble/curves
 *
 * Usage:
 *   npx hardhat run scripts/test_p256_precompile.js --network iotex_testnet
 *
 * What this validates:
 *   1. Valid P256 signature → precompile returns uint256(1)
 *   2. Invalid signature (tampered s) → precompile returns uint256(0)
 *   3. 160-byte input layout: msgHash(32) || r(32) || s(32) || x(32) || y(32)
 *
 * Document results in bridge/attestation-enforcement-guide.md §6 before
 * calling setAttestationEnforced(true) on mainnet.
 */

const { ethers } = require("hardhat");

const P256_PRECOMPILE = "0x0000000000000000000000000000000000000100";

// ---------------------------------------------------------------------------
// Helper: encode 160-byte precompile input
//   msgHash(32) || r(32) || s(32) || pubkeyX(32) || pubkeyY(32)
// ---------------------------------------------------------------------------
function encodePrecompileInput(msgHash, r, s, pubkeyX, pubkeyY) {
    return ethers.concat([
        ethers.getBytes(msgHash),
        ethers.zeroPadValue(ethers.toBeHex(r), 32),
        ethers.zeroPadValue(ethers.toBeHex(s), 32),
        ethers.zeroPadValue(ethers.toBeHex(pubkeyX), 32),
        ethers.zeroPadValue(ethers.toBeHex(pubkeyY), 32),
    ]);
}

async function main() {
    let p256;
    try {
        // @noble/curves provides pure-JS P256 operations
        const { p256: _p256 } = await import("@noble/curves/p256");
        p256 = _p256;
    } catch (e) {
        console.error("ERROR: @noble/curves not installed.");
        console.error("Run: npm install @noble/curves");
        process.exit(1);
    }

    const provider = ethers.provider;
    const network  = await provider.getNetwork();
    console.log("=== IoTeX P256 Precompile Integration Test ===");
    console.log(`Network:     ${network.name} (chainId=${network.chainId})`);
    console.log(`Precompile:  ${P256_PRECOMPILE}`);
    console.log("");

    // -----------------------------------------------------------------------
    // 1. Generate a real P256 keypair
    // -----------------------------------------------------------------------
    const privKeyBytes = p256.utils.randomPrivateKey();
    const pubKey       = p256.getPublicKey(privKeyBytes, false); // uncompressed 65 bytes

    // Extract raw X and Y coordinates (skip the 0x04 prefix byte)
    const pubkeyX = BigInt("0x" + Buffer.from(pubKey.slice(1, 33)).toString("hex"));
    const pubkeyY = BigInt("0x" + Buffer.from(pubKey.slice(33, 65)).toString("hex"));

    console.log("Generated P256 keypair:");
    console.log(`  pubkeyX: 0x${pubkeyX.toString(16).padStart(64, "0")}`);
    console.log(`  pubkeyY: 0x${pubkeyY.toString(16).padStart(64, "0")}`);
    console.log("");

    // -----------------------------------------------------------------------
    // 2. Compute msgHash = keccak256(pubkey) — mirrors _validateAttestationV2
    // -----------------------------------------------------------------------
    const pubkeyHex = "0x" + Buffer.from(pubKey).toString("hex");
    const msgHash   = ethers.keccak256(pubkeyHex);
    console.log(`msgHash (keccak256 of pubkey): ${msgHash}`);
    console.log("");

    // -----------------------------------------------------------------------
    // 3. Sign msgHash with the private key
    // -----------------------------------------------------------------------
    const msgHashBytes = ethers.getBytes(msgHash);
    const sig = p256.sign(msgHashBytes, privKeyBytes);
    const r   = sig.r;
    const s   = sig.s;

    console.log("P256 signature:");
    console.log(`  r: 0x${r.toString(16).padStart(64, "0")}`);
    console.log(`  s: 0x${s.toString(16).padStart(64, "0")}`);
    console.log("");

    // -----------------------------------------------------------------------
    // 4. Test Case 1: Valid signature → precompile must return 1
    // -----------------------------------------------------------------------
    console.log("Test 1: Valid signature...");
    const validInput = encodePrecompileInput(msgHash, r, s, pubkeyX, pubkeyY);
    if (validInput.length !== 160) {
        throw new Error(`Expected 160-byte input, got ${validInput.length} bytes`);
    }

    const validResult = await provider.call({ to: P256_PRECOMPILE, data: validInput });
    const validDecoded = BigInt(validResult);

    if (validDecoded !== 1n) {
        console.error(`  FAIL: Expected 1, got ${validDecoded}`);
        console.error("  The precompile rejected a valid P256 signature.");
        console.error("  Possible causes: incorrect input layout, precompile unavailable on this network.");
        process.exit(1);
    }
    console.log(`  PASS: precompile returned ${validDecoded} (valid signature accepted)`);
    console.log("");

    // -----------------------------------------------------------------------
    // 5. Test Case 2: Invalid signature (tamper s) → precompile must return 0
    // -----------------------------------------------------------------------
    console.log("Test 2: Invalid signature (tampered s)...");
    const tamperedS = (s + 1n) % p256.CURVE.n;
    const invalidInput = encodePrecompileInput(msgHash, r, tamperedS, pubkeyX, pubkeyY);

    const invalidResult = await provider.call({ to: P256_PRECOMPILE, data: invalidInput });
    const invalidDecoded = BigInt(invalidResult);

    if (invalidDecoded !== 0n) {
        console.error(`  FAIL: Expected 0, got ${invalidDecoded}`);
        console.error("  The precompile accepted a tampered P256 signature — this is a critical bug.");
        process.exit(1);
    }
    console.log(`  PASS: precompile returned ${invalidDecoded} (invalid signature rejected)`);
    console.log("");

    // -----------------------------------------------------------------------
    // 6. Test Case 3: Wrong public key → precompile must return 0
    // -----------------------------------------------------------------------
    console.log("Test 3: Wrong public key (different keypair)...");
    const privKey2     = p256.utils.randomPrivateKey();
    const pubKey2      = p256.getPublicKey(privKey2, false);
    const wrongX = BigInt("0x" + Buffer.from(pubKey2.slice(1, 33)).toString("hex"));
    const wrongY = BigInt("0x" + Buffer.from(pubKey2.slice(33, 65)).toString("hex"));

    const wrongKeyInput = encodePrecompileInput(msgHash, r, s, wrongX, wrongY);
    const wrongKeyResult  = await provider.call({ to: P256_PRECOMPILE, data: wrongKeyInput });
    const wrongKeyDecoded = BigInt(wrongKeyResult);

    if (wrongKeyDecoded !== 0n) {
        console.error(`  FAIL: Expected 0, got ${wrongKeyDecoded}`);
        console.error("  The precompile verified a signature against the wrong public key.");
        process.exit(1);
    }
    console.log(`  PASS: precompile returned ${wrongKeyDecoded} (wrong key rejected)`);
    console.log("");

    // -----------------------------------------------------------------------
    // 7. Summary
    // -----------------------------------------------------------------------
    console.log("=== All 3 test cases passed ===");
    console.log("");
    console.log("The IoTeX P256 precompile at 0x0100 is working correctly.");
    console.log("Input layout verified: msgHash(32)||r(32)||s(32)||x(32)||y(32)");
    console.log("");
    console.log("You may now safely call setAttestationEnforced(true) on this network.");
    console.log("See bridge/attestation-enforcement-guide.md §6 for mainnet checklist.");
}

main()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error("Test failed:", err.message || err);
        process.exit(1);
    });
