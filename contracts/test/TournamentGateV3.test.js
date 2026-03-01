/**
 * TournamentGateV3 Tests — Phase 37
 *
 * 4 tests covering:
 *   9.  assertEligible() passes when all three gates pass
 *   10. assertEligible() reverts CredentialSuspended when isActive()=false
 *   11. assertEligible() reverts InsufficientHumanityScore
 *   12. assertEligible() reverts InsufficientRecentVelocity
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const BIO_HASH  = ethers.zeroPadBytes("0x11", 32);
const EVIDENCE  = ethers.zeroPadBytes("0xee", 32);

function makeNullifier(n) {
  return ethers.zeroPadValue(ethers.toBeHex(n), 32);
}
function makeCommitment(n) {
  return ethers.zeroPadValue(ethers.toBeHex(n + 100), 32);
}

// ---------------------------------------------------------------------------
// Deploy helpers
// ---------------------------------------------------------------------------

async function deployRegistry(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGRegistry");
  return Factory.deploy(bridgeAddress);
}

async function deployCredential(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGCredential");
  return Factory.deploy(bridgeAddress);
}

async function deployGateV3(registryAddress, credentialAddress,
                             minCumulative, minVelocity, velocityWindow) {
  const Factory = await ethers.getContractFactory("TournamentGateV3");
  return Factory.deploy(
    registryAddress, credentialAddress,
    minCumulative, minVelocity, velocityWindow
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TournamentGateV3 (Phase 37)", function () {
  let registry, cred, gate;
  let bridge, player;

  const MIN_CUMUL = 100n;
  const MIN_VEL   = 50n;
  const VEL_WIN   = 3n;
  const SCORE_PER_CHECKPOINT = 120n; // delta per commitCheckpoint call

  beforeEach(async function () {
    [bridge, player] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
    cred     = await deployCredential(bridge.address);
    gate     = await deployGateV3(
      await registry.getAddress(),
      await cred.getAddress(),
      MIN_CUMUL, MIN_VEL, VEL_WIN
    );
  });

  // Helper: give DEVICE_A enough PHG score + credential to pass all gates
  async function setupEligible() {
    // Score enough to exceed minCumulative and minVelocity using commitCheckpoint
    for (let i = 0; i < 3; i++) {
      await registry.connect(bridge).commitCheckpoint(
        DEVICE_A, SCORE_PER_CHECKPOINT, 10, BIO_HASH
      );
    }
    // Mint credential
    await cred.connect(bridge).mintCredential(
      DEVICE_A, makeNullifier(1), makeCommitment(1), 800n
    );
  }

  // 9
  it("9. assertEligible() passes when score, velocity, and isActive all pass", async function () {
    await setupEligible();
    // Should not revert
    await expect(gate.assertEligible(DEVICE_A)).to.not.be.reverted;
    expect(await gate.isEligible(DEVICE_A)).to.equal(true);
  });

  // 10
  it("10. assertEligible() reverts CredentialSuspended when credential is suspended", async function () {
    await setupEligible();
    // Suspend the credential
    await cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, 86400n);
    await expect(
      gate.assertEligible(DEVICE_A)
    ).to.be.revertedWithCustomError(gate, "CredentialSuspended");
    expect(await gate.isEligible(DEVICE_A)).to.equal(false);
  });

  // 11
  it("11. assertEligible() reverts InsufficientHumanityScore when score too low", async function () {
    // Mint credential but don't add PHG score → cumulative = 0 < 100
    await cred.connect(bridge).mintCredential(
      DEVICE_A, makeNullifier(2), makeCommitment(2), 800n
    );
    await expect(
      gate.assertEligible(DEVICE_A)
    ).to.be.revertedWithCustomError(gate, "InsufficientHumanityScore");
  });

  // 12
  it("12. assertEligible() reverts InsufficientRecentVelocity when velocity too low", async function () {
    // Score once (delta=120, cumulative=120 >= 100) but window=3 means need 3 checkpoints for velocity
    // With only 1 checkpoint contributing velocity=120 which is >= MIN_VEL=50
    // So we need to pick a higher minVelocity scenario — deploy a gate requiring velocity=200
    const gateHighVel = await deployGateV3(
      await registry.getAddress(),
      await cred.getAddress(),
      10n,   // low min cumulative
      200n,  // high min velocity requiring multiple checkpoints with large deltas
      3n
    );
    // Score once (cumulative=120 >= 10, but velocity depends on implementation)
    // Since getRecentVelocity sums score deltas in window, 1 checkpoint = 120 < 200
    await registry.connect(bridge).commitCheckpoint(DEVICE_A, 120n, 10, BIO_HASH);
    await cred.connect(bridge).mintCredential(
      DEVICE_A, makeNullifier(3), makeCommitment(3), 800n
    );
    await expect(
      gateHighVel.assertEligible(DEVICE_A)
    ).to.be.revertedWithCustomError(gateHighVel, "InsufficientRecentVelocity");
  });
});
