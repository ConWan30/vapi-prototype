const { expect } = require("chai");
const { ethers, network } = require("hardhat");

// ---------------------------------------------------------------------------
//  Mock P256 precompile bytecode
//  PUSH1 0x01 | PUSH1 0x00 | MSTORE | PUSH1 0x20 | PUSH1 0x00 | RETURN
//  Always returns 1 (valid signature) in a 32-byte word.
// ---------------------------------------------------------------------------
const MOCK_P256_BYTECODE = "0x600160005260206000f3";
const P256_PRECOMPILE_ADDR = "0x0000000000000000000000000000000000000100";

// ---------------------------------------------------------------------------
//  Test constants
// ---------------------------------------------------------------------------
const DEVICE_PUBKEY = "0x04" + "aa".repeat(32) + "bb".repeat(32);
const FAKE_SIG = "0x" + "cc".repeat(64);
const MIN_DEPOSIT = ethers.parseEther("0.01");
const MAX_TIMESTAMP_SKEW = 300; // 300 seconds = 5 minutes

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
  // Write 4 hashes (each 32 bytes)
  Buffer.from(prevHash.slice(2), "hex").copy(buf, offset);
  offset += 32;
  Buffer.from(sensorCommitment.slice(2), "hex").copy(buf, offset);
  offset += 32;
  Buffer.from(modelManifestHash.slice(2), "hex").copy(buf, offset);
  offset += 32;
  Buffer.from(worldModelHash.slice(2), "hex").copy(buf, offset);
  offset += 32;
  // Single-byte fields
  buf.writeUInt8(inferenceResult, offset++);
  buf.writeUInt8(actionCode, offset++);
  buf.writeUInt8(confidence, offset++);
  buf.writeUInt8(batteryPct, offset++);
  // uint32 big-endian (monotonic counter)
  buf.writeUInt32BE(monotonicCtr, offset);
  offset += 4;
  // int64 big-endian (timestamp in ms)
  buf.writeBigInt64BE(BigInt(timestampMs), offset);
  offset += 8;
  // IEEE 754 double big-endian (latitude)
  buf.writeDoubleBE(latitude, offset);
  offset += 8;
  // IEEE 754 double big-endian (longitude)
  buf.writeDoubleBE(longitude, offset);
  offset += 8;
  // uint32 big-endian (bounty ID)
  buf.writeUInt32BE(bountyId, offset);
  offset += 4;
  return "0x" + buf.toString("hex");
}

// ---------------------------------------------------------------------------
//  Helper: Get current block timestamp in milliseconds
// ---------------------------------------------------------------------------
async function currentBlockTimestampMs() {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp) * 1000n;
}

// ---------------------------------------------------------------------------
//  Helper: Compute expected record hash (SHA-256)
// ---------------------------------------------------------------------------
function computeRecordHash(rawBody) {
  return ethers.sha256(rawBody);
}

