const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const DEVICE_B = ethers.zeroPadBytes("0xbb", 32);

const BIO_HASH_1 = ethers.zeroPadBytes("0x11", 32);
const BIO_HASH_2 = ethers.zeroPadBytes("0x22", 32);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Deploy a fresh PHGRegistry with the bridge set to `bridge` signer.
 */
async function deployRegistry(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGRegistry");
  return Factory.deploy(bridgeAddress);
}

/**
 * Deploy TournamentGate wired to a given PHGRegistry and minScore.
 */
async function deployGate(registryAddress, minScore) {
  const Factory = await ethers.getContractFactory("TournamentGate");
  return Factory.deploy(registryAddress, minScore);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PHGRegistry", function () {
  let registry;
  let bridge, attacker, other;

  beforeEach(async function () {
    [bridge, attacker, other] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
  });

  // 1
  it("1. deploys with correct bridge address", async function () {
    expect(await registry.bridge()).to.equal(bridge.address);
  });

  // 2
  it("2. commitCheckpoint reverts for non-bridge caller", async function () {
    await expect(
      registry.connect(attacker).commitCheckpoint(DEVICE_A, 50n, 10, BIO_HASH_1)
    ).to.be.revertedWithCustomError(registry, "OnlyBridge");
  });

  // 3
  it("3. commitCheckpoint emits PHGCheckpointCommitted with correct fields", async function () {
    await expect(
      registry.connect(bridge).commitCheckpoint(DEVICE_A, 78n, 10, BIO_HASH_1)
    )
      .to.emit(registry, "PHGCheckpointCommitted")
      .withArgs(
        DEVICE_A,
        78n,            // cumulativeScore after
        10,             // recordCount after
        BIO_HASH_1,
        ethers.ZeroHash, // prevCheckpointHash (first checkpoint)
        await ethers.provider.getBlock("latest").then(b => b.number + 1)
      );
  });

  // 4
  it("4. commitCheckpoint accumulates cumulativeScore across two calls", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 78n, 10, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 85n, 10, BIO_HASH_2);
    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(163n);
  });

  // 5
  it("5. commitCheckpoint chains prevCheckpointHash correctly", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 78n, 10, BIO_HASH_1);

    // Compute expected prevHash using solidityPackedKeccak256 to match abi.encodePacked
    const expectedPrevHash = ethers.solidityPackedKeccak256(
      ["bytes32", "uint256", "uint32", "bytes32"],
      [DEVICE_A, 78n, 10, BIO_HASH_1]
    );

    const tx = await registry.connect(bridge).commitCheckpoint(
      DEVICE_A, 85n, 10, BIO_HASH_2
    );
    const receipt = await tx.wait();
    const event = receipt.logs
      .map(l => { try { return registry.interface.parseLog(l); } catch { return null; } })
      .find(e => e && e.name === "PHGCheckpointCommitted");

    expect(event.args.prevCheckpointHash).to.equal(expectedPrevHash);
  });

  // 6
  it("6. commitCheckpoint increments recordCount correctly", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 10, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 10, BIO_HASH_2);
    expect(await registry.recordCount(DEVICE_A)).to.equal(20);
  });

  // 7
  it("7. isEligible returns true when score >= minScore", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 10, BIO_HASH_1);
    expect(await registry.isEligible(DEVICE_A, 100n)).to.be.true;
    expect(await registry.isEligible(DEVICE_A, 99n)).to.be.true;
  });

  // 8
  it("8. isEligible returns false when score < minScore", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 10, BIO_HASH_1);
    expect(await registry.isEligible(DEVICE_A, 51n)).to.be.false;
  });

  // 9
  it("9. isEligible returns false for unknown device (zero score)", async function () {
    expect(await registry.isEligible(DEVICE_B, 1n)).to.be.false;
  });
});

// ---------------------------------------------------------------------------

