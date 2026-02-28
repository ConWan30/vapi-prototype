/**
 * Phase 25 Deployment Script
 *
 * Deploys:
 *   1. TournamentGateV2 (velocity-gated PHG eligibility)
 *   2. Wires TournamentGateV2 into BountyMarket via setTournamentGate()
 *
 * Reads PHGRegistry and BountyMarket addresses from bridge/.env or env vars.
 * Writes deployed addresses to bridge/.env.phase25.
 *
 * Usage:
 *   cd contracts && npx hardhat run scripts/deploy-phase25.js --network iotex_testnet
 */

const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Phase 25 deployer:", deployer.address);

  // --- Load prerequisite addresses ---
  const phgRegistryAddr = process.env.PHG_REGISTRY_ADDRESS || "";
  const bountyMarketAddr = process.env.BOUNTY_MARKET_ADDRESS || "";

  if (!phgRegistryAddr) throw new Error("PHG_REGISTRY_ADDRESS required");
  if (!bountyMarketAddr) throw new Error("BOUNTY_MARKET_ADDRESS required");

  // --- Gate parameters (configurable via env) ---
  const minCumulative  = BigInt(process.env.GATE_MIN_CUMULATIVE  || "100");
  const minVelocity    = BigInt(process.env.GATE_MIN_VELOCITY    || "20");
  const velocityWindow = BigInt(process.env.GATE_VELOCITY_WINDOW || "3");

  // --- Deploy TournamentGateV2 ---
  console.log("Deploying TournamentGateV2...");
  const GateFactory = await ethers.getContractFactory("TournamentGateV2");
  const gateV2 = await GateFactory.deploy(
    phgRegistryAddr, minCumulative, minVelocity, velocityWindow
  );
  await gateV2.waitForDeployment();
  const gateV2Addr = await gateV2.getAddress();
  console.log("TournamentGateV2 deployed:", gateV2Addr);

  // --- Wire into BountyMarket ---
  console.log("Wiring TournamentGateV2 into BountyMarket...");
  const BountyMarket = await ethers.getContractAt("BountyMarket", bountyMarketAddr);
  const tx = await BountyMarket.setTournamentGate(gateV2Addr);
  await tx.wait();
  console.log("BountyMarket.setTournamentGate() confirmed");

  // --- Write env file ---
  const envPath = path.join(__dirname, "../../bridge/.env.phase25");
  const envContent = [
    "# Phase 25 deployment --- " + new Date().toISOString(),
    "TOURNAMENT_GATE_V2_ADDRESS=" + gateV2Addr,
    "PHG_REGISTRY_ADDRESS=" + phgRegistryAddr,
    "BOUNTY_MARKET_ADDRESS=" + bountyMarketAddr,
    "GATE_MIN_CUMULATIVE=" + minCumulative,
    "GATE_MIN_VELOCITY=" + minVelocity,
    "GATE_VELOCITY_WINDOW=" + velocityWindow,
  ].join("
") + "
";

  fs.writeFileSync(envPath, envContent);
  console.log("Written:", envPath);
  console.log("Phase 25 deployment complete.");
}

main().catch(err => { console.error(err); process.exit(1); });
