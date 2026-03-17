const { ethers } = require("hardhat");
const fs = require("fs"), path = require("path");

async function main() {
    const [deployer] = await ethers.getSigners();
    const sessReg = process.env.PITL_SESSION_REGISTRY_ADDRESS;
    const ioidReg = process.env.IOID_REGISTRY_ADDRESS;
    if (!sessReg || !ioidReg) throw new Error("Set PITL_SESSION_REGISTRY_ADDRESS and IOID_REGISTRY_ADDRESS");
    const Factory = await ethers.getContractFactory("PITLTournamentPassport");
    const passport = await Factory.deploy(deployer.address, sessReg, ioidReg);
    await passport.waitForDeployment();
    const addr = await passport.getAddress();
    console.log("PITLTournamentPassport deployed:", addr);
    fs.writeFileSync(
        path.join(__dirname, "../../bridge/.env.phase56"),
        `TOURNAMENT_PASSPORT_ADDRESS=${addr}\n`
    );
    console.log("Written to bridge/.env.phase56");
}
main().catch(console.error);