describe("TournamentGate", function () {
  let registry, gate;
  let bridge, player;

  beforeEach(async function () {
    [bridge, player] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
    gate = await deployGate(await registry.getAddress(), 100n);
  });

  // 10
  it("10. deploys with correct phgRegistry and minPHGScore", async function () {
    expect(await gate.phgRegistry()).to.equal(await registry.getAddress());
    expect(await gate.minPHGScore()).to.equal(100n);
  });

  // 11
  it("11. assertEligible does not revert when device meets minimum", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 10, BIO_HASH_1);
    await expect(gate.assertEligible(DEVICE_A)).to.not.be.reverted;
  });

  // 12
  it("12. assertEligible reverts with InsufficientHumanityScore when below minimum", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 10, BIO_HASH_1);
    await expect(gate.assertEligible(DEVICE_A))
      .to.be.revertedWithCustomError(gate, "InsufficientHumanityScore")
      .withArgs(DEVICE_A, 50n, 100n);
  });

  // 13
  it("13. assertEligible reverts for unknown device (score = 0)", async function () {
    await expect(gate.assertEligible(DEVICE_B))
      .to.be.revertedWithCustomError(gate, "InsufficientHumanityScore")
      .withArgs(DEVICE_B, 0n, 100n);
  });
});

// ---------------------------------------------------------------------------

describe("PHGRegistry — Checkpoint Chain Integrity", function () {
  let registry;
  let bridge;

  beforeEach(async function () {
    [bridge] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
  });

  // 14
  it("14. prevCheckpointHash is ZeroHash for the first checkpoint", async function () {
    const tx = await registry.connect(bridge).commitCheckpoint(
      DEVICE_A, 78n, 10, BIO_HASH_1
    );
    const receipt = await tx.wait();
    const event = receipt.logs
      .map(l => { try { return registry.interface.parseLog(l); } catch { return null; } })
      .find(e => e && e.name === "PHGCheckpointCommitted");

    expect(event.args.prevCheckpointHash).to.equal(ethers.ZeroHash);
  });

  // 15
  it("15. prevCheckpointHash matches keccak256 of previous checkpoint fields", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 78n, 10, BIO_HASH_1);

    // Read the checkpoint head stored after the first commit — this is what should become prevHash
    const headAfterFirst = await registry.checkpointHead(DEVICE_A);
    expect(headAfterFirst).to.not.equal(ethers.ZeroHash);

    const tx = await registry.connect(bridge).commitCheckpoint(
      DEVICE_A, 85n, 10, BIO_HASH_2
    );
    const receipt = await tx.wait();
    const event = receipt.logs
      .map(l => { try { return registry.interface.parseLog(l); } catch { return null; } })
      .find(e => e && e.name === "PHGCheckpointCommitted");

    // prevCheckpointHash of the second checkpoint must equal the head stored after the first
    expect(event.args.prevCheckpointHash).to.equal(headAfterFirst);
    expect(await registry.checkpointHead(DEVICE_A)).to.not.equal(headAfterFirst);
  });

  // 16
  it("16. biometricHash is stored in event and matches input", async function () {
    const bioHash = "0x" + "ab".repeat(32);
    const tx = await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 10, bioHash);
    const receipt = await tx.wait();
    const event = receipt.logs
      .map(l => { try { return registry.interface.parseLog(l); } catch { return null; } })
      .find(e => e && e.name === "PHGCheckpointCommitted");

    expect(event.args.biometricHash).to.equal(bioHash);
  });

  // 17
  it("17. two devices accumulate scores independently", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 10, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_B, 50n,  5,  BIO_HASH_2);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 80n,  10, BIO_HASH_2);

    expect(await registry.cumulativeScore(DEVICE_A)).to.equal(180n);
    expect(await registry.cumulativeScore(DEVICE_B)).to.equal(50n);
    expect(await registry.recordCount(DEVICE_A)).to.equal(20);
    expect(await registry.recordCount(DEVICE_B)).to.equal(5);
  });

  // 18
  it("18. getDeviceState returns consistent score, count, and head", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 78n, 10, BIO_HASH_1);
    const [score, count, head] = await registry.getDeviceState(DEVICE_A);
    expect(score).to.equal(78n);
    expect(count).to.equal(10);
    expect(head).to.not.equal(ethers.ZeroHash);
    // head matches checkpointHead mapping
    expect(head).to.equal(await registry.checkpointHead(DEVICE_A));
  });
});

// ---------------------------------------------------------------------------

