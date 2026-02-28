/**
 * deploy-mainnet.js — VAPI Mainnet Deployment Script
 *
 * Deploys all six contracts with mainnet-grade deposits and registers the
 * initial hardware manufacturer P256 key. Does NOT enable attestation
 * enforcement or transfer ownership — those are deliberate manual steps.
 *
 * Pre-flight requirements:
 *   1. Run scripts/test_p256_precompile.js on IoTeX testnet first (verify layout).
 *   2. Run full end-to-end test on testnet with a real YubiKey/ATECC608A device.
 *   3. Confirm bridge keystore is set up (BRIDGE_PRIVATE_KEY_SOURCE=keystore).
 *   4. Ensure deployer wallet has sufficient IOTX (estimate: ~500 IOTX).
 *
 * Required env vars:
 *   MANUFACTURER_ADDRESS   Ethereum address of the hardware manufacturer
 *   MANUFACTURER_PUBKEY_X  32-byte hex P256 x-coordinate (0x-prefixed, 66 chars)
 *   MANUFACTURER_PUBKEY_Y  32-byte hex P256 y-coordinate (0x-prefixed, 66 chars)
 *   MANUFACTURER_NAME      Human-readable label (e.g. "Yubico Inc")
 *
 * Optional env vars (mainnet deposits override testnet defaults):
 *   EMULATED_DEPOSIT  default: 10  IOTX
 *   STANDARD_DEPOSIT  default: 100 IOTX
 *   ATTESTED_DEPOSIT  default: 1   IOTX
 *
 * Usage:
 *   npx hardhat run scripts/deploy-mainnet.js --network iotex_mainnet
 *
 * After deployment:
 *   1. Verify all contract addresses on iotexscan.io
 *   2. Run E2E test: register a real attested device, submit bounty evidence
 *   3. Run scripts/enable-enforcement.js (separate deliberate step)
 *   4. Transfer registry ownership to Gnosis Safe (separate deliberate step)
 */

const hre  = require("hardhat");
const fs   = require("fs");
const path = require("path");

function requireEnv(name) {
    const val = process.env[name];
    if (!val) {
        console.error(`ERROR: Required env var ${name} is not set.`);
        process.exit(1);
    }
    return val;
}

