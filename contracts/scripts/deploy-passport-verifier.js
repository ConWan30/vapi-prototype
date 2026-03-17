/**
 * Phase 56 — Deploy TournamentPassportVerifier + wire to PITLTournamentPassport
 *
 * Deploys the Groth16 verifier for TournamentPassport.circom and wires it into
 * PITLTournamentPassport.setPassportVerifier(). After this call, the contract
 * operates in real ZK verification mode rather than mock mode.
 *
 * Usage:
 *   cd contracts && npx hardhat run scripts/deploy-passport-verifier.js --network iotex_testnet
 */

const { ethers } = require("hardhat");
const fs = require("fs"), path = require("path");

async function main() {
    const [deployer] = await ethers.getSigners();
    console.log("Deployer:", deployer.address);
    const balance = await ethers.provider.getBalance(deployer.address);
    console.log("Balance:", ethers.formatEther(balance), "IOTX");

    // Load PITLTournamentPassport address
    const envPhase56 = path.join(__dirname, "../../bridge/.env.phase56");
    const envContent = fs.readFileSync(envPhase56, "utf8");
    const match = envContent.match(/TOURNAMENT_PASSPORT_ADDRESS=(\S+)/);
    if (!match) throw new Error("TOURNAMENT_PASSPORT_ADDRESS not found in bridge/.env.phase56");
    const passportAddr = match[1];
    console.log("PITLTournamentPassport:", passportAddr);

    // Deploy TournamentPassportVerifier
    console.log("Deploying TournamentPassportVerifier...");
    const VerifierFactory = await ethers.getContractFactory("TournamentPassportVerifier");
    const verifier = await VerifierFactory.deploy();
    await verifier.waitForDeployment();
    const verifierAddr = await verifier.getAddress();
    console.log("TournamentPassportVerifier deployed:", verifierAddr);

    // Wire into PITLTournamentPassport
    const passport = await ethers.getContractAt("PITLTournamentPassport", passportAddr);
    const currentVerifier = await passport.passportVerifier();
    if (currentVerifier !== ethers.ZeroAddress) {
        throw new Error(`passportVerifier already set to ${currentVerifier} — setPassportVerifier() is one-time`);
    }

    console.log("Calling setPassportVerifier()...");
    const tx = await passport.setPassportVerifier(verifierAddr);
    await tx.wait();
    console.log("setPassportVerifier() confirmed:", tx.hash);

    // Verify
    const wired = await passport.passportVerifier();
    if (wired.toLowerCase() !== verifierAddr.toLowerCase())
        throw new Error("passportVerifier mismatch after wiring");
    console.log("Smoke test passed: PITLTournamentPassport.passportVerifier =", wired);

    // Write env
    const envOut = path.join(__dirname, "../../bridge/.env.passport-verifier");
    fs.writeFileSync(envOut, [
        `# Phase 56 Tournament Passport Verifier — ${new Date().toISOString()}`,
        `PASSPORT_VERIFIER_ADDRESS=${verifierAddr}`,
        `TOURNAMENT_PASSPORT_ADDRESS=${passportAddr}`,
        "",
        "# PITLTournamentPassport is now in live ZK verification mode.",
    ].join("\n") + "\n");
    console.log("Written to bridge/.env.passport-verifier");
}

main().catch(err => { console.error(err); process.exit(1); });
