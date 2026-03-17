/**
 * Phase 62 — Deploy PITLSessionRegistryV2
 *
 * Uses Phase 62 ceremony artifacts (PitlSessionProof.circom with C3 constraint:
 * featureCommitment = Poseidon(8)(scaledFeatures, inferenceCodeFromBody)).
 *
 * Prerequisites:
 *   1. Run ceremony: cd contracts && PATH="$(pwd):$PATH" npx hardhat run scripts/run-ceremony.js
 *   2. Ensure wallet has sufficient IOTX (wallet: 0x0Cf36dB57fc4680bcdfC65D1Aff96993C57a4692)
 *   3. Set BRIDGE_PRIVATE_KEY in contracts/.env
 *
 * Usage:
 *   cd contracts && npx hardhat run scripts/deploy-pitl-registry-v2.js --network iotex_testnet
 *
 * Outputs:
 *   bridge/.env.phase62  -- PITL_SESSION_REGISTRY_V2_ADDRESS=<addr>
 */

const { ethers } = require("hardhat");
const fs = require("fs"), path = require("path");

async function main() {
    const [deployer] = await ethers.getSigners();
    console.log("Deployer:", deployer.address);
    const balance = await ethers.provider.getBalance(deployer.address);
    console.log("Balance:", ethers.formatEther(balance), "IOTX");

    // Deploy PITLSessionRegistryV2 with deployer as bridge
    const Factory = await ethers.getContractFactory("PITLSessionRegistryV2");
    const registry = await Factory.deploy(deployer.address);
    await registry.waitForDeployment();
    const addr = await registry.getAddress();
    console.log("PITLSessionRegistryV2 deployed:", addr);

    // Sanity check
    const bridgeOnChain = await registry.bridge();
    if (bridgeOnChain.toLowerCase() !== deployer.address.toLowerCase()) {
        throw new Error(`Bridge address mismatch: expected ${deployer.address}, got ${bridgeOnChain}`);
    }
    console.log("Bridge address verified:", bridgeOnChain);

    // Write env file
    const envPath = path.join(__dirname, "../../bridge/.env.phase62");
    fs.writeFileSync(envPath, `PITL_SESSION_REGISTRY_V2_ADDRESS=${addr}\n`);
    console.log("Written to bridge/.env.phase62");
    console.log("\nNext steps:");
    console.log("  1. Run ceremony to generate Phase 62 verifier: scripts/run-ceremony.js");
    console.log("  2. Deploy PitlSessionProofVerifierV2 from the generated Verifier.sol");
    console.log("  3. Call registry.setPITLVerifier(verifierAddress) to activate ZK verification");
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
