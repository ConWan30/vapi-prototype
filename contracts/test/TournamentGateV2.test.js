const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const DEVICE_B = ethers.zeroPadBytes("0xbb", 32);
const BIO_HASH_1 = ethers.zeroPadBytes("0x11", 32);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function deployRegistry(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGRegistry");
  return Factory.deploy(bridgeAddress);
}

async function deployGateV2(registryAddress, minCumulative, minVelocity, velocityWindow) {
  const Factory = await ethers.getContractFactory("TournamentGateV2");
  return Factory.deploy(registryAddress, minCumulative, minVelocity, velocityWindow);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TournamentGateV2", function () {
  let registry, gate;
  let bridge, player;

  beforeEach(async function () {
    [bridge, player] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
    // minCumulative=100, minVelocity=50, velocityWindow=3
    gate = await deployGateV2(await registry.getAddress(), 100n, 50n, 3n);
  });

  // 1
  it("1. deploys with correct parameters", async function () {
    expect(await gate.phgRegistry()).to.equal(await registry.getAddress());
    expect(await gate.minCumulative()).to.equal(100n);
    expect(await gate.minVelocity()).to.equal(50n);
    expect(await gate.velocityWindow()).to.equal(3n);
  });

  // 2
  it("2. assertEligible passes when device meets both cumulative and velocity", async function () {
    // Commit 3 checkpoints: total 120, velocity in window=3 is 120
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH_1);
    await expect(gate.assertEligible(DEVICE_A)).to.not.be.reverted;
  });

  // 3
  it("3. assertEligible reverts InsufficientHumanityScore when cumulative too low", async function () {
    // Only 30 total -- below minCumulative=100
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 30n, 5, BIO_HASH_1);
    await expect(gate.assertEligible(DEVICE_A))
      .to.be.revertedWithCustomError(gate, "InsufficientHumanityScore")
      .withArgs(DEVICE_A, 30n, 100n);
  });

  // 4
  it("4. assertEligible reverts InsufficientRecentVelocity despite high cumulative", async function () {
    // High cumulative (200) but velocity window sums only 10 (window=3, only 1 recent checkpoint)
    // We need cumulative >= 100 but velocity < 50
    // Trick: Set up registry directly by committing an old checkpoint and a small recent one
    // Actually, need cumulative >= 100 with recent velocity < 50
    // Commit 1 big checkpoint (100), then nothing recent -- velocity in last 3 is only 100 (>50), won't work
    // Let's use minVelocity=80 for this test via fresh gate
    const gate2 = await deployGateV2(await registry.getAddress(), 100n, 80n, 1n);
    // cumulative=100 (passes first check), velocity window=1 last delta=10 (fails second)
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 90n, 5, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 5, BIO_HASH_1);
    // cumulativeScore = 100, velocity window=1 = 10 (< minVelocity=80)
    await expect(gate2.assertEligible(DEVICE_A))
      .to.be.revertedWithCustomError(gate2, "InsufficientRecentVelocity")
      .withArgs(DEVICE_A, 10n, 80n);
  });

  // 5
  it("5. assertEligible reverts for unknown device (both scores zero)", async function () {
    await expect(gate.assertEligible(DEVICE_B))
      .to.be.revertedWithCustomError(gate, "InsufficientHumanityScore")
      .withArgs(DEVICE_B, 0n, 100n);
  });

  // 6
  it("6. velocity window boundary: window=1 checks only most recent checkpoint", async function () {
    const gate1 = await deployGateV2(await registry.getAddress(), 100n, 30n, 1n);
    // Commit multiple checkpoints; only last one matters for velocity
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 5, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 5, BIO_HASH_1);
    // cumulative=110 (pass), velocity=10 (< minVelocity=30) (fail)
    await expect(gate1.assertEligible(DEVICE_A))
      .to.be.revertedWithCustomError(gate1, "InsufficientRecentVelocity");
  });

  // 7
  it("7. assertEligible revert shows actual velocity in error", async function () {
    const gate2 = await deployGateV2(await registry.getAddress(), 10n, 100n, 2n);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 20n, 5, BIO_HASH_1);
    // cumulative=20 >= 10, velocity=20 < 100
    await expect(gate2.assertEligible(DEVICE_A))
      .to.be.revertedWithCustomError(gate2, "InsufficientRecentVelocity")
      .withArgs(DEVICE_A, 20n, 100n);
  });

  // 8
  it("8. gate with minVelocity=0 always passes velocity check", async function () {
    const gate0 = await deployGateV2(await registry.getAddress(), 50n, 0n, 3n);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 5, BIO_HASH_1);
    await expect(gate0.assertEligible(DEVICE_A)).to.not.be.reverted;
  });

  // 9
  it("9. constructor reverts on zero registry address", async function () {
    const Factory = await ethers.getContractFactory("TournamentGateV2");
    await expect(
      Factory.deploy(ethers.ZeroAddress, 100n, 50n, 3n)
    ).to.be.revertedWith("TournamentGateV2: zero registry");
  });

  // 10
  it("10. constructor reverts on zero velocity window", async function () {
    const Factory = await ethers.getContractFactory("TournamentGateV2");
    await expect(
      Factory.deploy(await registry.getAddress(), 100n, 50n, 0n)
    ).to.be.revertedWith("TournamentGateV2: zero window");
  });
});
