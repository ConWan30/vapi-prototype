/**
 * deploy-phase34.js — FederatedThreatRegistry deployment
 *
 * Deploys FederatedThreatRegistry with the deployer address as the authorized bridge.
 * Smoke-tests bridge address and initial report count.
 * Writes FEDERATED_THREAT_REGISTRY_ADDRESS to bridge/.env.phase34.
 *
 * Usage:
 *   npx hardhat run scripts/deploy-phase34.js --network iotex_testnet
 */

const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
    const [deployer] = await ethers.getSigners();
    console.log("Deployer:", deployer.address);
    console.log("Balance:", ethers.formatEther(await ethers.provider.getBalance(deployer.address)), "IOTX");

    // Deploy FederatedThreatRegistry with deployer as authorized bridge
    console.log("\nDeploying FederatedThreatRegistry...");
    const FTR = await ethers.getContractFactory("FederatedThreatRegistry");
    const ftr = await FTR.deploy(deployer.address);
    await ftr.waitForDeployment();
    const ftrAddr = await ftr.getAddress();
    console.log("FederatedThreatRegistry deployed:", ftrAddr);

    // --- Smoke tests ---
    console.log("\nRunning smoke tests...");

    // 1. Verify bridge address is immutable
    const bridgeAddr = await ftr.bridge();
    if (bridgeAddr.toLowerCase() !== deployer.address.toLowerCase()) {
        throw new Error(`Bridge address mismatch: expected ${deployer.address}, got ${bridgeAddr}`);
    }
    console.log("  ✓ bridge address set correctly:", bridgeAddr);

    // 2. Verify initial report count is 0
    const initialCount = await ftr.getReportCount(ethers.ZeroHash);
    if (initialCount !== 0n) {
        throw new Error(`Expected initial count 0, got ${initialCount}`);
    }
    console.log("  ✓ initial getReportCount(ZeroHash) == 0");

    // 3. Verify isMultiVenueConfirmed returns false for unreported hash
    const confirmed = await ftr.isMultiVenueConfirmed(ethers.ZeroHash, 2);
    if (confirmed !== false) {
        throw new Error("Expected isMultiVenueConfirmed to be false for unreported hash");
    }
    console.log("  ✓ isMultiVenueConfirmed(ZeroHash, 2) == false");

    // Write .env.phase34
    const envPath = path.join(__dirname, "../../bridge/.env.phase34");
    const envContent = `# Phase 34: FederatedThreatRegistry deployment\nFEDERATED_THREAT_REGISTRY_ADDRESS=${ftrAddr}\n`;
    fs.writeFileSync(envPath, envContent);
    console.log("\nWritten:", envPath);

    console.log("\n=== Phase 34 Deployment Complete ===");
    console.log("FederatedThreatRegistry:", ftrAddr);
    console.log("\nAdd to bridge .env:");
    console.log(`  FEDERATED_THREAT_REGISTRY_ADDRESS=${ftrAddr}`);
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
