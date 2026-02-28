/**
 * transfer-ownership.js — Transfer registry/verifier/market ownership to Gnosis Safe
 *
 * Transfers contract ownership from the deployer wallet to a multisig address
 * (typically a Gnosis Safe). This is a ONE-WAY operational step for mainnet
 * hardening — ensure the new owner address is correct before proceeding.
 *
 * Pre-flight checklist (do NOT skip these):
 *   [ ] attestationEnforced = true has been set and verified on iotexscan.io
 *   [ ] E2E test passed: attested device registered, bounty evidence submitted
 *   [ ] NEW_OWNER_ADDRESS is the Gnosis Safe, not a single-key wallet
 *   [ ] You have confirmed the Safe can execute transactions (multisig quorum)
 *
 * Required env vars:
 *   REGISTRY_ADDRESS   Address of TieredDeviceRegistry
 *   VERIFIER_ADDRESS   Address of PoACVerifier
 *   MARKET_ADDRESS     Address of BountyMarket
 *   NEW_OWNER_ADDRESS  Target multisig address (Gnosis Safe)
 *
 * Usage:
 *   npx hardhat run scripts/transfer-ownership.js --network iotex_mainnet
 *
 * Dry-run (reads state only, no transactions):
 *   DRY_RUN=true npx hardhat run scripts/transfer-ownership.js --network iotex_mainnet
 */

const hre      = require("hardhat");
const readline = require("readline");

const OWNABLE_ABI = [
    {
        "name": "owner",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "name": "transferOwnership",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "newOwner", "type": "address"}],
        "outputs": [],
    },
];

const CONTRACT_NAMES = ["Registry", "Verifier", "Market"];

function prompt(question) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    return new Promise(resolve => rl.question(question, ans => { rl.close(); resolve(ans); }));
}

async function main() {
    const registryAddr  = process.env.REGISTRY_ADDRESS;
    const verifierAddr  = process.env.VERIFIER_ADDRESS;
    const marketAddr    = process.env.MARKET_ADDRESS;
    const newOwnerAddr  = process.env.NEW_OWNER_ADDRESS;
    const dryRun        = process.env.DRY_RUN === "true";

    for (const [name, addr] of [["REGISTRY_ADDRESS", registryAddr], ["VERIFIER_ADDRESS", verifierAddr],
                                 ["MARKET_ADDRESS", marketAddr], ["NEW_OWNER_ADDRESS", newOwnerAddr]]) {
        if (!addr) {
            console.error(`ERROR: ${name} env var is required.`);
            process.exit(1);
        }
    }

    const [deployer] = await hre.ethers.getSigners();

    const addresses   = [registryAddr, verifierAddr, marketAddr];
    const contracts   = addresses.map((addr, i) =>
        new hre.ethers.Contract(hre.ethers.getAddress(addr), OWNABLE_ABI, deployer)
    );

    console.log("=== Transfer Contract Ownership ===");
    console.log(`Network:          ${hre.network.name}`);
    console.log(`Deployer:         ${deployer.address}`);
    console.log(`New owner:        ${newOwnerAddr}`);
    console.log(`Mode:             ${dryRun ? "DRY-RUN (no transactions)" : "LIVE"}`);
    console.log("");

    // Read current owners
    const owners = await Promise.all(contracts.map(c => c.owner()));
    for (let i = 0; i < contracts.length; i++) {
        console.log(`${CONTRACT_NAMES[i]} (${addresses[i]}):`);
        console.log(`  Current owner: ${owners[i]}`);
        if (owners[i].toLowerCase() !== deployer.address.toLowerCase()) {
            console.error(`ERROR: Deployer is not the current owner of ${CONTRACT_NAMES[i]}.`);
            console.error("Run this script from the owner account.");
            process.exit(1);
        }
        if (owners[i].toLowerCase() === newOwnerAddr.toLowerCase()) {
            console.log(`  Already owned by new owner — skipping.`);
        }
    }
    console.log("");

    if (dryRun) {
        console.log("DRY-RUN: Would transfer ownership of all 3 contracts to:", newOwnerAddr);
        console.log("No transactions sent. Remove DRY_RUN=true to execute.");
        process.exit(0);
    }

    console.log("WARNING: This transfers ownership of 3 contracts to:");
    console.log(`  ${newOwnerAddr}`);
    console.log("This action is IRREVERSIBLE unless the new owner transfers back.");
    console.log("");

    const answer = await prompt("Type YES to transfer ownership of all 3 contracts: ");
    if (answer.trim() !== "YES") {
        console.log("Aborted.");
        process.exit(0);
    }

    console.log("");
    for (let i = 0; i < contracts.length; i++) {
        const currentOwner = owners[i];
        if (currentOwner.toLowerCase() === newOwnerAddr.toLowerCase()) {
            console.log(`${CONTRACT_NAMES[i]}: already owned by new owner — skipped.`);
            continue;
        }
        console.log(`Transferring ${CONTRACT_NAMES[i]}...`);
        const tx      = await contracts[i].transferOwnership(hre.ethers.getAddress(newOwnerAddr));
        const receipt = await tx.wait();
        console.log(`  tx: ${receipt.hash}  block: ${receipt.blockNumber}`);
    }

    console.log("");
    console.log("Verifying new owners...");
    for (let i = 0; i < contracts.length; i++) {
        const newOwner = await contracts[i].owner();
        const ok = newOwner.toLowerCase() === newOwnerAddr.toLowerCase();
        console.log(`  ${CONTRACT_NAMES[i]}: ${newOwner} ${ok ? "[OK]" : "[MISMATCH!]"}`);
        if (!ok) {
            console.error(`ERROR: ${CONTRACT_NAMES[i]} owner mismatch after transfer.`);
            process.exit(1);
        }
    }

    console.log("");
    console.log("SUCCESS: Ownership of all 3 contracts transferred to:");
    console.log(`  ${newOwnerAddr}`);
    console.log("");
    console.log("Next steps:");
    console.log("  1. Import Registry, Verifier, and Market into the Gnosis Safe app");
    console.log("  2. Test a multisig transaction (e.g., setMaxTimestampSkew on Verifier)");
    console.log("  3. Remove deployer key from production env vars");
}

main()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error("Error:", err.message || err);
        process.exit(1);
    });
