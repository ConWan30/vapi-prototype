/**
 * PITLSessionRegistry Tests — Phase 26
 *
 * 13 tests covering:
 *   - Deployment and bridge address
 *   - Access control (OnlyBridge)
 *   - Mock mode (pitlVerifier=address(0))
 *   - Event emission
 *   - State updates (latestHumanityProb, sessionCount)
 *   - Anti-replay (NullifierUsed)
 *   - Input validation (HumanityProbOutOfRange, proof length)
 *   - setPITLVerifier (once-only)
 *   - Multi-device independence
 *   - Epoch=0 boundary
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEVICE_A = ethers.zeroPadBytes("0xaa", 32);
const DEVICE_B = ethers.zeroPadBytes("0xbb", 32);

// 256-byte mock proof (all zeros)
const MOCK_PROOF_256 = "0x" + "00".repeat(256);

// Short proof (255 bytes) — should revert
const MOCK_PROOF_255 = "0x" + "00".repeat(255);

function makeNullifier(n) {
  return ethers.zeroPadValue(ethers.toBeHex(n), 32);
}

// ---------------------------------------------------------------------------
// Deploy helper
// ---------------------------------------------------------------------------

async function deployRegistry(bridgeAddress) {
  const Factory = await ethers.getContractFactory("PITLSessionRegistry");
  return Factory.deploy(bridgeAddress);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PITLSessionRegistry", function () {
  let registry;
  let bridge, other;

  beforeEach(async function () {
    [bridge, other] = await ethers.getSigners();
    registry = await deployRegistry(bridge.address);
  });

  // 1
  it("1. deploys with correct bridge address", async function () {
    expect(await registry.bridge()).to.equal(bridge.address);
    expect(await registry.pitlVerifier()).to.equal(ethers.ZeroAddress);
  });

  // 2
  it("2. submitPITLProof reverts for non-bridge (OnlyBridge)", async function () {
    await expect(
      registry.connect(other).submitPITLProof(
        DEVICE_A, MOCK_PROOF_256,
        1n, 800n,
         0n, ethers.toBigInt(makeNullifier(1)),
        1n
      )
    ).to.be.revertedWithCustomError(registry, "OnlyBridge");
  });

  // 3
  it("3. submitPITLProof succeeds in mock mode (pitlVerifier=address(0))", async function () {
    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_A, MOCK_PROOF_256,
        12345n, 700n,
         0n, ethers.toBigInt(makeNullifier(10)),
        42n
      )
    ).to.not.be.reverted;
  });

  // 4
  it("4. emits PITLSessionProofSubmitted with correct args", async function () {
    const fc   = 99999n;
    const hp   = 500n;
    const null1 = ethers.toBigInt(makeNullifier(20));
    const epoch = 100n;

    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_A, MOCK_PROOF_256, fc, hp,  0n, null1, epoch
      )
    )
      .to.emit(registry, "PITLSessionProofSubmitted")
      .withArgs(DEVICE_A, hp, fc, epoch);
  });

  // 5
  it("5. latestHumanityProb updated after submission", async function () {
    const hp = 850n;
    await registry.connect(bridge).submitPITLProof(
      DEVICE_A, MOCK_PROOF_256,
      1n, hp,
       0n, ethers.toBigInt(makeNullifier(30)),
      5n
    );
    expect(await registry.latestHumanityProb(DEVICE_A)).to.equal(hp);
  });

  // 6
  it("6. sessionCount incremented per submission", async function () {
    expect(await registry.sessionCount(DEVICE_A)).to.equal(0n);

    await registry.connect(bridge).submitPITLProof(
      DEVICE_A, MOCK_PROOF_256,
      1n, 500n,
       0n, ethers.toBigInt(makeNullifier(40)),
      1n
    );
    expect(await registry.sessionCount(DEVICE_A)).to.equal(1n);

    await registry.connect(bridge).submitPITLProof(
      DEVICE_A, MOCK_PROOF_256,
      1n, 600n,
       0n, ethers.toBigInt(makeNullifier(41)),
      2n
    );
    expect(await registry.sessionCount(DEVICE_A)).to.equal(2n);
  });

  // 7
  it("7. same nullifier on second call reverts NullifierUsed", async function () {
    const null1 = ethers.toBigInt(makeNullifier(50));
    await registry.connect(bridge).submitPITLProof(
      DEVICE_A, MOCK_PROOF_256, 1n, 500n,  0n, null1, 1n
    );
    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_A, MOCK_PROOF_256, 1n, 500n,  0n, null1, 2n
      )
    ).to.be.revertedWithCustomError(registry, "NullifierUsed");
  });

  // 8
  it("8. humanityProbInt > 1000 reverts HumanityProbOutOfRange", async function () {
    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_A, MOCK_PROOF_256,
        1n, 1001n,
         0n, ethers.toBigInt(makeNullifier(60)),
        1n
      )
    ).to.be.revertedWithCustomError(registry, "HumanityProbOutOfRange");
  });

  // 9
  it("9. proof.length != 256 reverts", async function () {
    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_A, MOCK_PROOF_255,
        1n, 500n,
         0n, ethers.toBigInt(makeNullifier(70)),
        1n
      )
    ).to.be.reverted;  // "invalid proof length" require
  });

  // 10
  it("10. setPITLVerifier sets address; second call reverts", async function () {
    const fakeVerifier = other.address;
    await registry.connect(bridge).setPITLVerifier(fakeVerifier);
    expect(await registry.pitlVerifier()).to.equal(fakeVerifier);

    await expect(
      registry.connect(bridge).setPITLVerifier(fakeVerifier)
    ).to.be.revertedWith("verifier already set");
  });

  // 11
  it("11. two devices track independent latestHumanityProb", async function () {
    await registry.connect(bridge).submitPITLProof(
      DEVICE_A, MOCK_PROOF_256, 1n, 400n,
       0n, ethers.toBigInt(makeNullifier(80)), 1n
    );
    await registry.connect(bridge).submitPITLProof(
      DEVICE_B, MOCK_PROOF_256, 1n, 900n,
       0n, ethers.toBigInt(makeNullifier(81)), 1n
    );
    expect(await registry.latestHumanityProb(DEVICE_A)).to.equal(400n);
    expect(await registry.latestHumanityProb(DEVICE_B)).to.equal(900n);
  });

  // 12
  it("12. epoch=0 is valid (boundary check)", async function () {
    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_A, MOCK_PROOF_256,
        1n, 1000n,
         0n, ethers.toBigInt(makeNullifier(90)),
        0n  // epoch=0
      )
    ).to.not.be.reverted;
  });

  // 13
  it("13. humanityProbInt=0 and humanityProbInt=1000 are both valid boundaries", async function () {
    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_A, MOCK_PROOF_256, 1n, 0n,
         0n, ethers.toBigInt(makeNullifier(100)), 1n
      )
    ).to.not.be.reverted;

    await expect(
      registry.connect(bridge).submitPITLProof(
        DEVICE_B, MOCK_PROOF_256, 1n, 1000n,
         0n, ethers.toBigInt(makeNullifier(101)), 1n
      )
    ).to.not.be.reverted;
  });
});
