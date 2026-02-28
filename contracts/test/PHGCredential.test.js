/**
 * PHGCredential Tests — Phase 28
 *
 * 12 tests covering:
 *   - Deployment (bridge address)
 *   - mintCredential state changes
 *   - Event emission
 *   - Read functions (hasCredential, getCredential)
 *   - Revert conditions (AlreadyMinted, NullifierUsed, InvalidScore, OnlyBridge)
 *   - Boundary: humanityProbInt = 1000 succeeds, 1001 reverts
 *   - ERC-5192 locked() invariant
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const DEVICE_B = ethers.zeroPadBytes("0xbb", 32);

function makeNullifier(n) {
  return ethers.zeroPadValue(ethers.toBeHex(n), 32);
}

function makeCommitment(n) {
  return ethers.zeroPadValue(ethers.toBeHex(n + 100), 32);
}

// ---------------------------------------------------------------------------
// Deploy helper
// ---------------------------------------------------------------------------

async function deployCredential(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PHGCredential");
  return Factory.deploy(bridgeAddress);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PHGCredential", function () {
  let cred;
  let bridge, other;

  beforeEach(async function () {
    [bridge, other] = await ethers.getSigners();
    cred = await deployCredential(bridge.address);
  });

  // 1
  it("1. deploys with correct bridge address", async function () {
    expect(await cred.bridge()).to.equal(bridge.address);
  });

  // 2
  it("2. mintCredential sets credentialOf[deviceId] != 0", async function () {
    await cred.connect(bridge).mintCredential(
      DEVICE_A, makeNullifier(1), makeCommitment(1), 750
    );
    expect(await cred.credentialOf(DEVICE_A)).to.be.greaterThan(0n);
  });

  // 3
  it("3. mintCredential emits CredentialMinted event with correct fields", async function () {
    const tx = await cred.connect(bridge).mintCredential(
      DEVICE_A, makeNullifier(1), makeCommitment(1), 800
    );
    const receipt = await tx.wait();
    const block = await ethers.provider.getBlock(receipt.blockNumber);

    await expect(tx)
      .to.emit(cred, "CredentialMinted")
      .withArgs(DEVICE_A, 1n, 800n, BigInt(receipt.blockNumber));
  });

  // 4
  it("4. credentials[id] stores all fields correctly", async function () {
    const null1 = makeNullifier(1);
    const comm1 = makeCommitment(1);
    await cred.connect(bridge).mintCredential(DEVICE_A, null1, comm1, 600);

    const id = await cred.credentialOf(DEVICE_A);
    const data = await cred.credentials(id);

    expect(data.nullifierHash).to.equal(null1);
    expect(data.featureCommitment).to.equal(comm1);
    expect(data.humanityProbInt).to.equal(600n);
    expect(data.mintedAt).to.be.greaterThan(0n);
  });

  // 5
  it("5. hasCredential returns false before mint, true after", async function () {
    expect(await cred.hasCredential(DEVICE_A)).to.be.false;
    await cred.connect(bridge).mintCredential(
      DEVICE_A, makeNullifier(1), makeCommitment(1), 500
    );
    expect(await cred.hasCredential(DEVICE_A)).to.be.true;
  });

  // 6
  it("6. getCredential returns correct Credential struct", async function () {
    const null1 = makeNullifier(7);
    const comm1 = makeCommitment(7);
    await cred.connect(bridge).mintCredential(DEVICE_A, null1, comm1, 900);

    const data = await cred.getCredential(DEVICE_A);
    expect(data.nullifierHash).to.equal(null1);
    expect(data.featureCommitment).to.equal(comm1);
    expect(data.humanityProbInt).to.equal(900n);
  });

  // 7
  it("7. double mint same deviceId reverts AlreadyMinted", async function () {
    await cred.connect(bridge).mintCredential(
      DEVICE_A, makeNullifier(1), makeCommitment(1), 500
    );
    await expect(
      cred.connect(bridge).mintCredential(
        DEVICE_A, makeNullifier(2), makeCommitment(2), 500
      )
    ).to.be.revertedWithCustomError(cred, "AlreadyMinted")
     .withArgs(DEVICE_A);
  });

  // 8
  it("8. duplicate nullifierHash reverts NullifierUsed", async function () {
    const sharedNull = makeNullifier(99);
    await cred.connect(bridge).mintCredential(
      DEVICE_A, sharedNull, makeCommitment(1), 500
    );
    await expect(
      cred.connect(bridge).mintCredential(
        DEVICE_B, sharedNull, makeCommitment(2), 500
      )
    ).to.be.revertedWithCustomError(cred, "NullifierUsed")
     .withArgs(sharedNull);
  });

  // 9
  it("9. humanityProbInt = 1001 reverts InvalidScore", async function () {
    await expect(
      cred.connect(bridge).mintCredential(
        DEVICE_A, makeNullifier(1), makeCommitment(1), 1001
      )
    ).to.be.revertedWithCustomError(cred, "InvalidScore")
     .withArgs(1001n);
  });

  // 10
  it("10. humanityProbInt = 1000 succeeds (boundary)", async function () {
    await expect(
      cred.connect(bridge).mintCredential(
        DEVICE_A, makeNullifier(1), makeCommitment(1), 1000
      )
    ).not.to.be.reverted;
    const data = await cred.getCredential(DEVICE_A);
    expect(data.humanityProbInt).to.equal(1000n);
  });

  // 11
  it("11. non-bridge caller reverts OnlyBridge", async function () {
    await expect(
      cred.connect(other).mintCredential(
        DEVICE_A, makeNullifier(1), makeCommitment(1), 500
      )
    ).to.be.revertedWithCustomError(cred, "OnlyBridge");
  });

  // 12
  it("12. locked(tokenId) returns true for any id (ERC-5192 soulbound invariant)", async function () {
    expect(await cred.locked(0)).to.be.true;
    expect(await cred.locked(1)).to.be.true;
    expect(await cred.locked(999999)).to.be.true;
  });
});
