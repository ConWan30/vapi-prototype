/**
 * Phase 28 Deployment Script
 *
 * Deploys:
 *   1. PHGCredential (bridge = deployer.address)
 *
 * Writes deployed address to bridge/.env.phase28.
 *
 * Usage:
 *   cd contracts && npx hardhat run scripts/deploy-phase28.js --network iotex_testnet
 *
 * After deployment, set PHG_CREDENTIAL_ADDRESS in your bridge environment.
 * The bridge will automatically mint credentials when devices earn PHG checkpoints
 * and have PITL session proofs on record.
 */

const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Phase 28 deployer:", deployer.address);
  console.log("Deploying PHGCredential...");

  const Factory = await ethers.getContractFactory("PHGCredential");
  const cred = await Factory.deploy(deployer.address);
  await cred.waitForDeployment();
  const credAddr = await cred.getAddress();
  console.log("PHGCredential deployed:", credAddr);

  // Verify bridge address is set correctly
  const bridgeAddr = await cred.bridge();
  console.log("PHGCredential.bridge:", bridgeAddr);
  if (bridgeAddr.toLowerCase() !== deployer.address.toLowerCase()) {
    throw new Error("Bridge address mismatch — deployment may be incorrect");
  }

  // Verify locked() ERC-5192 invariant
  const isLocked = await cred.locked(0);
  console.log("PHGCredential.locked(0):", isLocked, "(should be true)");
  if (!isLocked) {
    throw new Error("locked() returned false — ERC-5192 invariant violated");
  }

  // Write env file
  const envPath = path.join(__dirname, "../../bridge/.env.phase28");
  const envContent = [
    "# Phase 28 deployment --- " + new Date().toISOString(),
    "PHG_CREDENTIAL_ADDRESS=" + credAddr,
    "",
    "# Set PHG_CREDENTIAL_ADDRESS in your bridge .env to activate soulbound credential minting.",
    "# Credentials are minted automatically when a device earns a PHG checkpoint",
    "# AND has a PITL session proof on record in pitl_session_proofs.",
    "# Credential mint is non-fatal — bridge operates normally if credential contract is absent.",
  ].join("\n") + "\n";

  fs.writeFileSync(envPath, envContent);
  console.log("Written:", envPath);
  console.log("Phase 28 deployment complete.");
}

main().catch(err => { console.error(err); process.exit(1); });
