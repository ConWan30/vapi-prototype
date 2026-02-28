/**
 * Phase 23 Deployment Script — IdentityContinuityRegistry
 *
 * Deploys IdentityContinuityRegistry and wires it to PHGRegistry.setIdentityRegistry().
 *
 * Prerequisites:
 *   - PHG_REGISTRY_ADDRESS env var set, OR bridge/.env.phase22 exists
 *
 * Usage:
 *   npx hardhat run scripts/deploy-phase23.js --network iotex_testnet
 *
 * Writes: bridge/.env.phase23 — IDENTITY_REGISTRY_ADDRESS=0x...
 */

const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deploying Phase 23 contracts with:", deployer.address);

  const balance = await ethers.provider.getBalance(deployer.address);
  console.log("Deployer balance:", ethers.formatEther(balance), "IOTX");

  // --- Resolve PHGRegistry address ---
  let phgRegistryAddress = process.env.PHG_REGISTRY_ADDRESS || "";
  if (!phgRegistryAddress) {
    const envPath = path.join(__dirname, "../../bridge/.env.phase22");
    if (fs.existsSync(envPath)) {
      const content = fs.readFileSync(envPath, "utf8");
      const match = content.match(/PHG_REGISTRY_ADDRESS=(\S+)/);
      if (match) phgRegistryAddress = match[1];
    }
  }
  if (!phgRegistryAddress) {
    throw new Error(
      "PHG_REGISTRY_ADDRESS not set. Run deploy-phase22.js first or set the env var."
    );
  }
  console.log("PHGRegistry (existing):", phgRegistryAddress);

  // --- Deploy IdentityContinuityRegistry ---
  console.log("\nDeploying IdentityContinuityRegistry...");
  const ICR = await ethers.getContractFactory("IdentityContinuityRegistry");
  const identityRegistry = await ICR.deploy(deployer.address, phgRegistryAddress);
  await identityRegistry.waitForDeployment();
  const icrAddress = await identityRegistry.getAddress();
  console.log("IdentityContinuityRegistry:", icrAddress);

  // --- Wire into PHGRegistry ---
  console.log("\nWiring IdentityContinuityRegistry into PHGRegistry...");
  const PHGRegistry = await ethers.getContractFactory("PHGRegistry");
  const phgRegistry = PHGRegistry.attach(phgRegistryAddress);
  const tx = await phgRegistry.setIdentityRegistry(icrAddress);
  await tx.wait();
  console.log("PHGRegistry.setIdentityRegistry() done. tx:", tx.hash);

  // --- Write env file ---
  const envOut = [
    "# Phase 23 — IdentityContinuityRegistry deployment",
    `IDENTITY_REGISTRY_ADDRESS=${icrAddress}`,
    "CONTINUITY_THRESHOLD=2.0",
    "",
  ].join("\n");
  const outPath = path.join(__dirname, "../../bridge/.env.phase23");
  fs.writeFileSync(outPath, envOut);
  console.log("\nWrote:", outPath);

  console.log("\n=== Phase 23 Deployment Complete ===");
  console.log("IdentityContinuityRegistry:", icrAddress);
  console.log("PHGRegistry (unchanged):   ", phgRegistryAddress);
  console.log("\nAdd to bridge/.env:");
  console.log("  IDENTITY_REGISTRY_ADDRESS=" + icrAddress);
  console.log("  CONTINUITY_THRESHOLD=2.0");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
