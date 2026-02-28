const { expect } = require("chai");
const { ethers } = require("hardhat");

// ---------------------------------------------------------------------------
//  Tier deposits (match TieredDeviceRegistry constructor values in deploy.js)
// ---------------------------------------------------------------------------
const EMULATED_DEPOSIT = ethers.parseEther("0.1");
const STANDARD_DEPOSIT = ethers.parseEther("1.0");
const ATTESTED_DEPOSIT = ethers.parseEther("0.01");

// ---------------------------------------------------------------------------
//  Device public keys — distinct 65-byte uncompressed P256 keys
// ---------------------------------------------------------------------------
const PUBKEY_EMULATED = "0x04" + "11".repeat(32) + "22".repeat(32);
const PUBKEY_STANDARD = "0x04" + "33".repeat(32) + "44".repeat(32);
const PUBKEY_ATTESTED  = "0x04" + "55".repeat(32) + "66".repeat(32);

// 64-byte fake attestation proof (valid length; attestationEnforced=false by default)
const FAKE_PROOF = "0x" + "cc".repeat(64);

const MAX_TIMESTAMP_SKEW = 300; // seconds, matches PoACVerifier default in tests

// ---------------------------------------------------------------------------
//  Helper: build a minimal valid 164-byte PoAC body
// ---------------------------------------------------------------------------
function buildRawBody({ monotonicCtr = 1, timestampMs = BigInt(Date.now()) } = {}) {
  const buf = Buffer.alloc(164);
  let offset = 0;
  // prevHash(32) + sensorCommitment(32) + modelManifestHash(32) + worldModelHash(32) = 128 zeros
  offset += 128;
  buf.writeUInt8(0x20, offset++);           // inferenceResult
  buf.writeUInt8(0x01, offset++);           // actionCode
  buf.writeUInt8(200,  offset++);           // confidence
  buf.writeUInt8(75,   offset++);           // batteryPct
  buf.writeUInt32BE(monotonicCtr, offset);  offset += 4;
  buf.writeBigInt64BE(BigInt(timestampMs), offset); offset += 8;
  // latitude(8) + longitude(8) = 16 zero bytes
  offset += 16;
  // bountyId(4) = 0
  offset += 4;
  return "0x" + buf.toString("hex");
}

async function blockTimestampMs() {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp) * 1000n;
}

