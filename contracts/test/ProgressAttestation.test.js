const { expect } = require("chai");
const { ethers, network } = require("hardhat");

// ---------------------------------------------------------------------------
//  Mock P256 precompile
// ---------------------------------------------------------------------------
const MOCK_P256_BYTECODE = "0x600160005260206000f3";
const P256_PRECOMPILE_ADDR = "0x0000000000000000000000000000000000000100";

const DEVICE_PUBKEY = "0x04" + "aa".repeat(32) + "bb".repeat(32);
const FAKE_SIG = "0x" + "cc".repeat(64);
const MIN_DEPOSIT = ethers.parseEther("0.01");
const MAX_TIMESTAMP_SKEW = 300;

// ---------------------------------------------------------------------------
//  Helper: Build a raw 164-byte PoAC body
// ---------------------------------------------------------------------------
function buildRawBody({
  prevHash = ethers.ZeroHash,
  sensorCommitment = ethers.ZeroHash,
  modelManifestHash = ethers.ZeroHash,
  worldModelHash = ethers.ZeroHash,
  inferenceResult = 0x20,
  actionCode = 0x01,
  confidence = 200,
  batteryPct = 75,
  monotonicCtr = 1,
  timestampMs = BigInt(Date.now()),
  latitude = 0.0,
  longitude = 0.0,
  bountyId = 0,
} = {}) {
  const buf = Buffer.alloc(164);
  let offset = 0;
  Buffer.from(prevHash.slice(2), "hex").copy(buf, offset); offset += 32;
  Buffer.from(sensorCommitment.slice(2), "hex").copy(buf, offset); offset += 32;
  Buffer.from(modelManifestHash.slice(2), "hex").copy(buf, offset); offset += 32;
  Buffer.from(worldModelHash.slice(2), "hex").copy(buf, offset); offset += 32;
  buf.writeUInt8(inferenceResult, offset++);
  buf.writeUInt8(actionCode, offset++);
  buf.writeUInt8(confidence, offset++);
  buf.writeUInt8(batteryPct, offset++);
  buf.writeUInt32BE(monotonicCtr, offset); offset += 4;
  buf.writeBigInt64BE(BigInt(timestampMs), offset); offset += 8;
  buf.writeDoubleBE(latitude, offset); offset += 8;
  buf.writeDoubleBE(longitude, offset); offset += 8;
  buf.writeUInt32BE(bountyId, offset); offset += 4;
  return "0x" + buf.toString("hex");
}

async function currentBlockTimestampMs() {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp) * 1000n;
}

function computeRecordHash(rawBody) {
  return ethers.sha256(rawBody);
}

