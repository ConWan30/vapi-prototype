/**
 * deploy-pitl-verifier.js — Deploy PitlSessionProofVerifier + wire to PITLSessionRegistry
 *
 * Deploys the auto-generated Groth16 verifier for the PitlSessionProof circuit
 * and wires it into PITLSessionRegistry.setPITLVerifier().
 *
 * Prerequisites:
 *   1. setup.sh must have run (generates PitlSessionProofVerifier.sol)
 *   2. npx hardhat compile must succeed (PitlSessionProofVerifier.sol compiled)
 *   3. PITL_SESSION_REGISTRY_ADDRESS must be set (from deploy-phase26.js)
 *
 * Usage:
 *   cd contracts
 *   PITL_SESSION_REGISTRY_ADDRESS=0x... \
 *     npx hardhat run scripts/deploy-pitl-verifier.js --network iotex_testnet
 *
 * Once wired, PITLSessionRegistry will verify real Groth16 PITL session proofs
 * on-chain instead of operating in mock mode (pitlVerifier == address(0)).
 *
 * NOTE: setPITLVerifier() is one-time — it can only be called when the current
 * verifier address is address(0). Plan your deployment accordingly.
 */

const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("PITL Verifier deployer:", deployer.address);

  // --- Check PitlSessionProofVerifier.sol exists ---
  const verifierSol = path.join(__dirname, "../contracts/PitlSessionProofVerifier.sol");
  if (!fs.existsSync(verifierSol)) {
    throw new Error(
      "PitlSessionProofVerifier.sol not found at " + verifierSol + "\n" +
      "Run `cd contracts/circuits && bash setup.sh` first to generate it."
    );
  }
  console.log("PitlSessionProofVerifier.sol found:", verifierSol);

  // --- Load prerequisite addresses ---
  const pitlRegistryAddr = process.env.PITL_SESSION_REGISTRY_ADDRESS || "";
  if (!pitlRegistryAddr) {
    throw new Error("PITL_SESSION_REGISTRY_ADDRESS required (from deploy-phase26.js)");
  }

  // --- Deploy PitlSessionProofVerifier ---
  console.log("Deploying PitlSessionProofVerifier...");
  const VerifierFactory = await ethers.getContractFactory("PitlSessionProofVerifier");
  const verifier = await VerifierFactory.deploy();
  await verifier.waitForDeployment();
  const verifierAddr = await verifier.getAddress();
  console.log("PitlSessionProofVerifier deployed:", verifierAddr);

  // --- Wire into PITLSessionRegistry ---
  console.log("Wiring PitlSessionProofVerifier into PITLSessionRegistry...");
  const PITLRegistry = await ethers.getContractAt("PITLSessionRegistry", pitlRegistryAddr);

  // Verify current verifier is address(0) (setPITLVerifier is one-time)
  const currentVerifier = await PITLRegistry.pitlVerifier();
  if (currentVerifier !== ethers.ZeroAddress) {
    throw new Error(
      "PITLSessionRegistry.pitlVerifier is already set to " + currentVerifier +
      "\nsetPITLVerifier() is one-time and cannot be called again."
    );
  }

  const tx = await PITLRegistry.setPITLVerifier(verifierAddr);
  await tx.wait();
  console.log("PITLSessionRegistry.setPITLVerifier() confirmed:", tx.hash);

  // --- Smoke test ---
  const verifierOnChain = await PITLRegistry.pitlVerifier();
  if (verifierOnChain.toLowerCase() !== verifierAddr.toLowerCase()) {
    throw new Error("pitlVerifier mismatch after setPITLVerifier()");
  }
  console.log("Smoke test passed: PITLSessionRegistry.pitlVerifier =", verifierOnChain);

  // --- Write env file ---
  const envPath = path.join(__dirname, "../../bridge/.env.pitl-verifier");
  const envContent = [
    "# PITL Verifier deployment --- " + new Date().toISOString(),
    "PITL_VERIFIER_ADDRESS=" + verifierAddr,
    "PITL_SESSION_REGISTRY_ADDRESS=" + pitlRegistryAddr,
    "",
    "# PITLSessionRegistry is now in live ZK verification mode.",
    "# Real Groth16 PITL proofs will be verified on-chain.",
    "# Bridge PITL proof submission is now binding.",
  ].join("\n") + "\n";

  fs.writeFileSync(envPath, envContent);
  console.log("Written:", envPath);
  console.log("PITL verifier deployment complete.");
}

main().catch(err => { console.error(err); process.exit(1); });