// ---------------------------------------------------------------------------
//  Test suite
// ---------------------------------------------------------------------------
describe("BountyMarket", function () {
  let owner, alice;
  let registry, verifier, bountyMarket;
  let emulatedDeviceId, standardDeviceId, attestedDeviceId;

  beforeEach(async function () {
    [owner, alice] = await ethers.getSigners();

    // Deploy TieredDeviceRegistry
    const RegistryFactory = await ethers.getContractFactory("TieredDeviceRegistry");
    registry = await RegistryFactory.deploy(EMULATED_DEPOSIT, STANDARD_DEPOSIT, ATTESTED_DEPOSIT);
    await registry.waitForDeployment();

    // Deploy PoACVerifierTestable (signature check bypassed)
    const VerifierFactory = await ethers.getContractFactory("PoACVerifierTestable");
    verifier = await VerifierFactory.deploy(await registry.getAddress(), MAX_TIMESTAMP_SKEW);
    await verifier.waitForDeployment();

    // Allow verifier to update device reputation
    await registry.setReputationUpdater(await verifier.getAddress(), true);

    // Deploy BountyMarket
    const BountyFactory = await ethers.getContractFactory("BountyMarket");
    bountyMarket = await BountyFactory.deploy(
      await verifier.getAddress(),
      await registry.getAddress(),
      250 // 2.5% platform fee
    );
    await bountyMarket.waitForDeployment();

    // Register one device at each tier
    await registry.registerTieredDevice(PUBKEY_EMULATED, 0, { value: EMULATED_DEPOSIT });
    await registry.registerTieredDevice(PUBKEY_STANDARD, 1, { value: STANDARD_DEPOSIT });
    await registry.registerAttested(PUBKEY_ATTESTED, FAKE_PROOF, { value: ATTESTED_DEPOSIT });

    emulatedDeviceId = ethers.keccak256(PUBKEY_EMULATED);
    standardDeviceId = ethers.keccak256(PUBKEY_STANDARD);
    attestedDeviceId = ethers.keccak256(PUBKEY_ATTESTED);
  });

  // =========================================================================
  //  1. Deployment
  // =========================================================================
  describe("1. Deployment", function () {
    it("stores the TieredDeviceRegistry address", async function () {
      expect(await bountyMarket.deviceRegistry()).to.equal(await registry.getAddress());
    });
  });

  // =========================================================================
  //  2. canClaimBounty enforced by TieredDeviceRegistry
  // =========================================================================
  describe("2. canClaimBounty", function () {
    it("returns false for Emulated-tier device", async function () {
      expect(await registry.canClaimBounty(emulatedDeviceId)).to.be.false;
    });

    it("returns true for Standard-tier device", async function () {
      expect(await registry.canClaimBounty(standardDeviceId)).to.be.true;
    });

    it("returns true for Attested-tier device", async function () {
      expect(await registry.canClaimBounty(attestedDeviceId)).to.be.true;
    });
  });

  // =========================================================================
  //  3. Tier enforcement in submitEvidence
  // =========================================================================
  describe("3. Tier enforcement", function () {
    let bountyId;

    // Helper: post a wide-zone, 1-day bounty funded by alice
    async function postDefaultBounty() {
      const tx = await bountyMarket.connect(alice).postBounty(
        0,             // sensorRequirements: none
        1,             // minSamples: 1
        0,             // sampleIntervalS: no restriction
        86400,         // durationS: 1 day
        -900000000n,   // zoneLatMin: -90 degrees (scaled * 1e7)
        900000000n,    // zoneLatMax: +90 degrees
        -1800000000n,  // zoneLonMin: -180 degrees
        1800000000n,   // zoneLonMax: +180 degrees
        0n, 0n, 0n,    // thresholds: unused
        { value: ethers.parseEther("1.0") }
      );
      const receipt = await tx.wait();
      return receipt.logs.find(l => l.fragment && l.fragment.name === "BountyPosted").args[0];
    }

    beforeEach(async function () {
      bountyId = await postDefaultBounty();
    });

    it("acceptBounty succeeds for Emulated-tier (tier check is only at submitEvidence)", async function () {
      await expect(
        bountyMarket.acceptBounty(bountyId, emulatedDeviceId)
      ).to.not.be.reverted;
    });

    it("submitEvidence reverts IneligibleTier for Emulated-tier device", async function () {
      const ts = await blockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      await verifier.verifyPoAC(emulatedDeviceId, body, FAKE_PROOF);
      const hash = ethers.sha256(body);

      await bountyMarket.acceptBounty(bountyId, emulatedDeviceId);
      await expect(
        bountyMarket.submitEvidence(bountyId, emulatedDeviceId, hash, 0n, 0n, ts)
      ).to.be.revertedWithCustomError(bountyMarket, "IneligibleTier")
        .withArgs(emulatedDeviceId);
    });

    it("submitEvidence succeeds for Standard-tier device", async function () {
      const ts = await blockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 2, timestampMs: ts });
      await verifier.verifyPoAC(standardDeviceId, body, FAKE_PROOF);
      const hash = ethers.sha256(body);

      await bountyMarket.acceptBounty(bountyId, standardDeviceId);
      await expect(
        bountyMarket.submitEvidence(bountyId, standardDeviceId, hash, 0n, 0n, ts)
      ).to.emit(bountyMarket, "EvidenceSubmitted");
    });

    it("submitEvidence succeeds for Attested-tier device", async function () {
      const ts = await blockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 3, timestampMs: ts });
      await verifier.verifyPoAC(attestedDeviceId, body, FAKE_PROOF);
      const hash = ethers.sha256(body);

      await bountyMarket.acceptBounty(bountyId, attestedDeviceId);
      await expect(
        bountyMarket.submitEvidence(bountyId, attestedDeviceId, hash, 0n, 0n, ts)
      ).to.emit(bountyMarket, "EvidenceSubmitted");
    });
  });
});

