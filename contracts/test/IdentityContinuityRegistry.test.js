const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("IdentityContinuityRegistry", function () {
  let phgRegistry, identityRegistry;
  let bridge, other;

  const DEV_OLD = ethers.encodeBytes32String("device_old");
  const DEV_NEW = ethers.encodeBytes32String("device_new");
  const DEV_C   = ethers.encodeBytes32String("device_c");
  const PROOF   = ethers.encodeBytes32String("biometric_proof_1");

  beforeEach(async function () {
    [bridge, other] = await ethers.getSigners();

    const PHGRegistry = await ethers.getContractFactory("PHGRegistry");
    phgRegistry = await PHGRegistry.deploy(bridge.address);
    await phgRegistry.waitForDeployment();

    const ICR = await ethers.getContractFactory("IdentityContinuityRegistry");
    identityRegistry = await ICR.deploy(bridge.address, await phgRegistry.getAddress());
    await identityRegistry.waitForDeployment();

    // Wire identity registry into PHGRegistry
    await phgRegistry.connect(bridge).setIdentityRegistry(await identityRegistry.getAddress());
  });

  // -------------------------------------------------------------------------
  // Deployment
  // -------------------------------------------------------------------------

  it("1. deploys with correct bridge and phgRegistry addresses", async function () {
    expect(await identityRegistry.bridge()).to.equal(bridge.address);
    expect(await identityRegistry.phgRegistry()).to.equal(await phgRegistry.getAddress());
  });

  // -------------------------------------------------------------------------
  // Access control
  // -------------------------------------------------------------------------

  it("2. attestContinuity: reverts for non-bridge caller", async function () {
    await expect(
      identityRegistry.connect(other).attestContinuity(DEV_OLD, DEV_NEW, PROOF)
    ).to.be.revertedWithCustomError(identityRegistry, "OnlyBridge");
  });

  // -------------------------------------------------------------------------
  // Happy path
  // -------------------------------------------------------------------------

  it("3. attestContinuity: emits ContinuityAttested with correct scoreMigrated", async function () {
    const bio = ethers.encodeBytes32String("bio_hash");
    await phgRegistry.connect(bridge).commitCheckpoint(DEV_OLD, 50, 10, bio);
    expect(await phgRegistry.cumulativeScore(DEV_OLD)).to.equal(50n);

    const tx = await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    const receipt = await tx.wait();
    const event = receipt.logs.find(
      (l) => l.fragment && l.fragment.name === "ContinuityAttested"
    );
    expect(event).to.not.be.undefined;
    expect(event.args.scoreMigrated).to.equal(50n);
    expect(event.args.oldDeviceId).to.equal(DEV_OLD);
    expect(event.args.newDeviceId).to.equal(DEV_NEW);
    expect(event.args.biometricProofHash).to.equal(PROOF);
  });

  it("4. attestContinuity: marks newDeviceId as claimed", async function () {
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    expect(await identityRegistry.claimed(DEV_NEW)).to.be.true;
  });

  it("5. attestContinuity: marks oldDeviceId as claimed (source locked)", async function () {
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    expect(await identityRegistry.claimed(DEV_OLD)).to.be.true;
  });

  it("6. attestContinuity: second claim on same newDeviceId reverts AlreadyClaimed", async function () {
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    await expect(
      identityRegistry.connect(bridge).attestContinuity(DEV_C, DEV_NEW, PROOF)
    ).to.be.revertedWithCustomError(identityRegistry, "AlreadyClaimed");
  });

  it("7. using claimed oldDeviceId as source again reverts SourceAlreadyClaimed", async function () {
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    await expect(
      identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_C, PROOF)
    ).to.be.revertedWithCustomError(identityRegistry, "SourceAlreadyClaimed");
  });

  // -------------------------------------------------------------------------
  // Mapping state
  // -------------------------------------------------------------------------

  it("8. continuedFrom mapping: set correctly after attestation", async function () {
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    expect(await identityRegistry.continuedFrom(DEV_NEW)).to.equal(DEV_OLD);
  });

  // -------------------------------------------------------------------------
  // View functions
  // -------------------------------------------------------------------------

  it("9. isContinuationOf: returns true after attestation", async function () {
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    expect(await identityRegistry.isContinuationOf(DEV_NEW, DEV_OLD)).to.be.true;
  });

  it("10. isContinuationOf: returns false for unrelated devices", async function () {
    expect(await identityRegistry.isContinuationOf(DEV_NEW, DEV_OLD)).to.be.false;
  });

  it("11. getCanonicalRoot: returns self for unclaimed device", async function () {
    expect(await identityRegistry.getCanonicalRoot(DEV_OLD)).to.equal(DEV_OLD);
  });

  it("12. getCanonicalRoot: returns root for single-hop chain", async function () {
    // Anti-replay: each device can only participate in one attestation.
    // After DEV_OLD -> DEV_NEW attestation, getCanonicalRoot(DEV_NEW) walks
    // to DEV_OLD (which has no further continuedFrom), returning DEV_OLD.
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    // DEV_NEW -> root is DEV_OLD (since DEV_OLD has no predecessor)
    expect(await identityRegistry.getCanonicalRoot(DEV_NEW)).to.equal(DEV_OLD);
    // DEV_OLD -> root is DEV_OLD (no predecessor)
    expect(await identityRegistry.getCanonicalRoot(DEV_OLD)).to.equal(DEV_OLD);
  });

  // -------------------------------------------------------------------------
  // PHGRegistry.inheritScore integration
  // -------------------------------------------------------------------------

  it("13. PHGRegistry.inheritScore: transfers cumulativeScore to new device", async function () {
    const bio = ethers.encodeBytes32String("bio");
    await phgRegistry.connect(bridge).commitCheckpoint(DEV_OLD, 75, 15, bio);
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    expect(await phgRegistry.cumulativeScore(DEV_NEW)).to.equal(75n);
  });

  it("14. PHGRegistry.inheritScore: zeroes source cumulativeScore", async function () {
    const bio = ethers.encodeBytes32String("bio");
    await phgRegistry.connect(bridge).commitCheckpoint(DEV_OLD, 75, 15, bio);
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    expect(await phgRegistry.cumulativeScore(DEV_OLD)).to.equal(0n);
  });

  it("15. PHGRegistry.inheritScore: transfers recordCount", async function () {
    const bio = ethers.encodeBytes32String("bio");
    await phgRegistry.connect(bridge).commitCheckpoint(DEV_OLD, 50, 20, bio);
    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);
    expect(await phgRegistry.recordCount(DEV_NEW)).to.equal(20n);
    expect(await phgRegistry.recordCount(DEV_OLD)).to.equal(0n);
  });

  it("16. PHGRegistry.setIdentityRegistry: can only be set once", async function () {
    await expect(
      phgRegistry.connect(bridge).setIdentityRegistry(other.address)
    ).to.be.revertedWithCustomError(phgRegistry, "IdentityRegistryAlreadySet");
  });

  it("17. PHGRegistry.inheritScore: reverts if called by non-registry", async function () {
    await expect(
      phgRegistry.connect(bridge).inheritScore(DEV_OLD, DEV_NEW)
    ).to.be.revertedWithCustomError(phgRegistry, "NotIdentityRegistry");
  });

  it("18. End-to-end: attest -> score migrated -> TournamentGate sees new score", async function () {
    const TournamentGate = await ethers.getContractFactory("TournamentGate");
    const gate = await TournamentGate.deploy(await phgRegistry.getAddress(), 50);
    await gate.waitForDeployment();

    const bio = ethers.encodeBytes32String("bio_e2e");
    await phgRegistry.connect(bridge).commitCheckpoint(DEV_OLD, 100, 20, bio);

    await identityRegistry.connect(bridge).attestContinuity(DEV_OLD, DEV_NEW, PROOF);

    // New device passes the gate
    await expect(gate.assertEligible(DEV_NEW)).to.not.be.reverted;
    // Old device (score zeroed) fails the gate
    await expect(gate.assertEligible(DEV_OLD)).to.be.reverted;
  });
});
