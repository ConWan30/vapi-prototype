/**
 * Phase 37 Deployment Script
 *
 * Deploys:
 *   1. TournamentGateV3 (suspension-aware PHG tournament eligibility)
 *
 * Reads PHGRegistry and PHGCredential addresses from env vars.
 * Writes deployed address to bridge/.env.phase37.
 *
 * TournamentGateV3 adds PHGCredential.isActive() enforcement to V2's
 * cumulative + velocity gates. A device with a suspended PHGCredential
 * cannot enter a tournament even if its PHGRegistry score meets the minimum
 * thresholds.
 *
 * Usage:
 *   cd contracts
 *   PHG_REGISTRY_ADDRESS=0x... PHG_CREDENTIAL_ADDRESS=0x... \
 *     npx hardhat run scripts/deploy-phase37.js --network iotex_testnet
 *
 * Prerequisites:
 *   - deploy.js            (for BountyMarket address)
 *   - deploy-phase22.js    (for PHG_REGISTRY_ADDRESS)
 *   - deploy-phase28.js    (for PHG_CREDENTIAL_ADDRESS)
 *
 * After deployment:
 *   - Set TOURNAMENT_GATE_V3_ADDRESS in bridge/.env
 *   - Bridge will use this address for PHGCredential enforcement status queries
 *   - Optional: wire into BountyMarket via setTournamentGate() to gate bounty
 *     entry behind V3 eligibility (requires BountyMarket admin role)
 */

const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Phase 37 deployer:", deployer.address);

  // --- Load prerequisite addresses ---
  const phgRegistryAddr   = process.env.PHG_REGISTRY_ADDRESS   || "";
  const phgCredentialAddr = process.env.PHG_CREDENTIAL_ADDRESS || "";

  if (!phgRegistryAddr)   throw new Error("PHG_REGISTRY_ADDRESS required (from deploy-phase22.js)");
  if (!phgCredentialAddr) throw new Error("PHG_CREDENTIAL_ADDRESS required (from deploy-phase28.js)");

  // --- Gate parameters (configurable via env — same defaults as V2) ---
  // minCumulative: minimum cumulative PHG score for tournament entry
  // minVelocity:   minimum recent-window score velocity
  // velocityWindow: number of PHG checkpoints to measure velocity over
  const minCumulative  = BigInt(process.env.GATE_MIN_CUMULATIVE  || "100");
  const minVelocity    = BigInt(process.env.GATE_MIN_VELOCITY    || "20");
  const velocityWindow = BigInt(process.env.GATE_VELOCITY_WINDOW || "3");

  console.log("Gate parameters:");
  console.log("  minCumulative: ", minCumulative.toString());
  console.log("  minVelocity:   ", minVelocity.toString());
  console.log("  velocityWindow:", velocityWindow.toString());

  // --- Deploy TournamentGateV3 ---
  console.log("Deploying TournamentGateV3...");
  const GateFactory = await ethers.getContractFactory("TournamentGateV3");
  const gateV3 = await GateFactory.deploy(
    phgRegistryAddr,
    phgCredentialAddr,
    minCumulative,
    minVelocity,
    velocityWindow
  );
  await gateV3.waitForDeployment();
  const gateV3Addr = await gateV3.getAddress();
  console.log("TournamentGateV3 deployed:", gateV3Addr);

  // --- Smoke tests ---
  // Verify immutable references are set correctly
  const registryOnChain   = await gateV3.phgRegistry();
  const credentialOnChain = await gateV3.phgCredential();
  const minCumulativeOnChain  = await gateV3.minCumulative();
  const minVelocityOnChain    = await gateV3.minVelocity();
  const velocityWindowOnChain = await gateV3.velocityWindow();

  console.log("\nPost-deploy verification:");
  console.log("  phgRegistry:   ", registryOnChain,   "(expected:", phgRegistryAddr, ")");
  console.log("  phgCredential: ", credentialOnChain, "(expected:", phgCredentialAddr, ")");
  console.log("  minCumulative: ", minCumulativeOnChain.toString());
  console.log("  minVelocity:   ", minVelocityOnChain.toString());
  console.log("  velocityWindow:", velocityWindowOnChain.toString());

  if (registryOnChain.toLowerCase() !== phgRegistryAddr.toLowerCase()) {
    throw new Error("phgRegistry mismatch — deployment may be incorrect");
  }
  if (credentialOnChain.toLowerCase() !== phgCredentialAddr.toLowerCase()) {
    throw new Error("phgCredential mismatch — deployment may be incorrect");
  }
  console.log("Smoke tests passed.\n");

  // --- Optional: wire into BountyMarket ---
  const bountyMarketAddr = process.env.BOUNTY_MARKET_ADDRESS || "";
  if (bountyMarketAddr) {
    console.log("Wiring TournamentGateV3 into BountyMarket...");
    const BountyMarket = await ethers.getContractAt("BountyMarket", bountyMarketAddr);
    try {
      const tx = await BountyMarket.setTournamentGate(gateV3Addr);
      await tx.wait();
      console.log("BountyMarket.setTournamentGate() confirmed:", tx.hash);
    } catch (err) {
      console.warn("WARNING: setTournamentGate() failed (may need admin role):", err.message);
      console.warn("         Wire manually: BountyMarket.setTournamentGate(", gateV3Addr, ")");
    }
  } else {
    console.log("BOUNTY_MARKET_ADDRESS not set — skipping BountyMarket wiring.");
    console.log("Wire manually if needed: BountyMarket.setTournamentGate(", gateV3Addr, ")");
  }

  // --- Write env file ---
  const envPath = path.join(__dirname, "../../bridge/.env.phase37");
  const envContent = [
    "# Phase 37 deployment --- " + new Date().toISOString(),
    "TOURNAMENT_GATE_V3_ADDRESS=" + gateV3Addr,
    "PHG_REGISTRY_ADDRESS=" + phgRegistryAddr,
    "PHG_CREDENTIAL_ADDRESS=" + phgCredentialAddr,
    "GATE_MIN_CUMULATIVE=" + minCumulative.toString(),
    "GATE_MIN_VELOCITY=" + minVelocity.toString(),
    "GATE_VELOCITY_WINDOW=" + velocityWindow.toString(),
    "",
    "# Set TOURNAMENT_GATE_V3_ADDRESS in your bridge .env to enable credential enforcement queries.",
    "# TournamentGateV3 enforces: cumulative score + velocity + PHGCredential.isActive()",
    "# A suspended credential blocks tournament entry regardless of PHG score.",
  ].join("\n") + "\n";

  fs.writeFileSync(envPath, envContent);
  console.log("Written:", envPath);
  console.log("Phase 37 deployment complete.");
  console.log("\nNext step: add TOURNAMENT_GATE_V3_ADDRESS=" + gateV3Addr + " to bridge/.env");
}

main().catch(err => { console.error(err); process.exit(1); });