// ---------------------------------------------------------------------------
//  Test Suite
// ---------------------------------------------------------------------------
describe("ProgressAttestation", function () {
  let owner, alice;
  let registry, verifier, attestation;
  let deviceId;

  beforeEach(async function () {
    [owner, alice] = await ethers.getSigners();

    await network.provider.send("hardhat_setCode", [
      P256_PRECOMPILE_ADDR,
      MOCK_P256_BYTECODE,
    ]);

    const RegistryFactory = await ethers.getContractFactory("DeviceRegistry");
    registry = await RegistryFactory.deploy(MIN_DEPOSIT);
    await registry.waitForDeployment();

    const VerifierFactory = await ethers.getContractFactory("PoACVerifierTestable");
    verifier = await VerifierFactory.deploy(
      await registry.getAddress(),
      MAX_TIMESTAMP_SKEW
    );
    await verifier.waitForDeployment();

    await registry.setReputationUpdater(await verifier.getAddress(), true);

    const AttestFactory = await ethers.getContractFactory("ProgressAttestation");
    attestation = await AttestFactory.deploy(await verifier.getAddress());
    await attestation.waitForDeployment();

    deviceId = ethers.keccak256(DEVICE_PUBKEY);
  });

  async function registerDevice() {
    await registry.registerDevice(DEVICE_PUBKEY, { value: MIN_DEPOSIT });
  }

  // Submit a PoAC record and return its hash
  async function submitRecord(overrides = {}) {
    const ts = await currentBlockTimestampMs();
    const body = buildRawBody({ timestampMs: ts, ...overrides });
    await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
    return computeRecordHash(body);
  }

  // Submit two linked records and return both hashes
  async function submitTwoRecords() {
    const ts = await currentBlockTimestampMs();
    const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts, confidence: 150 });
    await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
    const hash1 = computeRecordHash(body1);

    const body2 = buildRawBody({
      monotonicCtr: 2,
      timestampMs: ts,
      prevHash: hash1,
      confidence: 200,
    });
    await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
    const hash2 = computeRecordHash(body2);

    return { baselineHash: hash1, currentHash: hash2 };
  }

  // =========================================================================
  //  1. Deployment
  // =========================================================================
  describe("Deployment", function () {
    it("should deploy with correct PoACVerifier reference", async function () {
      expect(await attestation.poACVerifier()).to.equal(
        await verifier.getAddress()
      );
    });

    it("should set deployer as owner", async function () {
      expect(await attestation.owner()).to.equal(owner.address);
    });

    it("should start with zero attestations", async function () {
      expect(await attestation.attestationCount()).to.equal(0);
    });
  });

  // =========================================================================
  //  2. Reject same record for baseline and current
  // =========================================================================
  describe("SameRecord rejection", function () {
    it("should revert when baseline == current", async function () {
      await registerDevice();
      const hash = await submitRecord({ monotonicCtr: 1 });

      await expect(
        attestation.attestProgress(deviceId, hash, hash, 0, 500)
      ).to.be.revertedWithCustomError(attestation, "SameRecord");
    });
  });

  // =========================================================================
  //  3. Reject zero improvement
  // =========================================================================
  describe("ZeroImprovement rejection", function () {
    it("should revert when improvementBps is 0", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 0)
      ).to.be.revertedWithCustomError(attestation, "ZeroImprovement");
    });
  });

  // =========================================================================
  //  4. Reject unverified baseline
  // =========================================================================
  describe("Unverified baseline rejection", function () {
    it("should revert with BaselineNotVerified for unknown baseline hash", async function () {
      await registerDevice();
      const currentHash = await submitRecord({ monotonicCtr: 1 });
      const fakeBaseline = ethers.keccak256("0xdeadbeef");

      await expect(
        attestation.attestProgress(deviceId, fakeBaseline, currentHash, 0, 500)
      ).to.be.revertedWithCustomError(attestation, "BaselineNotVerified");
    });
  });

  // =========================================================================
  //  5. Reject unverified current
  // =========================================================================
  describe("Unverified current rejection", function () {
    it("should revert with CurrentNotVerified for unknown current hash", async function () {
      await registerDevice();
      const baselineHash = await submitRecord({ monotonicCtr: 1 });
      const fakeCurrent = ethers.keccak256("0xbeefdead");

      await expect(
        attestation.attestProgress(deviceId, baselineHash, fakeCurrent, 0, 500)
      ).to.be.revertedWithCustomError(attestation, "CurrentNotVerified");
    });
  });

  // =========================================================================
  //  6. Reject duplicate attestation pair
  // =========================================================================
  describe("Duplicate pair rejection", function () {
    it("should revert with PairAlreadyAttested on second attempt", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      // First attestation succeeds
      await attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500);

      // Second with same pair should fail
      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500)
      ).to.be.revertedWithCustomError(attestation, "PairAlreadyAttested");
    });
  });

  // =========================================================================
  //  7. Successful attestation
  // =========================================================================
  describe("Successful attestation", function () {
    it("should store attestation with correct fields", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      const tx = await attestation.attestProgress(
        deviceId, baselineHash, currentHash, 0, 1500 // REACTION_TIME, 15% improvement
      );
      await tx.wait();

      const record = await attestation.getAttestation(0);
      expect(record.deviceId).to.equal(deviceId);
      expect(record.baselineRecordHash).to.equal(baselineHash);
      expect(record.currentRecordHash).to.equal(currentHash);
      expect(record.metricType).to.equal(0); // REACTION_TIME
      expect(record.improvementBps).to.equal(1500);
      expect(record.attestor).to.equal(owner.address);
      expect(record.attestedAt).to.be.gt(0);
    });

    it("should increment attestation count", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      await attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500);
      expect(await attestation.attestationCount()).to.equal(1);
    });

    it("should allow any caller as attestor", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      await attestation.connect(alice).attestProgress(
        deviceId, baselineHash, currentHash, 1, 800 // ACCURACY
      );

      const record = await attestation.getAttestation(0);
      expect(record.attestor).to.equal(alice.address);
    });
  });

  // =========================================================================
  //  8. All metric types
  // =========================================================================
  describe("Metric types", function () {
    it("should accept all four metric types", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      // Submit 5 records (need pairs for 4 attestations)
      const hashes = [];
      let prevHash = ethers.ZeroHash;
      for (let i = 1; i <= 5; i++) {
        const body = buildRawBody({
          monotonicCtr: i,
          timestampMs: ts,
          prevHash,
          sensorCommitment: "0x" + i.toString(16).padStart(64, "0"),
        });
        await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
        const hash = computeRecordHash(body);
        hashes.push(hash);
        prevHash = hash;
      }

      // REACTION_TIME (0)
      await attestation.attestProgress(deviceId, hashes[0], hashes[1], 0, 500);
      // ACCURACY (1)
      await attestation.attestProgress(deviceId, hashes[1], hashes[2], 1, 300);
      // CONSISTENCY (2)
      await attestation.attestProgress(deviceId, hashes[2], hashes[3], 2, 200);
      // COMBO_EXECUTION (3)
      await attestation.attestProgress(deviceId, hashes[3], hashes[4], 3, 100);

      expect(await attestation.attestationCount()).to.equal(4);
    });
  });

  // =========================================================================
  //  9. Events
  // =========================================================================
  describe("Events", function () {
    it("should emit ProgressAttested with correct values", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      const tx = await attestation.attestProgress(
        deviceId, baselineHash, currentHash, 2, 750 // CONSISTENCY
      );

      await expect(tx)
        .to.emit(attestation, "ProgressAttested")
        .withArgs(
          deviceId,
          0,    // attestationId
          2,    // CONSISTENCY
          750,  // improvementBps
          owner.address
        );
    });
  });

  // =========================================================================
  //  10. View functions
  // =========================================================================
  describe("View functions", function () {
    it("getDeviceAttestationCount should return correct count", async function () {
      await registerDevice();
      expect(await attestation.getDeviceAttestationCount(deviceId)).to.equal(0);

      const { baselineHash, currentHash } = await submitTwoRecords();
      await attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500);

      expect(await attestation.getDeviceAttestationCount(deviceId)).to.equal(1);
    });

    it("getDeviceAttestationIds should return correct IDs", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      await attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500);

      const ids = await attestation.getDeviceAttestationIds(deviceId);
      expect(ids.length).to.equal(1);
      expect(ids[0]).to.equal(0);
    });

    it("getProgressHistory should return all records for a device", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      // Submit 3 records
      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts, confidence: 100 });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const h1 = computeRecordHash(body1);

      const body2 = buildRawBody({ monotonicCtr: 2, timestampMs: ts, prevHash: h1, confidence: 150 });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      const h2 = computeRecordHash(body2);

      const body3 = buildRawBody({ monotonicCtr: 3, timestampMs: ts, prevHash: h2, confidence: 200 });
      await verifier.verifyPoAC(deviceId, body3, FAKE_SIG);
      const h3 = computeRecordHash(body3);

      await attestation.attestProgress(deviceId, h1, h2, 0, 500);
      await attestation.attestProgress(deviceId, h2, h3, 1, 300);

      const history = await attestation.getProgressHistory(deviceId);
      expect(history.length).to.equal(2);
      expect(history[0].metricType).to.equal(0); // REACTION_TIME
      expect(history[1].metricType).to.equal(1); // ACCURACY
    });
  });

  // =========================================================================
  //  11. Reverse pair allowed (different direction)
  // =========================================================================
  describe("Reverse pair", function () {
    it("should allow reverse pair (different pairKey)", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();

      // Forward: baseline -> current
      await attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500);

      // Reverse: current -> baseline (different pairKey)
      await expect(
        attestation.attestProgress(deviceId, currentHash, baselineHash, 0, 500)
      ).to.not.be.reverted;
    });
  });

  // =========================================================================
  //  12. Schema Version Validation
  // =========================================================================
  describe("Schema Version Validation", function () {
    async function submitRecordWithSchema(schemaVersion, overrides = {}) {
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({ timestampMs: ts, ...overrides });
      await verifier.verifyPoACWithSchema(deviceId, body, FAKE_SIG, schemaVersion);
      return computeRecordHash(body);
    }

    it("attestProgress passes when both records use schema v2 (kinematic)", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const baselineHash = await submitRecordWithSchema(2, {
        monotonicCtr: 1, timestampMs: ts,
        sensorCommitment: "0x" + "11".repeat(32),
      });
      const currentHash = await submitRecordWithSchema(2, {
        monotonicCtr: 2, timestampMs: ts,
        sensorCommitment: "0x" + "22".repeat(32),
      });
      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500)
      ).to.not.be.reverted;
    });

    it("attestProgress passes when both records use schema v1 (environmental)", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const baselineHash = await submitRecordWithSchema(1, {
        monotonicCtr: 1, timestampMs: ts,
        sensorCommitment: "0x" + "33".repeat(32),
      });
      const currentHash = await submitRecordWithSchema(1, {
        monotonicCtr: 2, timestampMs: ts,
        sensorCommitment: "0x" + "44".repeat(32),
      });
      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500)
      ).to.not.be.reverted;
    });

    it("attestProgress reverts IncompatibleSchema when baseline=v1 current=v2", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const baselineHash = await submitRecordWithSchema(1, {
        monotonicCtr: 1, timestampMs: ts,
        sensorCommitment: "0x" + "55".repeat(32),
      });
      const currentHash = await submitRecordWithSchema(2, {
        monotonicCtr: 2, timestampMs: ts,
        sensorCommitment: "0x" + "66".repeat(32),
      });
      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500)
      ).to.be.revertedWithCustomError(attestation, "IncompatibleSchema");
    });

    it("attestProgress passes when neither record has schema set (legacy backward compat)", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();
      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500)
      ).to.not.be.reverted;
    });

    it("attestProgress passes when only one record has schema set (partial legacy compat)", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body1 = buildRawBody({
        monotonicCtr: 1, timestampMs: ts,
        sensorCommitment: "0x" + "77".repeat(32),
      });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const baselineHash = computeRecordHash(body1);

      const body2 = buildRawBody({
        monotonicCtr: 2, timestampMs: ts,
        sensorCommitment: "0x" + "88".repeat(32),
      });
      await verifier.verifyPoACWithSchema(deviceId, body2, FAKE_SIG, 2);
      const currentHash = computeRecordHash(body2);

      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 0, 500)
      ).to.not.be.reverted;
    });
  });

  // =========================================================================
  //  13. World Model Evolution Metric (Phase 13)
  // =========================================================================
  describe("13. World Model Evolution Metric", function () {
    it("attestProgress accepts WORLD_MODEL_EVOLUTION metric type (4)", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();
      // MetricType 4 = WORLD_MODEL_EVOLUTION
      await expect(
        attestation.attestProgress(deviceId, baselineHash, currentHash, 4, 2500)
      ).to.not.be.reverted;
    });

    it("attestProgress stores correct metricType for WORLD_MODEL_EVOLUTION", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();
      const tx = await attestation.attestProgress(deviceId, baselineHash, currentHash, 4, 1800);
      const receipt = await tx.wait();
      const history = await attestation.getProgressHistory(deviceId);
      const last = history[history.length - 1];
      expect(Number(last.metricType)).to.equal(4);
      expect(Number(last.improvementBps)).to.equal(1800);
    });

    it("all 5 MetricType values are accepted: REACTION_TIME to WORLD_MODEL_EVOLUTION", async function () {
      // Register device once; use fresh record pairs with incrementing counters
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      let ctr = 0;
      for (let metricType = 0; metricType <= 4; metricType++) {
        ctr++;
        const body1 = buildRawBody({ monotonicCtr: ctr, timestampMs: ts, confidence: 150 });
        await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
        const h1 = computeRecordHash(body1);
        ctr++;
        const body2 = buildRawBody({ monotonicCtr: ctr, timestampMs: ts, prevHash: h1, confidence: 200 });
        await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
        const h2 = computeRecordHash(body2);
        await expect(
          attestation.attestProgress(deviceId, h1, h2, metricType, 500)
        ).to.not.be.reverted;
      }
    });

    it("WORLD_MODEL_EVOLUTION improvement BPS round-trips on-chain", async function () {
      await registerDevice();
      const { baselineHash, currentHash } = await submitTwoRecords();
      const expectedBps = 6250;   // simulates ~50% bit divergence between two hashes
      await attestation.attestProgress(deviceId, baselineHash, currentHash, 4, expectedBps);
      const history = await attestation.getProgressHistory(deviceId);
      const record = history[history.length - 1];
      expect(Number(record.improvementBps)).to.equal(expectedBps);
    });
  });
});