// ---------------------------------------------------------------------------
//  Test Suite
// ---------------------------------------------------------------------------
describe("PoACVerifier", function () {
  let owner, alice, bob;
  let registry, verifier;
  let deviceId;

  // Deploy fresh contracts and inject P256 mock before each test
  beforeEach(async function () {
    [owner, alice, bob] = await ethers.getSigners();

    // Deploy mock P256 precompile
    await network.provider.send("hardhat_setCode", [
      P256_PRECOMPILE_ADDR,
      MOCK_P256_BYTECODE,
    ]);

    // Deploy DeviceRegistry (minimumDeposit)
    const RegistryFactory = await ethers.getContractFactory("DeviceRegistry");
    registry = await RegistryFactory.deploy(MIN_DEPOSIT);
    await registry.waitForDeployment();

    // Deploy PoACVerifierTestable (skips P256 sig check — no IoTeX precompile in Hardhat)
    const VerifierFactory = await ethers.getContractFactory("PoACVerifierTestable");
    verifier = await VerifierFactory.deploy(
      await registry.getAddress(),
      MAX_TIMESTAMP_SKEW
    );
    await verifier.waitForDeployment();

    // Authorize the PoACVerifier as a reputation updater on the registry
    await registry.setReputationUpdater(await verifier.getAddress(), true);

    // Compute deviceId = keccak256(pubkey)
    deviceId = ethers.keccak256(DEVICE_PUBKEY);
  });

  // -----------------------------------------------------------------------
  //  Helper: register device (devices are active on registration)
  // -----------------------------------------------------------------------
  async function registerDevice() {
    await registry.registerDevice(DEVICE_PUBKEY, { value: MIN_DEPOSIT });
  }

  // Build body with block-aligned timestamp
  async function buildValidBody(overrides = {}) {
    const ts = await currentBlockTimestampMs();
    return buildRawBody({ timestampMs: ts, ...overrides });
  }

  // =========================================================================
  //  1. Deployment
  // =========================================================================
  describe("Deployment", function () {
    it("should deploy with correct DeviceRegistry reference", async function () {
      const registryAddr = await verifier.deviceRegistry();
      expect(registryAddr).to.equal(await registry.getAddress());
    });

    it("should deploy with correct maxTimestampSkew", async function () {
      const skew = await verifier.maxTimestampSkew();
      expect(skew).to.equal(BigInt(MAX_TIMESTAMP_SKEW));
    });

    it("should set deployer as owner", async function () {
      expect(await verifier.owner()).to.equal(owner.address);
    });
  });

  // =========================================================================
  //  2. Constants
  // =========================================================================
  describe("Constants", function () {
    it("should have POAC_BODY_SIZE = 164", async function () {
      expect(await verifier.POAC_BODY_SIZE()).to.equal(164n);
    });

    it("should have POAC_SIG_SIZE = 64", async function () {
      expect(await verifier.POAC_SIG_SIZE()).to.equal(64n);
    });

    it("should have P256_PRECOMPILE = 0x0100", async function () {
      const precompile = await verifier.P256_PRECOMPILE();
      expect(precompile.toLowerCase()).to.equal(
        P256_PRECOMPILE_ADDR.toLowerCase()
      );
    });
  });

  // =========================================================================
  //  3. Reject invalid body length
  // =========================================================================
  describe("Invalid body length", function () {
    it("should revert with InvalidBodyLength when body is too short", async function () {
      await registerDevice();
      const shortBody = "0x" + "aa".repeat(100);
      await expect(
        verifier.verifyPoAC(deviceId, shortBody, FAKE_SIG)
      )
        .to.be.revertedWithCustomError(verifier, "InvalidBodyLength")
        .withArgs(100);
    });

    it("should revert with InvalidBodyLength when body is too long", async function () {
      await registerDevice();
      const longBody = "0x" + "aa".repeat(200);
      await expect(
        verifier.verifyPoAC(deviceId, longBody, FAKE_SIG)
      )
        .to.be.revertedWithCustomError(verifier, "InvalidBodyLength")
        .withArgs(200);
    });
  });

  // =========================================================================
  //  4. Reject invalid signature length
  // =========================================================================
  describe("Invalid signature length", function () {
    it("should revert with InvalidSignatureLength when sig is 32 bytes", async function () {
      await registerDevice();
      const body = await buildValidBody();
      const shortSig = "0x" + "dd".repeat(32);
      await expect(
        verifier.verifyPoAC(deviceId, body, shortSig)
      )
        .to.be.revertedWithCustomError(verifier, "InvalidSignatureLength")
        .withArgs(32);
    });

    it("should revert with InvalidSignatureLength when sig is 128 bytes", async function () {
      await registerDevice();
      const body = await buildValidBody();
      const longSig = "0x" + "dd".repeat(128);
      await expect(
        verifier.verifyPoAC(deviceId, body, longSig)
      )
        .to.be.revertedWithCustomError(verifier, "InvalidSignatureLength")
        .withArgs(128);
    });
  });

  // =========================================================================
  //  5. Reject unregistered device
  // =========================================================================
  describe("Unregistered device", function () {
    it("should revert with DeviceNotRegistered for unknown deviceId", async function () {
      const body = await buildValidBody();
      const fakeDeviceId = ethers.keccak256("0xdeadbeef");
      await expect(
        verifier.verifyPoAC(fakeDeviceId, body, FAKE_SIG)
      ).to.be.revertedWithCustomError(verifier, "DeviceNotRegistered");
    });
  });

  // =========================================================================
  //  6. Reject inactive device
  // =========================================================================
  describe("Inactive device", function () {
    it("should revert with DeviceNotActive for deactivated device", async function () {
      // Register then deactivate
      await registry.registerDevice(DEVICE_PUBKEY, { value: MIN_DEPOSIT });
      await registry.deactivateDevice(deviceId);

      const body = await buildValidBody();
      await expect(
        verifier.verifyPoAC(deviceId, body, FAKE_SIG)
      ).to.be.revertedWithCustomError(verifier, "DeviceNotActive");
    });
  });

  // =========================================================================
  //  7. Successful single verification
  // =========================================================================
  describe("Successful single verification", function () {
    it("should verify a valid PoAC record and update state", async function () {
      await registerDevice();
      const tsMs = await currentBlockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: tsMs });
      const expectedHash = computeRecordHash(body);

      const tx = await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
      await tx.wait();

      // Verify the record hash is stored
      expect(await verifier.isRecordVerified(expectedHash)).to.be.true;

      // Verify chain state
      const [lastHash, lastCtr, initialized] =
        await verifier.getChainHead(deviceId);
      expect(lastHash).to.equal(expectedHash);
      expect(lastCtr).to.equal(1);
      expect(initialized).to.be.true;

      // Verify count
      expect(await verifier.getVerifiedCount(deviceId)).to.equal(1);

      // Verify total
      expect(await verifier.totalVerifiedCount()).to.equal(1);

      // Verify PoACVerified event was emitted with correct fields
      await expect(tx)
        .to.emit(verifier, "PoACVerified")
        .withArgs(
          deviceId,
          expectedHash,
          1, // monotonicCtr
          tsMs, // timestampMs — use the same value we put in the body
          1, // actionCode
          0x20, // inferenceResult
          0 // bountyId
        );
    });
  });

  // =========================================================================
  //  8. Record hash is SHA-256
  // =========================================================================
  describe("Record hash is SHA-256", function () {
    it("should produce recordHash == sha256(rawBody)", async function () {
      await registerDevice();
      const body = await buildValidBody({ monotonicCtr: 1 });
      const expectedHash = ethers.sha256(body);

      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);

      // The chain head hash should match ethers.sha256 of the raw body
      const [lastHash] = await verifier.getChainHead(deviceId);
      expect(lastHash).to.equal(expectedHash);

      // Double-check via verifiedRecords mapping
      expect(await verifier.isRecordVerified(expectedHash)).to.be.true;
    });

    it("should NOT match keccak256 of the body", async function () {
      await registerDevice();
      const body = await buildValidBody({ monotonicCtr: 1 });
      const keccakHash = ethers.keccak256(body);

      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);

      const [lastHash] = await verifier.getChainHead(deviceId);
      // SHA-256 and keccak256 should differ
      expect(lastHash).to.not.equal(keccakHash);
    });
  });

  // =========================================================================
  //  9. Reject duplicate record
  // =========================================================================
  describe("Duplicate record", function () {
    it("should revert with RecordAlreadySubmitted on second submission (Phase 6)", async function () {
      await registerDevice();
      const body = await buildValidBody({ monotonicCtr: 1 });
      const expectedHash = computeRecordHash(body);

      // First submission succeeds
      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);

      // Second submission of same body reverts with RecordAlreadySubmitted
      // (submittedHashes gate fires before verifiedRecords check)
      await expect(verifier.verifyPoAC(deviceId, body, FAKE_SIG))
        .to.be.revertedWithCustomError(verifier, "RecordAlreadySubmitted")
        .withArgs(expectedHash);
    });
  });

  // =========================================================================
  //  10. Monotonic counter enforcement
  // =========================================================================
  describe("Monotonic counter enforcement", function () {
    it("should revert with CounterNotMonotonic when counter goes backwards", async function () {
      await registerDevice();

      // Submit counter = 5
      const body1 = await buildValidBody({ monotonicCtr: 5 });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

      // Submit counter = 3 (less than 5)
      const ts = await currentBlockTimestampMs();
      const body2 = buildRawBody({ monotonicCtr: 3, timestampMs: ts + 1000n });
      await expect(verifier.verifyPoAC(deviceId, body2, FAKE_SIG))
        .to.be.revertedWithCustomError(verifier, "CounterNotMonotonic")
        .withArgs(deviceId, 3, 5);
    });

    it("should revert with CounterNotMonotonic when counter is equal", async function () {
      await registerDevice();

      // Submit counter = 5
      const body1 = await buildValidBody({ monotonicCtr: 5 });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

      // Submit counter = 5 again (equal, not strictly greater)
      const ts = await currentBlockTimestampMs();
      const body2 = buildRawBody({ monotonicCtr: 5, timestampMs: ts + 1000n });
      await expect(verifier.verifyPoAC(deviceId, body2, FAKE_SIG))
        .to.be.revertedWithCustomError(verifier, "CounterNotMonotonic")
        .withArgs(deviceId, 5, 5);
    });

    it("should accept strictly increasing counter", async function () {
      await registerDevice();

      const ts = await currentBlockTimestampMs();
      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

      // Need prevHash = sha256(body1) for chain linkage to pass
      const prevHash = computeRecordHash(body1);
      const body2 = buildRawBody({
        monotonicCtr: 2,
        timestampMs: ts,
        prevHash,
      });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);

      const [, lastCtr] = await verifier.getChainHead(deviceId);
      expect(lastCtr).to.equal(2);
    });
  });

  // =========================================================================
  //  11. Timestamp validation
  // =========================================================================
  describe("Timestamp validation", function () {
    it("should revert with TimestampOutOfRange for very old timestamp", async function () {
      await registerDevice();

      // Timestamp from year 2000 (way outside 5 min skew)
      const oldTs = BigInt(946684800) * 1000n; // Jan 1 2000
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: oldTs });

      await expect(
        verifier.verifyPoAC(deviceId, body, FAKE_SIG)
      ).to.be.revertedWithCustomError(verifier, "TimestampOutOfRange");
    });

    it("should revert with TimestampOutOfRange for timestamp too far in the future", async function () {
      await registerDevice();

      const ts = await currentBlockTimestampMs();
      // 10 minutes (600s) into the future, skew is 300s
      const futureTs = ts + 600000n;
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: futureTs });

      await expect(
        verifier.verifyPoAC(deviceId, body, FAKE_SIG)
      ).to.be.revertedWithCustomError(verifier, "TimestampOutOfRange");
    });

    it("should accept timestamp within skew range", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      // 1 minute offset (within 5 min skew)
      const body = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts + 60000n,
      });
      await expect(verifier.verifyPoAC(deviceId, body, FAKE_SIG)).to.not.be
        .reverted;
    });
  });

  // =========================================================================
  //  12. Chain linkage
  // =========================================================================
  describe("Chain linkage", function () {
    it("should accept genesis record with prevHash = 0x00", async function () {
      await registerDevice();
      const body = await buildValidBody({
        monotonicCtr: 1,
        prevHash: ethers.ZeroHash,
      });
      await expect(verifier.verifyPoAC(deviceId, body, FAKE_SIG)).to.not.be
        .reverted;
    });

    it("should revert with ChainLinkageBroken when prevHash does not match", async function () {
      await registerDevice();

      // Record 1 (genesis)
      const body1 = await buildValidBody({
        monotonicCtr: 1,
        prevHash: ethers.ZeroHash,
      });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

      const correctPrevHash = computeRecordHash(body1);
      const wrongPrevHash =
        "0x1111111111111111111111111111111111111111111111111111111111111111";

      // Record 2 with wrong prevHash
      const ts = await currentBlockTimestampMs();
      const body2 = buildRawBody({
        monotonicCtr: 2,
        timestampMs: ts,
        prevHash: wrongPrevHash,
      });

      await expect(verifier.verifyPoAC(deviceId, body2, FAKE_SIG))
        .to.be.revertedWithCustomError(verifier, "ChainLinkageBroken")
        .withArgs(deviceId, wrongPrevHash, correctPrevHash);
    });

    it("should accept correct chain linkage", async function () {
      await registerDevice();

      const ts = await currentBlockTimestampMs();
      const body1 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        prevHash: ethers.ZeroHash,
      });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

      const prevHash = computeRecordHash(body1);
      const body2 = buildRawBody({
        monotonicCtr: 2,
        timestampMs: ts,
        prevHash,
      });
      await expect(verifier.verifyPoAC(deviceId, body2, FAKE_SIG)).to.not.be
        .reverted;
    });
  });

  // =========================================================================
  //  13. Valid chain of 3 records
  // =========================================================================
  describe("Valid chain of 3 records", function () {
    it("should build a chain of 3 properly linked records", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      // Record 1 (genesis)
      const body1 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        prevHash: ethers.ZeroHash,
        inferenceResult: 0x10,
        actionCode: 0x01,
      });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const hash1 = computeRecordHash(body1);

      expect(await verifier.getVerifiedCount(deviceId)).to.equal(1);
      expect(await verifier.isRecordVerified(hash1)).to.be.true;

      // Record 2
      const body2 = buildRawBody({
        monotonicCtr: 2,
        timestampMs: ts,
        prevHash: hash1,
        inferenceResult: 0x20,
        actionCode: 0x02,
      });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      const hash2 = computeRecordHash(body2);

      expect(await verifier.getVerifiedCount(deviceId)).to.equal(2);
      expect(await verifier.isRecordVerified(hash2)).to.be.true;

      // Record 3
      const body3 = buildRawBody({
        monotonicCtr: 3,
        timestampMs: ts,
        prevHash: hash2,
        inferenceResult: 0x30,
        actionCode: 0x03,
      });
      await verifier.verifyPoAC(deviceId, body3, FAKE_SIG);
      const hash3 = computeRecordHash(body3);

      expect(await verifier.getVerifiedCount(deviceId)).to.equal(3);
      expect(await verifier.isRecordVerified(hash3)).to.be.true;

      // Verify chain head is now record 3
      const [lastHash, lastCtr, initialized] =
        await verifier.getChainHead(deviceId);
      expect(lastHash).to.equal(hash3);
      expect(lastCtr).to.equal(3);
      expect(initialized).to.be.true;

      // Total count
      expect(await verifier.totalVerifiedCount()).to.equal(3);
    });
  });

  // =========================================================================
  //  14. Batch verification
  // =========================================================================
  describe("Batch verification", function () {
    it("should verify 3 records in a single batch", async function () {
      // Register 3 different devices
      const pubkey1 = "0x04" + "11".repeat(32) + "21".repeat(32);
      const pubkey2 = "0x04" + "12".repeat(32) + "22".repeat(32);
      const pubkey3 = "0x04" + "13".repeat(32) + "23".repeat(32);

      const did1 = ethers.keccak256(pubkey1);
      const did2 = ethers.keccak256(pubkey2);
      const did3 = ethers.keccak256(pubkey3);

      await registry.registerDevice(pubkey1, { value: MIN_DEPOSIT });
      await registry.registerDevice(pubkey2, { value: MIN_DEPOSIT });
      await registry.registerDevice(pubkey3, { value: MIN_DEPOSIT });

      const ts = await currentBlockTimestampMs();

      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      const body2 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        confidence: 150,
      });
      const body3 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        confidence: 100,
      });

      const tx = await verifier.verifyPoACBatch(
        [did1, did2, did3],
        [body1, body2, body3],
        [FAKE_SIG, FAKE_SIG, FAKE_SIG]
      );

      // Check BatchVerified event: 3 submitted, 3 verified, 0 rejected
      await expect(tx)
        .to.emit(verifier, "BatchVerified")
        .withArgs(3, 3, 0);

      // Verify all records exist
      expect(await verifier.isRecordVerified(computeRecordHash(body1))).to.be
        .true;
      expect(await verifier.isRecordVerified(computeRecordHash(body2))).to.be
        .true;
      expect(await verifier.isRecordVerified(computeRecordHash(body3))).to.be
        .true;

      // Total count across all devices
      expect(await verifier.totalVerifiedCount()).to.equal(3);
    });

    it("should revert with EmptyBatch for empty arrays", async function () {
      await expect(
        verifier.verifyPoACBatch([], [], [])
      ).to.be.revertedWithCustomError(verifier, "EmptyBatch");
    });

    it("should revert with ArrayLengthMismatch for mismatched arrays", async function () {
      const body = await buildValidBody();
      await expect(
        verifier.verifyPoACBatch([deviceId, deviceId], [body], [FAKE_SIG])
      ).to.be.revertedWithCustomError(verifier, "ArrayLengthMismatch");
    });
  });

  // =========================================================================
  //  15. Batch with one failure
  // =========================================================================
  describe("Batch with partial failure", function () {
    it("should verify 2 of 3 records when middle one has bad counter", async function () {
      // Use 3 separate devices so counter conflicts don't interfere
      const pubkey1 = "0x04" + "a1".repeat(32) + "b1".repeat(32);
      const pubkey2 = "0x04" + "a2".repeat(32) + "b2".repeat(32);
      const pubkey3 = "0x04" + "a3".repeat(32) + "b3".repeat(32);

      const did1 = ethers.keccak256(pubkey1);
      const did2 = ethers.keccak256(pubkey2);
      const did3 = ethers.keccak256(pubkey3);

      await registry.registerDevice(pubkey1, { value: MIN_DEPOSIT });
      await registry.registerDevice(pubkey2, { value: MIN_DEPOSIT });
      await registry.registerDevice(pubkey3, { value: MIN_DEPOSIT });

      const ts = await currentBlockTimestampMs();

      // First: Pre-submit a record for device 2 with counter=5
      const preBody = buildRawBody({ monotonicCtr: 5, timestampMs: ts });
      await verifier.verifyPoAC(did2, preBody, FAKE_SIG);

      // Now batch: device1 ctr=1 (OK), device2 ctr=3 (BAD: 3 <= 5), device3 ctr=1 (OK)
      const body1 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + "f1".repeat(32),
      });
      const body2 = buildRawBody({
        monotonicCtr: 3,
        timestampMs: ts,
        sensorCommitment: "0x" + "f2".repeat(32),
      });
      const body3 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + "f3".repeat(32),
      });

      const tx = await verifier.verifyPoACBatch(
        [did1, did2, did3],
        [body1, body2, body3],
        [FAKE_SIG, FAKE_SIG, FAKE_SIG]
      );

      // 3 submitted, 2 verified, 1 rejected
      await expect(tx)
        .to.emit(verifier, "BatchVerified")
        .withArgs(3, 2, 1);

      // Device 1 and 3 records verified
      expect(await verifier.isRecordVerified(computeRecordHash(body1))).to.be
        .true;
      expect(await verifier.isRecordVerified(computeRecordHash(body3))).to.be
        .true;

      // Device 2 failed record not verified
      expect(await verifier.isRecordVerified(computeRecordHash(body2))).to.be
        .false;

      // Total count: 1 (pre-submit) + 2 (batch) = 3
      expect(await verifier.totalVerifiedCount()).to.equal(3);
    });

    it("should return zero hash for failed batch entries", async function () {
      // Register one device, submit twice in batch (second will be duplicate)
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts });

      // Duplicate in same batch: first succeeds, second fails (RecordAlreadyVerified)
      const tx = await verifier.verifyPoACBatch(
        [deviceId, deviceId],
        [body, body],
        [FAKE_SIG, FAKE_SIG]
      );

      await expect(tx)
        .to.emit(verifier, "BatchVerified")
        .withArgs(2, 1, 1);
    });
  });

  // =========================================================================
  //  16. View functions
  // =========================================================================
  describe("View functions", function () {
    describe("getChainHead", function () {
      it("should return uninitialized state for unknown device", async function () {
        const [lastHash, lastCtr, initialized] =
          await verifier.getChainHead(ethers.ZeroHash);
        expect(lastHash).to.equal(ethers.ZeroHash);
        expect(lastCtr).to.equal(0);
        expect(initialized).to.be.false;
      });

      it("should return correct state after verification", async function () {
        await registerDevice();
        const body = await buildValidBody({ monotonicCtr: 42 });
        await verifier.verifyPoAC(deviceId, body, FAKE_SIG);

        const [lastHash, lastCtr, initialized] =
          await verifier.getChainHead(deviceId);
        expect(lastHash).to.equal(computeRecordHash(body));
        expect(lastCtr).to.equal(42);
        expect(initialized).to.be.true;
      });
    });

    describe("getVerifiedCount", function () {
      it("should return 0 for device with no verifications", async function () {
        expect(await verifier.getVerifiedCount(deviceId)).to.equal(0);
      });

      it("should increment after each verification", async function () {
        await registerDevice();
        const ts = await currentBlockTimestampMs();

        const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
        await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
        expect(await verifier.getVerifiedCount(deviceId)).to.equal(1);

        const prevHash = computeRecordHash(body1);
        const body2 = buildRawBody({
          monotonicCtr: 2,
          timestampMs: ts,
          prevHash,
        });
        await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
        expect(await verifier.getVerifiedCount(deviceId)).to.equal(2);
      });
    });

    describe("isRecordVerified", function () {
      it("should return false for unknown hash", async function () {
        expect(await verifier.isRecordVerified(ethers.ZeroHash)).to.be.false;
      });

      it("should return true after record is verified", async function () {
        await registerDevice();
        const body = await buildValidBody({ monotonicCtr: 1 });
        const hash = computeRecordHash(body);

        expect(await verifier.isRecordVerified(hash)).to.be.false;
        await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
        expect(await verifier.isRecordVerified(hash)).to.be.true;
      });
    });

    describe("totalVerifiedCount", function () {
      it("should start at 0", async function () {
        expect(await verifier.totalVerifiedCount()).to.equal(0);
      });

      it("should track total across multiple devices", async function () {
        // Register two devices
        const pubkey2 = "0x04" + "dd".repeat(32) + "ee".repeat(32);
        const did2 = ethers.keccak256(pubkey2);

        await registerDevice();
        await registry.registerDevice(pubkey2, { value: MIN_DEPOSIT });

        const ts = await currentBlockTimestampMs();

        // Device 1: 1 record
        const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
        await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

        // Device 2: 1 record (unique body via different sensorCommitment)
        const body2 = buildRawBody({
          monotonicCtr: 1,
          timestampMs: ts,
          sensorCommitment: "0x" + "ab".repeat(32),
        });
        await verifier.verifyPoAC(did2, body2, FAKE_SIG);

        expect(await verifier.totalVerifiedCount()).to.equal(2);
      });
    });
  });

  // =========================================================================
  //  17. Admin: setMaxTimestampSkew
  // =========================================================================
  describe("Admin: setMaxTimestampSkew", function () {
    it("should allow owner to update maxTimestampSkew", async function () {
      await verifier.setMaxTimestampSkew(600);
      expect(await verifier.maxTimestampSkew()).to.equal(600);
    });

    it("should reject non-owner from updating maxTimestampSkew", async function () {
      await expect(
        verifier.connect(alice).setMaxTimestampSkew(600)
      ).to.be.revertedWithCustomError(verifier, "OwnableUnauthorizedAccount");
    });

    it("should enforce new skew after update", async function () {
      await registerDevice();

      // Set skew to 10 seconds
      await verifier.setMaxTimestampSkew(10);

      const ts = await currentBlockTimestampMs();
      // 60 seconds offset -- exceeds 10s skew
      const body = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts + 60000n,
      });
      await expect(
        verifier.verifyPoAC(deviceId, body, FAKE_SIG)
      ).to.be.revertedWithCustomError(verifier, "TimestampOutOfRange");

      // But within 10s should succeed
      const body2 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts + 5000n,
      });
      await expect(verifier.verifyPoAC(deviceId, body2, FAKE_SIG)).to.not.be
        .reverted;
    });
  });

  // =========================================================================
  //  Phase 6: submittedHashes replay protection
  // =========================================================================
  describe("Phase 6: submittedHashes anti-replay", function () {
    it("submittedHashes is false before and true after successful verifyPoAC", async function () {
      await registerDevice();
      const body = await buildValidBody({ monotonicCtr: 1 });
      const h = computeRecordHash(body);

      expect(await verifier.submittedHashes(h)).to.be.false;
      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
      expect(await verifier.submittedHashes(h)).to.be.true;
      // verifiedRecords also true after success
      expect(await verifier.isRecordVerified(h)).to.be.true;
    });

    it("failed batch entry sets submittedHashes blocking single-TX retry", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      // Pre-submit a record with counter=5
      const preBody = buildRawBody({ monotonicCtr: 5, timestampMs: ts });
      await verifier.verifyPoAC(deviceId, preBody, FAKE_SIG);

      // body2 has counter=3 < 5 — will fail batch verification
      const body2 = buildRawBody({
        monotonicCtr: 3,
        timestampMs: ts,
        sensorCommitment: "0x" + "ab".repeat(32),
      });
      const h2 = computeRecordHash(body2);

      // Batch submit: body2 fails inside child call, but submittedHashes[h2]
      // is set in OUTER context before the try/catch — it persists
      await verifier.verifyPoACBatch([deviceId], [body2], [FAKE_SIG]);

      // submittedHashes is true (set in outer context), verifiedRecords is false
      expect(await verifier.submittedHashes(h2)).to.be.true;
      expect(await verifier.isRecordVerified(h2)).to.be.false;

      // Retry via single verifyPoAC: blocked by submittedHashes gate
      await expect(verifier.verifyPoAC(deviceId, body2, FAKE_SIG))
        .to.be.revertedWithCustomError(verifier, "RecordAlreadySubmitted")
        .withArgs(h2);
    });

    it("single-TX path allows retry after transient failure (submittedHashes not set on revert)", async function () {
      // Register and build a body with invalid length (will revert in verifyPoAC)
      // The TX reverts entirely, so submittedHashes stays false — retry is allowed
      await registerDevice();
      const validBody = await buildValidBody({ monotonicCtr: 1 });
      const h = computeRecordHash(validBody);

      // Submit valid record — succeeds
      await verifier.verifyPoAC(deviceId, validBody, FAKE_SIG);
      expect(await verifier.submittedHashes(h)).to.be.true;

      // Build a DIFFERENT record (counter 2) — not previously submitted
      const ts = await currentBlockTimestampMs();
      const body2 = buildRawBody({
        monotonicCtr: 2,
        timestampMs: ts,
        prevHash: h,
        sensorCommitment: "0x" + "de".repeat(32),
      });
      const h2 = computeRecordHash(body2);

      // First attempt: body2 not in submittedHashes → proceeds into _verifyInternal
      expect(await verifier.submittedHashes(h2)).to.be.false;
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      expect(await verifier.submittedHashes(h2)).to.be.true;
    });

    it("batch with wrong-length body counts as rejected without setting submittedHashes", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const shortBody = "0x" + "aa".repeat(100); // wrong length

      const tx = await verifier.verifyPoACBatch(
        [deviceId],
        [shortBody],
        [FAKE_SIG]
      );
      // 1 submitted, 0 verified, 1 rejected
      await expect(tx)
        .to.emit(verifier, "BatchVerified")
        .withArgs(1, 0, 1);

      // Short body was rejected before sha256, submittedHashes not set
      expect(await verifier.submittedHashes(ethers.sha256(shortBody))).to.be.false;
    });
  });

  // =========================================================================
  //  Additional edge-case tests
  // =========================================================================
  describe("Edge cases", function () {
    it("should accept genesis record with prevHash = 0 even after chain init", async function () {
      // The contract checks: if prevHash != 0x00, then validate linkage.
      // So a prevHash of 0x00 always skips linkage check.
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      // Record 1 (genesis)
      const body1 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        prevHash: ethers.ZeroHash,
      });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

      // Record 2 also with prevHash = 0x00 -- skips linkage check entirely
      const body2 = buildRawBody({
        monotonicCtr: 2,
        timestampMs: ts,
        prevHash: ethers.ZeroHash,
      });
      // This should succeed because prevHash == 0 skips the linkage check
      await expect(verifier.verifyPoAC(deviceId, body2, FAKE_SIG)).to.not.be
        .reverted;
    });

    it("should reject verifyPoACExternal called directly by non-contract", async function () {
      await registerDevice();
      const body = await buildValidBody({ monotonicCtr: 1 });
      await expect(
        verifier.verifyPoACExternal(deviceId, body, FAKE_SIG)
      ).to.be.revertedWith("PoACVerifier: internal only");
    });

    it("should update device reputation via DeviceRegistry after verification", async function () {
      await registerDevice();
      const body = await buildValidBody({ monotonicCtr: 1 });

      // Get reputation before
      const infoBefore = await registry.getDeviceInfo(deviceId);
      const verifiedBefore = infoBefore.verifiedPoACCount;

      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);

      // Get reputation after
      const infoAfter = await registry.getDeviceInfo(deviceId);
      expect(infoAfter.verifiedPoACCount).to.equal(verifiedBefore + 1n);
    });

    it("should emit PoACVerified with correct parsed fields", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      const body = buildRawBody({
        monotonicCtr: 7,
        timestampMs: ts,
        inferenceResult: 0x42,
        actionCode: 0x05,
        bountyId: 99,
      });

      const tx = await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
      const hash = computeRecordHash(body);

      await expect(tx)
        .to.emit(verifier, "PoACVerified")
        .withArgs(deviceId, hash, 7, ts, 5, 0x42, 99);
    });

    it("should handle multiple devices independently", async function () {
      const pubkey2 = "0x04" + "ff".repeat(32) + "ee".repeat(32);
      const did2 = ethers.keccak256(pubkey2);

      await registerDevice();
      await registry.registerDevice(pubkey2, { value: MIN_DEPOSIT });

      const ts = await currentBlockTimestampMs();

      // Device 1: counter 10
      const body1 = buildRawBody({ monotonicCtr: 10, timestampMs: ts });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);

      // Device 2: counter 1 (independent, should not conflict with device 1)
      const body2 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + "01".repeat(32),
      });
      await verifier.verifyPoAC(did2, body2, FAKE_SIG);

      const [, ctr1] = await verifier.getChainHead(deviceId);
      const [, ctr2] = await verifier.getChainHead(did2);
      expect(ctr1).to.equal(10);
      expect(ctr2).to.equal(1);
    });
  });

  // =========================================================================
  //  18. Record Inference and Schema Storage
  // =========================================================================
  describe("Record Inference and Schema Storage", function () {
    it("verifyPoAC stores inferenceResult from body byte 128", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts, inferenceResult: 0x21 });
      const hash = computeRecordHash(body);
      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
      expect(await verifier.recordInferences(hash)).to.equal(0x21);
    });

    it("verifyPoAC stores inferenceResult 0x00 for clean record", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts, inferenceResult: 0x00 });
      const hash = computeRecordHash(body);
      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
      expect(await verifier.recordInferences(hash)).to.equal(0x00);
    });

    it("verifyPoACWithSchema stores schema version and sets recordHasSchema", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + "ab".repeat(32),
      });
      const hash = computeRecordHash(body);
      await verifier.verifyPoACWithSchema(deviceId, body, FAKE_SIG, 2);
      expect(await verifier.recordSchemas(hash)).to.equal(2);
      expect(await verifier.recordHasSchema(hash)).to.equal(true);
    });

    it("verifyPoAC does NOT set recordHasSchema (legacy path)", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      const hash = computeRecordHash(body);
      await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
      expect(await verifier.recordHasSchema(hash)).to.equal(false);
      const [schema, isSet] = await verifier.getRecordSchema(hash);
      expect(schema).to.equal(0);
      expect(isSet).to.equal(false);
    });

    it("getRecordSchema returns (schema, true) after verifyPoACWithSchema", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + "cd".repeat(32),
      });
      const hash = computeRecordHash(body);
      await verifier.verifyPoACWithSchema(deviceId, body, FAKE_SIG, 1);
      const [schema, isSet] = await verifier.getRecordSchema(hash);
      expect(schema).to.equal(1);
      expect(isSet).to.equal(true);
    });

    it("verifyPoACWithSchema reverts InvalidSchemaVersion when schemaVersion is 0", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();
      const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      await expect(
        verifier.verifyPoACWithSchema(deviceId, body, FAKE_SIG, 0)
      ).to.be.revertedWithCustomError(verifier, "InvalidSchemaVersion");
    });
  });
});