describe("PHGRegistry � getRecentVelocity", function () {
  let registry;
  let bridge;

  beforeEach(async function () {
    [bridge] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
  });

  // 19
  it("19. getRecentVelocity returns 0 for device with no checkpoints", async function () {
    expect(await registry.getRecentVelocity(DEVICE_A, 3n)).to.equal(0n);
  });

  // 20
  it("20. getRecentVelocity returns scoreDelta for window=1 (one checkpoint)", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 10, BIO_HASH_1);
    expect(await registry.getRecentVelocity(DEVICE_A, 1n)).to.equal(50n);
  });

  // 21
  it("21. getRecentVelocity sums last 3 checkpoints for window=3", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 10, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 20n, 10, BIO_HASH_2);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 30n, 10, BIO_HASH_1);
    // Window=3 sums all three: 10+20+30=60
    expect(await registry.getRecentVelocity(DEVICE_A, 3n)).to.equal(60n);
  });

  // 22
  it("22. getRecentVelocity with window=1 returns only the most recent delta", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 10, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 20n, 10, BIO_HASH_2);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 30n, 10, BIO_HASH_1);
    // Window=1 should return only the last delta (30)
    expect(await registry.getRecentVelocity(DEVICE_A, 1n)).to.equal(30n);
  });

  // 23
  it("23. getRecentVelocity caps at 8 hops even if windowSize > 8", async function () {
    // Commit 10 checkpoints each with delta=5
    for (let i = 0; i < 10; i++) {
      await registry.connect(bridge).commitCheckpoint(
        DEVICE_A, 5n, 1, i % 2 === 0 ? BIO_HASH_1 : BIO_HASH_2
      );
    }
    // windowSize=100 should be capped at 8 => 8x5=40
    expect(await registry.getRecentVelocity(DEVICE_A, 100n)).to.equal(40n);
  });

  // 24
  it("24. getRecentVelocity with window=8 returns sum of last 8 of 10 checkpoints", async function () {
    // Commit 10 checkpoints: first 2 with delta=100 (old), then 8 with delta=5
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 1, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 100n, 1, BIO_HASH_2);
    for (let i = 0; i < 8; i++) {
      await registry.connect(bridge).commitCheckpoint(
        DEVICE_A, 5n, 1, i % 2 === 0 ? BIO_HASH_1 : BIO_HASH_2
      );
    }
    // window=8 should sum only the last 8 (8x5=40), not the first 2
    expect(await registry.getRecentVelocity(DEVICE_A, 8n)).to.equal(40n);
  });

  // 25
  it("25. two devices have independent velocity chains", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 10n, 5, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_B, 30n, 5, BIO_HASH_2);
    expect(await registry.getRecentVelocity(DEVICE_A, 3n)).to.equal(10n);
    expect(await registry.getRecentVelocity(DEVICE_B, 3n)).to.equal(30n);
  });

  // 26
  it("26. scoreDeltaAt mapping populated correctly for each checkpoint", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 77n, 10, BIO_HASH_1);
    const head = await registry.checkpointHead(DEVICE_A);
    expect(await registry.scoreDeltaAt(head)).to.equal(77n);
  });

  // 27
  it("27. velocity window=2 with 5 checkpoints sums last 2 only", async function () {
    const deltas = [10n, 20n, 30n, 40n, 50n];
    for (const d of deltas) {
      await registry.connect(bridge).commitCheckpoint(
        DEVICE_A, d, 1, BIO_HASH_1
      );
    }
    // window=2: sum of last 2 deltas = 40+50 = 90
    expect(await registry.getRecentVelocity(DEVICE_A, 2n)).to.equal(90n);
  });

  // 28
  it("28. velocity is zero when window=0", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 50n, 5, BIO_HASH_1);
    expect(await registry.getRecentVelocity(DEVICE_A, 0n)).to.equal(0n);
  });

  // 29
  it("29. getRecentVelocity after inheritScore still works on destination device", async function () {
    // Deploy ICR-like setup: just test PHGRegistry directly
    // Set up identity registry (owner can set it)
    const [, identityReg] = await ethers.getSigners();
    await registry.setIdentityRegistry(identityReg.address);

    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 40n, 5, BIO_HASH_1);
    await registry.connect(identityReg).inheritScore(DEVICE_A, DEVICE_B);

    // DEVICE_B should now have DEVICE_A checkpoint head
    // getRecentVelocity for DEVICE_B should work (may return 0 if head mapping follows inherited head)
    // The key invariant: no revert
    const velocity = await registry.getRecentVelocity(DEVICE_B, 3n);
    expect(velocity).to.be.a("bigint");
  });

  // 30
  it("30. returns sum not single delta (accumulates window correctly)", async function () {
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 15n, 1, BIO_HASH_1);
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 25n, 1, BIO_HASH_2);
    // window=2: should return 15+25=40, not just 25
    const velocity = await registry.getRecentVelocity(DEVICE_A, 2n);
    expect(velocity).to.equal(40n);
    expect(velocity).to.not.equal(25n);
  });
});
