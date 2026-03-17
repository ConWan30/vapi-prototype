/**
 * Phase 62 — Deploy PitlSessionProofVerifierV2 + wire to PITLSessionRegistryV2
 *
 * Deploys the Phase 62 Groth16 verifier (PitlSessionProof.circom with C3 constraint)
 * and wires it into PITLSessionRegistryV2.setPITLVerifier().
 *
 * Prerequisites:
 *   1. run-ceremony.js executed (Phase 62 artifacts in bridge/zk_artifacts/)
 *   2. npx hardhat compile succeeded (PitlSessionProofVerifierV2.sol compiled)
 *   3. PITLSessionRegistryV2 deployed (bridge/.env.phase62)
 *
 * Usage:
 *   cd contracts && npx hardhat run scripts/deploy-pitl-verifier-v2.js --network iotex_testnet
 */

const { ethers } = require("hardhat");
const fs = require("fs"), path = require("path");

async function main() {
    const [deployer] = await ethers.getSigners();
    console.log("Deployer:", deployer.address);
    const balance = await ethers.provider.getBalance(deployer.address);
    console.log("Balance:", ethers.formatEther(balance), "IOTX");

    // Load PITLSessionRegistryV2 address
    const envPhase62 = path.join(__dirname, "../../bridge/.env.phase62");
    const envContent = fs.readFileSync(envPhase62, "utf8");
    const match = envContent.match(/PITL_SESSION_REGISTRY_V2_ADDRESS=(\S+)/);
    if (!match) throw new Error("PITL_SESSION_REGISTRY_V2_ADDRESS not found in bridge/.env.phase62");
    const registryAddr = match[1];
    console.log("PITLSessionRegistryV2:", registryAddr);

    // Deploy PitlSessionProofVerifierV2
    console.log("Deploying PitlSessionProofVerifierV2...");
    const VerifierFactory = await ethers.getContractFactory("PitlSessionProofVerifierV2");
    const verifier = await VerifierFactory.deploy();
    await verifier.waitForDeployment();
    const verifierAddr = await verifier.getAddress();
    console.log("PitlSessionProofVerifierV2 deployed:", verifierAddr);

    // Wire into PITLSessionRegistryV2
    const registry = await ethers.getContractAt("PITLSessionRegistryV2", registryAddr);
    const currentVerifier = await registry.pitlVerifier();
    if (currentVerifier !== ethers.ZeroAddress) {
        throw new Error(`pitlVerifier already set to ${currentVerifier} — setPITLVerifier() is one-time`);
    }

    console.log("Calling setPITLVerifier()...");
    const tx = await registry.setPITLVerifier(verifierAddr);
    await tx.wait();
    console.log("setPITLVerifier() confirmed:", tx.hash);

    // Verify
    const wired = await registry.pitlVerifier();
    if (wired.toLowerCase() !== verifierAddr.toLowerCase())
        throw new Error("pitlVerifier mismatch after wiring");
    console.log("Smoke test passed: PITLSessionRegistryV2.pitlVerifier =", wired);

    // Write env
    const envOut = path.join(__dirname, "../../bridge/.env.pitl-verifier-v2");
    fs.writeFileSync(envOut, [
        `# Phase 62 PITL Verifier V2 — ${new Date().toISOString()}`,
        `PITL_VERIFIER_V2_ADDRESS=${verifierAddr}`,
        `PITL_SESSION_REGISTRY_V2_ADDRESS=${registryAddr}`,
        "",
        "# PITLSessionRegistryV2 is now in live ZK verification mode (Phase 62 C3 circuit).",
    ].join("\n") + "\n");
    console.log("Written to bridge/.env.pitl-verifier-v2");
}

main().catch(err => { console.error(err); process.exit(1); });
