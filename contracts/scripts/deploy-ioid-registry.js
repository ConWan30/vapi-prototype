const { ethers } = require("hardhat");
const fs = require("fs"), path = require("path");

async function main() {
    const [deployer] = await ethers.getSigners();
    console.log("Deployer:", deployer.address);
    const Factory = await ethers.getContractFactory("VAPIioIDRegistry");
    const reg = await Factory.deploy(deployer.address);
    await reg.waitForDeployment();
    const addr = await reg.getAddress();
    console.log("VAPIioIDRegistry deployed:", addr);
    if ((await reg.bridge()).toLowerCase() !== deployer.address.toLowerCase())
        throw new Error("Bridge address mismatch");
    fs.writeFileSync(
        path.join(__dirname, "../../bridge/.env.phase55"),
        `IOID_REGISTRY_ADDRESS=${addr}\n`
    );
    console.log("Written to bridge/.env.phase55");
}
main().catch(console.error);
