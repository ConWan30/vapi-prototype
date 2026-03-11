/**
 * PHGCredentialEnforcement Tests — Phase 37
 *
 * 10 tests covering:
 *   1. suspend() sets isSuspended=true + emits CredentialSuspended
 *   2. reinstate() sets isSuspended=false + emits CredentialReinstated
 *   3. isActive() returns false when suspended
 *   4. isActive() returns true when credential exists and not suspended
 *   5. suspend() reverts AlreadySuspended on double-suspend
 *   6. reinstate() reverts NotSuspended when not suspended
 *   7. suspend() reverts CredentialNotMinted when no credential
 *   8. Non-bridge address reverts OnlyBridge
 *   9. isActive() returns true after suspendedUntil has elapsed (auto-expiry)
 *  10. suspend() allows re-suspension after auto-expiry without reinstate()
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const DEVICE_B = ethers.zeroPadBytes("0xbb", 32);
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

async function deployCredential(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGCredential");
  return Factory.deploy(bridgeAddress);
}

async function mintDevice(cred, bridge, deviceId, nullifierN) {
  await cred.connect(bridge).mintCredential(
    deviceId,
    makeNullifier(nullifierN),
    makeCommitment(nullifierN),
    800n
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PHGCredentialEnforcement (Phase 37)", function () {
  let cred;
  let bridge, other;

  beforeEach(async function () {
    [bridge, other] = await ethers.getSigners();
    cred = await deployCredential(bridge.address);
    // Mint DEVICE_A so suspension tests have a valid credential
    await mintDevice(cred, bridge, DEVICE_A, 1);
  });

  // 1
  it("1. suspend() sets isSuspended=true and emits CredentialSuspended", async function () {
    const duration = 7 * 86400; // 7 days
    const tx = await cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, duration);
    const receipt = await tx.wait();
    expect(await cred.isSuspended(DEVICE_A)).to.equal(true);
    const event = receipt.logs.find(l => {
      try { return cred.interface.parseLog(l).name === "CredentialSuspended"; }
      catch { return false; }
    });
    expect(event).to.not.be.undefined;
    const parsed = cred.interface.parseLog(event);
    expect(parsed.args.deviceId).to.equal(DEVICE_A);
    expect(parsed.args.evidenceHash).to.equal(EVIDENCE);
  });

  // 2
  it("2. reinstate() sets isSuspended=false and emits CredentialReinstated", async function () {
    await cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, 86400n);
    const tx = await cred.connect(bridge).reinstate(DEVICE_A);
    const receipt = await tx.wait();
    expect(await cred.isSuspended(DEVICE_A)).to.equal(false);
    const event = receipt.logs.find(l => {
      try { return cred.interface.parseLog(l).name === "CredentialReinstated"; }
      catch { return false; }
    });
    expect(event).to.not.be.undefined;
  });

  // 3
  it("3. isActive() returns false when suspended", async function () {
    await cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, 86400n);
    expect(await cred.isActive(DEVICE_A)).to.equal(false);
  });

  // 4
  it("4. isActive() returns true when credential exists and not suspended", async function () {
    expect(await cred.isActive(DEVICE_A)).to.equal(true);
  });

  // 5
  it("5. suspend() reverts AlreadySuspended on double-suspend", async function () {
    await cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, 86400n);
    await expect(
      cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, 86400n)
    ).to.be.revertedWithCustomError(cred, "AlreadySuspended");
  });

  // 6
  it("6. reinstate() reverts NotSuspended when not suspended", async function () {
    await expect(
      cred.connect(bridge).reinstate(DEVICE_A)
    ).to.be.revertedWithCustomError(cred, "NotSuspended");
  });

  // 7
  it("7. suspend() reverts CredentialNotMinted when no credential", async function () {
    await expect(
      cred.connect(bridge).suspend(DEVICE_B, EVIDENCE, 86400n)
    ).to.be.revertedWithCustomError(cred, "CredentialNotMinted");
  });

  // 8
  it("8. non-bridge address reverts OnlyBridge on suspend()", async function () {
    await expect(
      cred.connect(other).suspend(DEVICE_A, EVIDENCE, 86400n)
    ).to.be.revertedWithCustomError(cred, "OnlyBridge");
  });

  // 9 — auto-expiry: isActive() must return true once suspendedUntil has elapsed
  it("9. isActive() returns true after suspendedUntil has elapsed (auto-expiry)", async function () {
    const duration = 3600; // 1 hour
    await cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, duration);
    expect(await cred.isActive(DEVICE_A)).to.equal(false); // suspended now

    // Advance EVM clock past the suspension window
    await ethers.provider.send("evm_increaseTime", [duration + 1]);
    await ethers.provider.send("evm_mine", []);

    // isSuspended flag is still true (not cleared), but isActive() auto-expires
    expect(await cred.isSuspended(DEVICE_A)).to.equal(true);
    expect(await cred.isActive(DEVICE_A)).to.equal(true);
  });

  // 10 — re-suspension: bridge can re-suspend after auto-expiry without reinstate()
  it("10. suspend() allows re-suspension after auto-expiry without calling reinstate()", async function () {
    const duration = 3600; // 1 hour
    await cred.connect(bridge).suspend(DEVICE_A, EVIDENCE, duration);

    // Advance past the first suspension window
    await ethers.provider.send("evm_increaseTime", [duration + 1]);
    await ethers.provider.send("evm_mine", []);

    // Re-suspend with a new duration — must NOT revert AlreadySuspended
    const newEvidence = ethers.zeroPadBytes("0xff", 32);
    await expect(
      cred.connect(bridge).suspend(DEVICE_A, newEvidence, 86400)
    ).to.not.be.reverted;

    expect(await cred.isSuspended(DEVICE_A)).to.equal(true);
    expect(await cred.isActive(DEVICE_A)).to.equal(false);
  });
});