async function main() {
    const [deployer] = await hre.ethers.getSigners();
    const balance    = await hre.ethers.provider.getBalance(deployer.address);

    console.log("=== VAPI Mainnet Deployment ===");
    console.log(`Network:  ${hre.network.name}`);
    console.log(`Deployer: ${deployer.address}`);
    console.log(`Balance:  ${hre.ethers.formatEther(balance)} IOTX`);
    console.log("");

    if (hre.network.name === "hardhat" || hre.network.name === "localhost") {
        console.warn("WARNING: Running mainnet deployment against local Hardhat network.");
        console.warn("Use --network iotex_mainnet for production deployment.");
        console.warn("");
    }

    // --- Mainnet deposits (significantly higher for Sybil resistance) ---
    const emulatedDeposit = process.env.EMULATED_DEPOSIT
        ? hre.ethers.parseEther(process.env.EMULATED_DEPOSIT)
        : hre.ethers.parseEther("10");   // 10 IOTX mainnet
    const standardDeposit = process.env.STANDARD_DEPOSIT
        ? hre.ethers.parseEther(process.env.STANDARD_DEPOSIT)
        : hre.ethers.parseEther("100");  // 100 IOTX mainnet
    const attestedDeposit = process.env.ATTESTED_DEPOSIT
        ? hre.ethers.parseEther(process.env.ATTESTED_DEPOSIT)
        : hre.ethers.parseEther("1");    // 1 IOTX mainnet

    // --- Manufacturer key (required for V2 attestation on mainnet) ---
    const manufacturerAddr = requireEnv("MANUFACTURER_ADDRESS");
    const manufacturerX    = requireEnv("MANUFACTURER_PUBKEY_X");
    const manufacturerY    = requireEnv("MANUFACTURER_PUBKEY_Y");
    const manufacturerName = requireEnv("MANUFACTURER_NAME");

    const maxTimestampSkew = process.env.MAX_TIMESTAMP_SKEW || 3600;
    const platformFeeBps   = process.env.PLATFORM_FEE_BPS   || 250;

    console.log("Mainnet tier deposits:");
    console.log(`  Emulated: ${hre.ethers.formatEther(emulatedDeposit)} IOTX`);
    console.log(`  Standard: ${hre.ethers.formatEther(standardDeposit)} IOTX`);
    console.log(`  Attested: ${hre.ethers.formatEther(attestedDeposit)} IOTX`);
    console.log("");
    console.log("Manufacturer key to register:");
    console.log(`  Address: ${manufacturerAddr}`);
    console.log(`  Name:    ${manufacturerName}`);
    console.log(`  X:       ${manufacturerX}`);
    console.log(`  Y:       ${manufacturerY}`);
    console.log("");

    // -----------------------------------------------------------------------
    // 1. Deploy TieredDeviceRegistry
    // -----------------------------------------------------------------------
    console.log("1/6 Deploying TieredDeviceRegistry...");
    const TDR = await hre.ethers.getContractFactory("TieredDeviceRegistry");
    const registry = await TDR.deploy(emulatedDeposit, standardDeposit, attestedDeposit);
    await registry.waitForDeployment();
    const registryAddr = await registry.getAddress();
    console.log(`     TieredDeviceRegistry: ${registryAddr}`);

    // -----------------------------------------------------------------------
    // 2. Deploy PoACVerifier
    // -----------------------------------------------------------------------
    console.log("2/6 Deploying PoACVerifier...");
    const PoACVerifier = await hre.ethers.getContractFactory("PoACVerifier");
    const verifier     = await PoACVerifier.deploy(registryAddr, maxTimestampSkew);
    await verifier.waitForDeployment();
    const verifierAddr = await verifier.getAddress();
    console.log(`     PoACVerifier:         ${verifierAddr}`);

    // -----------------------------------------------------------------------
    // 3. Deploy BountyMarket
    // -----------------------------------------------------------------------
    console.log("3/6 Deploying BountyMarket...");
    const BountyMarket = await hre.ethers.getContractFactory("BountyMarket");
    const market       = await BountyMarket.deploy(verifierAddr, registryAddr, platformFeeBps);
    await market.waitForDeployment();
    const marketAddr   = await market.getAddress();
    console.log(`     BountyMarket:         ${marketAddr}`);

    // -----------------------------------------------------------------------
    // 4. Deploy SkillOracle
    // -----------------------------------------------------------------------
    console.log("4/6 Deploying SkillOracle...");
    const SkillOracle = await hre.ethers.getContractFactory("SkillOracle");
    const oracle      = await SkillOracle.deploy(verifierAddr);
    await oracle.waitForDeployment();
    const oracleAddr  = await oracle.getAddress();
    console.log(`     SkillOracle:          ${oracleAddr}`);

    // -----------------------------------------------------------------------
    // 5. Deploy ProgressAttestation
    // -----------------------------------------------------------------------
    console.log("5/6 Deploying ProgressAttestation...");
    const ProgressAttestation = await hre.ethers.getContractFactory("ProgressAttestation");
    const progress            = await ProgressAttestation.deploy(verifierAddr);
    await progress.waitForDeployment();
    const progressAddr        = await progress.getAddress();
    console.log(`     ProgressAttestation:  ${progressAddr}`);

    // -----------------------------------------------------------------------
    // 6. Deploy TeamProofAggregator
    // -----------------------------------------------------------------------
    console.log("6/6 Deploying TeamProofAggregator...");
    const TeamProofAggregator = await hre.ethers.getContractFactory("TeamProofAggregator");
    const teamAgg             = await TeamProofAggregator.deploy(verifierAddr);
    await teamAgg.waitForDeployment();
    const teamAggAddr         = await teamAgg.getAddress();
    console.log(`     TeamProofAggregator:  ${teamAggAddr}`);

    // -----------------------------------------------------------------------
    // 7. Grant reputation-updater roles
    // -----------------------------------------------------------------------
    console.log("");
    console.log("Granting reputation-updater roles...");
    let tx = await registry.setReputationUpdater(verifierAddr, true);
    await tx.wait();
    console.log(`  PoACVerifier (${verifierAddr}) granted`);
    tx = await registry.setReputationUpdater(marketAddr, true);
    await tx.wait();
    console.log(`  BountyMarket (${marketAddr}) granted`);

    // -----------------------------------------------------------------------
    // 8. Register initial manufacturer P256 key
    //    NOTE: Does NOT call setAttestationEnforced(true) — that is a deliberate
    //    manual step after E2E validation. See scripts/enable-enforcement.js.
    // -----------------------------------------------------------------------
    console.log("");
    console.log("Registering manufacturer P256 key...");
    tx = await registry.setManufacturerKey(
        manufacturerAddr,
        manufacturerX,
        manufacturerY,
        manufacturerName,
    );
    const receipt = await tx.wait();
    console.log(`  ManufacturerKeySet tx: ${receipt.hash}`);
    console.log(`  Manufacturer: ${manufacturerAddr} (${manufacturerName})`);

    // -----------------------------------------------------------------------
    // 9. Summary and deployment JSON
    // -----------------------------------------------------------------------
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
    console.log("Manufacturer Key:");
    console.log(`  Address: ${manufacturerAddr} (${manufacturerName})`);
    console.log(`  Status:  registered, active=true`);
    console.log("");
    console.log("IMPORTANT — Manual steps still required:");
    console.log("  1. Verify all contracts on iotexscan.io");
    console.log("  2. Run E2E test: register attested device, submit bounty evidence");
    console.log("  3. scripts/enable-enforcement.js — enable P256 verification");
    console.log("  4. Transfer registry ownership to Gnosis Safe multisig");
    console.log("  5. Update BRIDGE_PRIVATE_KEY_SOURCE=keystore on bridge servers");
    console.log("");
    console.log("attestationEnforced = false  (call enable-enforcement.js when ready)");
    console.log("");

    // Write deployment JSON
    const deployment = {
        network:    hre.network.name,
        chainId:    hre.network.config.chainId,
        deployer:   deployer.address,
        timestamp:  new Date().toISOString(),
        contracts: {
            TieredDeviceRegistry: registryAddr,
            PoACVerifier:         verifierAddr,
            BountyMarket:         marketAddr,
            SkillOracle:          oracleAddr,
            ProgressAttestation:  progressAddr,
            TeamProofAggregator:  teamAggAddr,
        },
        manufacturer: {
            address: manufacturerAddr,
            name:    manufacturerName,
            pubkeyX: manufacturerX,
            pubkeyY: manufacturerY,
        },
        config: {
            emulatedDeposit:  emulatedDeposit.toString(),
            standardDeposit:  standardDeposit.toString(),
            attestedDeposit:  attestedDeposit.toString(),
            maxTimestampSkew: Number(maxTimestampSkew),
            platformFeeBps:   Number(platformFeeBps),
            attestationEnforced: false,
        },
    };

    const deploymentPath = `./deployments/mainnet-${Date.now()}.json`;
    fs.mkdirSync("./deployments", { recursive: true });
    fs.writeFileSync(deploymentPath, JSON.stringify(deployment, null, 2));
    console.log(`Deployment record saved to: ${deploymentPath}`);
}

main()
    .then(() => process.exit(0))
    .catch((err) => {
        console.error("Mainnet deployment failed:", err);
        process.exit(1);
    });