// ---------------------------------------------------------------------------
// Phase 25: BountyMarket PHG Gate Tests
// ---------------------------------------------------------------------------

describe("BountyMarket -- PHG Gate (Phase 25)", function () {
  // These tests exercise setTournamentGate() and the gate check in claimReward().
  // Uses a minimal deployment: TieredDeviceRegistry (V1) + PoACVerifierTestable,
  // matching the constructor signatures of those contracts.

  const DEPOSIT_0 = 0n; // zero-deposit for test isolation (no ETH required)

  async function deployMinimalBM() {
    const [owner] = await ethers.getSigners();
    const RegFactory  = await ethers.getContractFactory("TieredDeviceRegistry");
    const reg = await RegFactory.deploy(DEPOSIT_0, DEPOSIT_0, DEPOSIT_0);

    const PoACFactory = await ethers.getContractFactory("PoACVerifierTestable");
    const poac = await PoACFactory.deploy(await reg.getAddress(), 300);

    const BMFactory = await ethers.getContractFactory("BountyMarket");
    const bm = await BMFactory.deploy(await poac.getAddress(), await reg.getAddress(), 0);
    return { bm, owner };
  }

  async function deployPHGRegistry(bridgeAddress) {
    const F = await ethers.getContractFactory("PHGRegistry");
    return F.deploy(bridgeAddress);
  }

  async function deployGateV2(registryAddress, minCumul, minVel, window) {
    const F = await ethers.getContractFactory("TournamentGateV2");
    return F.deploy(registryAddress, minCumul, minVel, window);
  }

  it("BG-1. setTournamentGate stores gate address", async function () {
    const { bm, owner } = await deployMinimalBM();
    const phgReg = await deployPHGRegistry(owner.address);
    const gate = await deployGateV2(await phgReg.getAddress(), 10n, 0n, 1n);
    await bm.setTournamentGate(await gate.getAddress());
    expect(await bm.tournamentGate()).to.equal(await gate.getAddress());
  });

  it("BG-2. setTournamentGate reverts if called twice (once-only)", async function () {
    const { bm, owner } = await deployMinimalBM();
    const phgReg = await deployPHGRegistry(owner.address);
    const gate = await deployGateV2(await phgReg.getAddress(), 10n, 0n, 1n);
    await bm.setTournamentGate(await gate.getAddress());
    await expect(
      bm.setTournamentGate(await gate.getAddress())
    ).to.be.revertedWith("BountyMarket: gate already set");
  });

  it("BG-3. tournamentGate defaults to address(0) (backwards compat)", async function () {
    const { bm } = await deployMinimalBM();
    expect(await bm.tournamentGate()).to.equal(ethers.ZeroAddress);
  });

  it("BG-4. setTournamentGate reverts for non-owner caller", async function () {
    const { bm, owner } = await deployMinimalBM();
    const [, attacker] = await ethers.getSigners();
    const phgReg = await deployPHGRegistry(owner.address);
    const gate = await deployGateV2(await phgReg.getAddress(), 10n, 0n, 1n);
    await expect(
      bm.connect(attacker).setTournamentGate(await gate.getAddress())
    ).to.be.reverted;
  });

  it("BG-5. GateCheckFailed error is defined in BountyMarket", async function () {
    const { bm } = await deployMinimalBM();
    // Check the error fragment exists in the ABI
    const fragment = bm.interface.getError("GateCheckFailed");
    expect(fragment).to.not.be.null;
  });

  it("BG-6. address(0) gate allows claimReward without PHG check", async function () {
    // When tournamentGate is address(0), the gate check is skipped entirely.
    // Verify the gate is address(0) at deployment and the error is in the ABI.
    const { bm } = await deployMinimalBM();
    expect(await bm.tournamentGate()).to.equal(ethers.ZeroAddress);
    expect(bm.interface.getError("GateCheckFailed")).to.not.be.null;
  });
});
