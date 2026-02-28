/**
 * Phase 26 Deployment Script
 *
 * Deploys:
 *   1. PITLSessionRegistry (bridge = deployer.address)
 *
 * Writes deployed address to bridge/.env.phase26.
 *
 * Usage:
 *   cd contracts && npx hardhat run scripts/deploy-phase26.js --network iotex_testnet
 *
 * After deployment, set PITL_SESSION_REGISTRY_ADDRESS in your bridge environment.
 * To activate ZK verification (optional — requires circom trusted setup):
 *   Deploy IPITLSessionVerifier impl, then call PITLSessionRegistry.setPITLVerifier(addr)
 *   using the bridge account.
 */

const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Phase 26 deployer:", deployer.address);
  console.log("Deploying PITLSessionRegistry...");

  const Factory = await ethers.getContractFactory("PITLSessionRegistry");
  const registry = await Factory.deploy(deployer.address);
  await registry.waitForDeployment();
  const registryAddr = await registry.getAddress();
  console.log("PITLSessionRegistry deployed:", registryAddr);

  // Verify bridge address is set correctly
  const bridgeAddr = await registry.bridge();
  console.log("PITLSessionRegistry.bridge:", bridgeAddr);
  if (bridgeAddr.toLowerCase() !== deployer.address.toLowerCase()) {
    throw new Error("Bridge address mismatch — deployment may be incorrect");
  }

  // Write env file
  const envPath = path.join(__dirname, "../../bridge/.env.phase26");
  const envContent = [
    "# Phase 26 deployment --- " + new Date().toISOString(),
    "PITL_SESSION_REGISTRY_ADDRESS=" + registryAddr,
    "",
    "# Set PITL_SESSION_REGISTRY_ADDRESS in your bridge .env to activate ZK session proofs.",
    "# ZK proofs are optional — bridge operates in mock/open mode when pitlVerifier=address(0).",
  ].join("\n") + "\n";

  fs.writeFileSync(envPath, envContent);
  console.log("Written:", envPath);
  console.log("Phase 26 deployment complete.");
}

main().catch(err => { console.error(err); process.exit(1); });
