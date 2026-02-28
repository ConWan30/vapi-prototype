/**
 * VAPI — Full Deployment Script for IoTeX
 *
 * Deploys all six contracts in dependency order:
 *   1. TieredDeviceRegistry (standalone; IS-A DeviceRegistry, Phase 7)
 *   2. PoACVerifier         (depends on DeviceRegistry)
 *   3. BountyMarket         (depends on PoACVerifier + DeviceRegistry)
 *   4. SkillOracle          (depends on PoACVerifier)
 *   5. ProgressAttestation  (depends on PoACVerifier)
 *   6. TeamProofAggregator  (depends on PoACVerifier)
 *
 * After deployment:
 *   - Grants the PoACVerifier and BountyMarket reputation-updater roles
 *     in the DeviceRegistry.
 *   - Logs all contract addresses for firmware configuration.
 *   - Writes bridge/.env.testnet with all deployed addresses.
 *
 * Usage:
 *   npx hardhat run scripts/deploy.js --network iotex_testnet
 */

const hre = require("hardhat");
const fs  = require("fs");
const path = require("path");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const balance = await hre.ethers.provider.getBalance(deployer.address);

  console.log("=== VAPI Contract Deployment ===");
  console.log(`Network:  ${hre.network.name}`);
  console.log(`Deployer: ${deployer.address}`);
  console.log(`Balance:  ${hre.ethers.formatEther(balance)} IOTX`);
  console.log("");

  // --- Configuration from environment ---
  const maxTimestampSkew = process.env.MAX_TIMESTAMP_SKEW || 3600;
  const platformFeeBps = process.env.PLATFORM_FEE_BPS || 250;

  // --- Phase 7: Tier deposits — override via env for mainnet ---
  const emulatedDeposit = process.env.EMULATED_DEPOSIT
      ? hre.ethers.parseEther(process.env.EMULATED_DEPOSIT)
      : hre.ethers.parseEther("0.1");   // testnet; mainnet target: 10 IOTX
  const standardDeposit = process.env.STANDARD_DEPOSIT
      ? hre.ethers.parseEther(process.env.STANDARD_DEPOSIT)
      : hre.ethers.parseEther("1");     // testnet; mainnet target: 100 IOTX
  const attestedDeposit = process.env.ATTESTED_DEPOSIT
      ? hre.ethers.parseEther(process.env.ATTESTED_DEPOSIT)
      : hre.ethers.parseEther("0.01");  // testnet; mainnet target: 1 IOTX

  console.log("Tier deposits:");
  console.log(`  Emulated: ${hre.ethers.formatEther(emulatedDeposit)} IOTX`);
  console.log(`  Standard: ${hre.ethers.formatEther(standardDeposit)} IOTX`);
  console.log(`  Attested: ${hre.ethers.formatEther(attestedDeposit)} IOTX`);
  console.log("");

  // --- 1. Deploy TieredDeviceRegistry ---
  console.log("1/6 Deploying TieredDeviceRegistry...");
  const TieredDeviceRegistry = await hre.ethers.getContractFactory("TieredDeviceRegistry");
  const registry = await TieredDeviceRegistry.deploy(
      emulatedDeposit, standardDeposit, attestedDeposit
  );
  await registry.waitForDeployment();
  const registryAddr = await registry.getAddress();
  console.log(`     TieredDeviceRegistry deployed at: ${registryAddr}`);

  // --- 2. Deploy PoACVerifier ---
  console.log("2/6 Deploying PoACVerifier...");
  const PoACVerifier = await hre.ethers.getContractFactory("PoACVerifier");
  const verifier = await PoACVerifier.deploy(registryAddr, maxTimestampSkew);
  await verifier.waitForDeployment();
  const verifierAddr = await verifier.getAddress();
  console.log(`     PoACVerifier deployed at:         ${verifierAddr}`);

  // --- 3. Deploy BountyMarket ---
  console.log("3/6 Deploying BountyMarket...");
  const BountyMarket = await hre.ethers.getContractFactory("BountyMarket");
  const market = await BountyMarket.deploy(verifierAddr, registryAddr, platformFeeBps);
  await market.waitForDeployment();
  const marketAddr = await market.getAddress();
  console.log(`     BountyMarket deployed at:         ${marketAddr}`);

  // --- 4. Deploy SkillOracle ---
  console.log("4/6 Deploying SkillOracle...");
  const SkillOracle = await hre.ethers.getContractFactory("SkillOracle");
  const oracle = await SkillOracle.deploy(verifierAddr);
  await oracle.waitForDeployment();
  const oracleAddr = await oracle.getAddress();
  console.log(`     SkillOracle deployed at:          ${oracleAddr}`);

  // --- 5. Deploy ProgressAttestation ---
  console.log("5/6 Deploying ProgressAttestation...");
  const ProgressAttestation = await hre.ethers.getContractFactory("ProgressAttestation");
  const progress = await ProgressAttestation.deploy(verifierAddr);
  await progress.waitForDeployment();
  const progressAddr = await progress.getAddress();
  console.log(`     ProgressAttestation deployed at:  ${progressAddr}`);

  // --- 6. Deploy TeamProofAggregator ---
  console.log("6/6 Deploying TeamProofAggregator...");
  const TeamProofAggregator = await hre.ethers.getContractFactory("TeamProofAggregator");
  const teamAgg = await TeamProofAggregator.deploy(verifierAddr);
  await teamAgg.waitForDeployment();
  const teamAggAddr = await teamAgg.getAddress();
  console.log(`     TeamProofAggregator deployed at:  ${teamAggAddr}`);

  // --- 7. Grant reputation-updater roles ---
  console.log("");
  console.log("Granting reputation-updater roles...");

  // PoACVerifier needs to update device reputation after verification
  let tx = await registry.setReputationUpdater(verifierAddr, true);
  await tx.wait();
  console.log(`  PoACVerifier (${verifierAddr}) granted updater role`);

  // BountyMarket needs to update reputation on bounty completion
  tx = await registry.setReputationUpdater(marketAddr, true);
  await tx.wait();
  console.log(`  BountyMarket (${marketAddr}) granted updater role`);

  // --- 8. Summary ---
  console.log("");
  console.log("=== Deployment Complete ===");
  console.log("");
  console.log("Contract Addresses:");
  console.log(`  DEVICE_REGISTRY_ADDRESS        = "${registryAddr}"`);
  console.log(`  POAC_VERIFIER_ADDRESS          = "${verifierAddr}"`);
  console.log(`  BOUNTY_MARKET_ADDRESS          = "${marketAddr}"`);
  console.log(`  SKILL_ORACLE_ADDRESS           = "${oracleAddr}"`);
  console.log(`  PROGRESS_ATTESTATION_ADDRESS   = "${progressAddr}"`);
  console.log(`  TEAM_AGGREGATOR_ADDRESS        = "${teamAggAddr}"`);
  console.log("");
  console.log("Tier Deposits:");
  console.log(`  Emulated: ${hre.ethers.formatEther(emulatedDeposit)} IOTX (mainnet target: 10 IOTX)`);
  console.log(`  Standard: ${hre.ethers.formatEther(standardDeposit)} IOTX (mainnet target: 100 IOTX)`);
  console.log(`  Attested: ${hre.ethers.formatEther(attestedDeposit)} IOTX (mainnet target: 1 IOTX)`);
  console.log("");
  console.log("Configuration:");
  console.log(`  Max timestamp skew: ${maxTimestampSkew} seconds`);
  console.log(`  Platform fee:       ${platformFeeBps / 100}%`);
  console.log("");
  console.log("Next steps:");
  console.log("  1. The bridge will auto-register the device on first DualShock startup.");
  console.log("  2. Post a test bounty: BountyMarket.postBounty(...) with deposit.");
  console.log("  3. Flash firmware and watch PoAC records flow to the verifier.");
  console.log("");

  // --- 9. Write deployment JSON ---
  const deployment = {
    network: hre.network.name,
    chainId: hre.network.config.chainId,
    deployer: deployer.address,
    timestamp: new Date().toISOString(),
    contracts: {
      TieredDeviceRegistry: registryAddr,
      PoACVerifier:         verifierAddr,
      BountyMarket:         marketAddr,
      SkillOracle:          oracleAddr,
      ProgressAttestation:  progressAddr,
      TeamProofAggregator:  teamAggAddr,
    },
    config: {
      emulatedDeposit:  emulatedDeposit.toString(),
      standardDeposit:  standardDeposit.toString(),
      attestedDeposit:  attestedDeposit.toString(),
      maxTimestampSkew: Number(maxTimestampSkew),
      platformFeeBps:   Number(platformFeeBps),
    },
  };

  const deploymentPath = `./deployments/${hre.network.name}-${Date.now()}.json`;
  fs.mkdirSync("./deployments", { recursive: true });
  fs.writeFileSync(deploymentPath, JSON.stringify(deployment, null, 2));
  console.log(`Deployment info saved to: ${deploymentPath}`);

  // --- 10. Write bridge/.env.testnet ---
  const chainId = hre.network.config.chainId || 4690;
  const rpcUrl  = hre.network.config.url || "https://babel-api.testnet.iotex.io";

  const envContent = `# VAPI Bridge — IoTeX Deployment Configuration
# Auto-generated by contracts/scripts/deploy.js
# Network: ${hre.network.name}  |  Deployed: ${new Date().toISOString()}

# -- Network ------------------------------------------------------------------
IOTEX_RPC_URL=${rpcUrl}
IOTEX_CHAIN_ID=${chainId}

# -- Contract Addresses -------------------------------------------------------
POAC_VERIFIER_ADDRESS=${verifierAddr}
BOUNTY_MARKET_ADDRESS=${marketAddr}
DEVICE_REGISTRY_ADDRESS=${registryAddr}
SKILL_ORACLE_ADDRESS=${oracleAddr}
PROGRESS_ATTESTATION_ADDRESS=${progressAddr}
TEAM_AGGREGATOR_ADDRESS=${teamAggAddr}

# -- Bridge Wallet (KEEP SECRET) ----------------------------------------------
BRIDGE_PRIVATE_KEY=0x<your-testnet-private-key>

# -- DualShock Integration (Phase 3/4) ----------------------------------------
DUALSHOCK_ENABLED=true
DUALSHOCK_RECORD_INTERVAL_S=1.0
DUALSHOCK_ACTIVE_BOUNTIES=
DUALSHOCK_KEY_DIR=~/.vapi

# -- Phase 7: Tiered Registration ---------------------------------------------
DEVICE_REGISTRATION_TIER=Standard
ATTESTATION_PROOF_HEX=
# Tier deposits deployed: Emulated=${hre.ethers.formatEther(emulatedDeposit)} IOTX, Standard=${hre.ethers.formatEther(standardDeposit)} IOTX, Attested=${hre.ethers.formatEther(attestedDeposit)} IOTX

# -- Gas / Tuning -------------------------------------------------------------
# IoTeX testnet gas price: ~1000 Gwei (auto-estimated by bridge)
# Faucet: https://faucet.iotex.io
`;

  const envPath = path.join(__dirname, "..", "..", "bridge", ".env.testnet");
  fs.writeFileSync(envPath, envContent);
  console.log(`Bridge .env.testnet written to: ${envPath}`);
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("Deployment failed:", error);
    process.exit(1);
  });
