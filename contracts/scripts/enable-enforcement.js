/**
 * enable-enforcement.js — Enable P256 Attestation Enforcement
 *
 * Calls TieredDeviceRegistry.setAttestationEnforced(true) on an already-deployed
 * registry. This is a DELIBERATE, one-way operational step — once enabled on
 * mainnet, all new registerAttestedV2() calls will require valid manufacturer
 * P256 signatures verified via the IoTeX precompile at 0x0100.
 *
 * Pre-flight checklist (do NOT skip these):
 *   [ ] scripts/test_p256_precompile.js passed on this network
 *   [ ] At least one ManufacturerKey registered (setManufacturerKey called)
 *   [ ] E2E test passed: real attested device registered, bounty evidence submitted
 *   [ ] Deployer wallet still owns the registry (or ownership not yet transferred)
 *
 * Required env vars:
 *   REGISTRY_ADDRESS  Address of the deployed TieredDeviceRegistry
 *
 * Usage:
 *   npx hardhat run scripts/enable-enforcement.js --network iotex_mainnet
 */

const hre      = require("hardhat");
const readline = require("readline");

// Minimal ABI — only the functions needed here
const REGISTRY_ABI = [
    {
        "name": "attestationEnforced",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{ "type": "bool" }],
    },
    {
        "name": "setAttestationEnforced",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{ "name": "_enforced", "type": "bool" }],
        "outputs": [],
    },
    {
        "name": "manufacturerKeys",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{ "name": "", "type": "address" }],
        "outputs": [
            { "name": "pubkeyX", "type": "bytes32" },
            { "name": "pubkeyY", "type": "bytes32" },
            { "name": "active",  "type": "bool"    },
            { "name": "name",    "type": "string"  },
        ],
    },
    {
        "name": "owner",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{ "name": "", "type": "address" }],
    },
];

function prompt(question) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    return new Promise(resolve => rl.question(question, ans => { rl.close(); resolve(ans); }));
}

async function main() {
    const registryAddr = process.env.REGISTRY_ADDRESS;
    if (!registryAddr) {
        console.error("ERROR: REGISTRY_ADDRESS env var is required.");
        process.exit(1);
    }

    const [deployer] = await hre.ethers.getSigners();
    const registry   = new hre.ethers.Contract(
        hre.ethers.getAddress(registryAddr),
        REGISTRY_ABI,
        deployer,
    );

    console.log("=== Enable Attestation Enforcement ===");
    console.log(`Network:          ${hre.network.name}`);
    console.log(`Registry:         ${registryAddr}`);
    console.log(`Deployer:         ${deployer.address}`);
    console.log("");

    // Pre-flight: check current state
    const alreadyEnforced = await registry.attestationEnforced();
    const owner           = await registry.owner();

    if (alreadyEnforced) {
        console.log("INFO: attestationEnforced is already true. Nothing to do.");
        process.exit(0);
    }

    if (owner.toLowerCase() !== deployer.address.toLowerCase()) {
        console.error(`ERROR: Deployer ${deployer.address} is not the registry owner (${owner}).`);
        console.error("Transfer ownership back or run from the correct account.");
        process.exit(1);
    }

    console.log("Current state:    attestationEnforced = false");
    console.log("Registry owner:   " + owner);
    console.log("");
    console.log("WARNING: This enables strict P256 signature verification for all");
    console.log("  new registerAttestedV2() calls. Existing registered devices are");
    console.log("  NOT affected. Old registerAttested() still reverts if called with");
    console.log("  attestationEnforced=true (AttestationValidatorNotImplemented).");
    console.log("");

    const answer = await prompt("Type YES to enable attestation enforcement: ");
    if (answer.trim() !== "YES") {
        console.log("Aborted.");
        process.exit(0);
    }

    console.log("");
    console.log("Sending setAttestationEnforced(true)...");
    const tx      = await registry.setAttestationEnforced(true);
    const receipt = await tx.wait();

    console.log(`Transaction hash: ${receipt.hash}`);
    console.log(`Block:            ${receipt.blockNumber}`);
    console.log("");
    console.log("SUCCESS: attestationEnforced = true");
    console.log("");
    console.log("Next steps:");
    console.log("  1. Verify on iotexscan.io that attestationEnforced=true is stored");
    console.log("  2. Register a test device via registerAttestedV2() to confirm E2E");
    console.log("  3. Transfer registry ownership to Gnosis Safe multisig");
}

main()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error("Error:", err.message || err);
        process.exit(1);
    });
