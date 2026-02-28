const { expect } = require("chai");
const { ethers, network } = require("hardhat");

// ---------------------------------------------------------------------------
//  Mock P256 precompile bytecode (always returns 1 = valid signature)
// ---------------------------------------------------------------------------
const MOCK_P256_BYTECODE = "0x600160005260206000f3";
const P256_PRECOMPILE_ADDR = "0x0000000000000000000000000000000000000100";

// ---------------------------------------------------------------------------
//  Test constants
// ---------------------------------------------------------------------------
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
describe("SkillOracle", function () {
  let owner, alice;
  let registry, verifier, oracle;
  let deviceId;

  beforeEach(async function () {
    [owner, alice] = await ethers.getSigners();

    await network.provider.send("hardhat_setCode", [
      P256_PRECOMPILE_ADDR,
      MOCK_P256_BYTECODE,
    ]);

    // Deploy DeviceRegistry
    const RegistryFactory = await ethers.getContractFactory("DeviceRegistry");
    registry = await RegistryFactory.deploy(MIN_DEPOSIT);
    await registry.waitForDeployment();

    // Deploy PoACVerifierTestable
    const VerifierFactory = await ethers.getContractFactory("PoACVerifierTestable");
    verifier = await VerifierFactory.deploy(
      await registry.getAddress(),
      MAX_TIMESTAMP_SKEW
    );
    await verifier.waitForDeployment();

    await registry.setReputationUpdater(await verifier.getAddress(), true);

    // Deploy SkillOracle
    const OracleFactory = await ethers.getContractFactory("SkillOracle");
    oracle = await OracleFactory.deploy(await verifier.getAddress());
    await oracle.waitForDeployment();

    deviceId = ethers.keccak256(DEVICE_PUBKEY);
  });

  async function registerDevice() {
    await registry.registerDevice(DEVICE_PUBKEY, { value: MIN_DEPOSIT });
  }

  // Submit a PoAC record and return its hash
  async function submitAndGetHash(overrides = {}) {
    const ts = await currentBlockTimestampMs();
    const body = buildRawBody({ timestampMs: ts, ...overrides });
    await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
    return computeRecordHash(body);
  }

  // =========================================================================
  //  1. Deployment
  // =========================================================================
  describe("Deployment", function () {
    it("should deploy with correct PoACVerifier reference", async function () {
      expect(await oracle.poACVerifier()).to.equal(await verifier.getAddress());
    });

    it("should set deployer as owner", async function () {
      expect(await oracle.owner()).to.equal(owner.address);
    });

    it("should start with zero profiles", async function () {
      expect(await oracle.totalProfileCount()).to.equal(0);
    });
  });

  // =========================================================================
  //  2. Constants
  // =========================================================================
  describe("Constants", function () {
    it("should have INITIAL_RATING = 1000", async function () {
      expect(await oracle.INITIAL_RATING()).to.equal(1000);
    });

    it("should have MAX_RATING = 3000", async function () {
      expect(await oracle.MAX_RATING()).to.equal(3000);
    });

    it("should have NOMINAL_GAIN = 5", async function () {
      expect(await oracle.NOMINAL_GAIN()).to.equal(5);
    });

    it("should have SKILLED_GAIN = 12", async function () {
      expect(await oracle.SKILLED_GAIN()).to.equal(12);
    });

    it("should have CHEAT_PENALTY = 200", async function () {
      expect(await oracle.CHEAT_PENALTY()).to.equal(200);
    });
  });

  // =========================================================================
  //  3. Reject unverified records
  // =========================================================================
  describe("Reject unverified records", function () {
    it("should revert with RecordNotVerified for unknown record hash", async function () {
      const fakeHash = ethers.keccak256("0xdeadbeef");
      await expect(
        oracle.updateSkillRating(deviceId, fakeHash, 0x20, 200)
      ).to.be.revertedWithCustomError(oracle, "RecordNotVerified");
    });
  });

  // =========================================================================
  //  4. Reject already-processed records
  // =========================================================================
  describe("Reject already-processed records", function () {
    it("should revert with RecordAlreadyProcessed on second call", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({ monotonicCtr: 1 });

      // First update succeeds
      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 200);

      // Second update with same record should fail
      await expect(
        oracle.updateSkillRating(deviceId, recordHash, 0x20, 200)
      ).to.be.revertedWithCustomError(oracle, "RecordAlreadyProcessed");
    });
  });

  // =========================================================================
  //  5. Initial profile creation
  // =========================================================================
  describe("Initial profile creation", function () {
    it("should initialize profile with INITIAL_RATING on first record", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({ monotonicCtr: 1 });

      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 200);

      const profile = await oracle.getSkillProfile(deviceId);
      expect(profile.initialized).to.be.true;
      expect(profile.gamesPlayed).to.equal(1);
      expect(await oracle.totalProfileCount()).to.equal(1);
    });

    it("should not double-count profile on second record", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const hash1 = computeRecordHash(body1);

      const prevHash = hash1;
      const body2 = buildRawBody({ monotonicCtr: 2, timestampMs: ts, prevHash });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      const hash2 = computeRecordHash(body2);

      await oracle.updateSkillRating(deviceId, hash1, 0x20, 200);
      await oracle.updateSkillRating(deviceId, hash2, 0x20, 200);

      expect(await oracle.totalProfileCount()).to.equal(1);
    });
  });

  // =========================================================================
  //  6. Rating calculation — NOMINAL gameplay
  // =========================================================================
  describe("NOMINAL gameplay rating", function () {
    it("should gain rating based on confidence for NOMINAL play (0x20)", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({
        monotonicCtr: 1,
        inferenceResult: 0x20,
        confidence: 255,
      });

      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 255);

      const profile = await oracle.getSkillProfile(deviceId);
      // NOMINAL_GAIN * 255 / 255 = 5, rating = 1000 + 5 = 1005
      expect(profile.rating).to.equal(1005);
      expect(profile.cleanGames).to.equal(1);
      expect(profile.cheatFlags).to.equal(0);
    });

    it("should floor gain to 1 when confidence is very low", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({
        monotonicCtr: 1,
        inferenceResult: 0x20,
        confidence: 1,
      });

      // NOMINAL_GAIN * 1 / 255 = 0, floored to 1
      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 1);

      const profile = await oracle.getSkillProfile(deviceId);
      expect(profile.rating).to.equal(1001);
    });
  });

  // =========================================================================
  //  7. Rating calculation — SKILLED gameplay
  // =========================================================================
  describe("SKILLED gameplay rating", function () {
    it("should gain higher rating for SKILLED play (0x21)", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({
        monotonicCtr: 1,
        inferenceResult: 0x21,
        confidence: 255,
      });

      await oracle.updateSkillRating(deviceId, recordHash, 0x21, 255);

      const profile = await oracle.getSkillProfile(deviceId);
      // SKILLED_GAIN * 255 / 255 = 12, rating = 1000 + 12 = 1012
      expect(profile.rating).to.equal(1012);
      expect(profile.cleanGames).to.equal(1);
    });

    it("should scale SKILLED gain by confidence", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({
        monotonicCtr: 1,
        inferenceResult: 0x21,
        confidence: 128,
      });

      // SKILLED_GAIN * 128 / 255 = 6 (truncated)
      await oracle.updateSkillRating(deviceId, recordHash, 0x21, 128);

      const profile = await oracle.getSkillProfile(deviceId);
      expect(profile.rating).to.equal(1006);
    });
  });

  // =========================================================================
  //  8. Rating calculation — CHEAT detection
  // =========================================================================
  describe("CHEAT detection penalty", function () {
    it("should apply harsh penalty for cheat flag (0x22)", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({
        monotonicCtr: 1,
        inferenceResult: 0x22,
        confidence: 255,
      });

      await oracle.updateSkillRating(deviceId, recordHash, 0x22, 255);

      const profile = await oracle.getSkillProfile(deviceId);
      // 1000 - 200 = 800
      expect(profile.rating).to.equal(800);
      expect(profile.cheatFlags).to.equal(1);
      expect(profile.cleanGames).to.equal(0);
    });

    it("should apply penalty for all cheat codes (0x22-0x29)", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      // Submit 8 records, one for each cheat code
      let prevHash = ethers.ZeroHash;
      for (let cheatCode = 0x22; cheatCode <= 0x29; cheatCode++) {
        const ctr = cheatCode - 0x21; // 1, 2, ..., 8
        const body = buildRawBody({
          monotonicCtr: ctr,
          timestampMs: ts,
          prevHash,
          inferenceResult: cheatCode,
          confidence: 200,
        });
        await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
        const hash = computeRecordHash(body);
        await oracle.updateSkillRating(deviceId, hash, cheatCode, 200);
        prevHash = hash;
      }

      const profile = await oracle.getSkillProfile(deviceId);
      expect(profile.cheatFlags).to.equal(8);
      // Rating floor: 1000 - (8 * 200) = -600 → clamped to 0
      expect(profile.rating).to.equal(0);
    });

    it("should clamp rating to 0 (never go negative)", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({
        monotonicCtr: 1,
        inferenceResult: 0x22,
        confidence: 255,
      });

      await oracle.updateSkillRating(deviceId, recordHash, 0x22, 255);

      // Rating is now 800. Submit another cheat.
      const ts = await currentBlockTimestampMs();
      const prevHash = recordHash;
      const body2 = buildRawBody({
        monotonicCtr: 2,
        timestampMs: ts,
        prevHash,
        inferenceResult: 0x23,
        confidence: 255,
      });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      const hash2 = computeRecordHash(body2);
      await oracle.updateSkillRating(deviceId, hash2, 0x23, 255);

      const profile = await oracle.getSkillProfile(deviceId);
      // 800 - 200 = 600
      expect(profile.rating).to.equal(600);
    });
  });

  // =========================================================================
  //  9. Rating ceiling
  // =========================================================================
  describe("Rating ceiling", function () {
    it("should clamp rating to MAX_RATING (3000)", async function () {
      // Use multiple devices to avoid chain-linkage complexity
      // Each device starts at 1000, we submit enough SKILLED records to exceed 3000
      // on a single device by advancing the timestamp properly
      await registerDevice();

      let prevHash = ethers.ZeroHash;
      let currentRating = 1000;

      for (let i = 1; i <= 200 && currentRating < 3000; i++) {
        // Mine a new block with incremented timestamp to avoid skew
        await network.provider.send("evm_mine");
        const ts = await currentBlockTimestampMs();

        const body = buildRawBody({
          monotonicCtr: i,
          timestampMs: ts,
          prevHash,
          inferenceResult: 0x21,
          confidence: 255,
        });
        await verifier.verifyPoAC(deviceId, body, FAKE_SIG);
        const hash = computeRecordHash(body);
        await oracle.updateSkillRating(deviceId, hash, 0x21, 255);
        prevHash = hash;
        currentRating += 12;
      }

      const profile = await oracle.getSkillProfile(deviceId);
      expect(profile.rating).to.equal(3000);
    });
  });

  // =========================================================================
  //  10. Skill tiers
  // =========================================================================
  describe("Skill tiers", function () {
    it("should return Bronze for uninitialized device (rating 0)", async function () {
      const tier = await oracle.getSkillTier(deviceId);
      expect(tier).to.equal(0); // Bronze
    });

    it("should return Silver at initial rating (1000)", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({ monotonicCtr: 1 });
      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 1);

      // Rating = 1001, tier = Silver
      const tier = await oracle.getSkillTier(deviceId);
      expect(tier).to.equal(1); // Silver
    });
  });

  // =========================================================================
  //  11. Events
  // =========================================================================
  describe("Events", function () {
    it("should emit SkillRatingUpdated with correct values", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({
        monotonicCtr: 1,
        inferenceResult: 0x21,
        confidence: 255,
      });

      const tx = await oracle.updateSkillRating(deviceId, recordHash, 0x21, 255);

      await expect(tx)
        .to.emit(oracle, "SkillRatingUpdated")
        .withArgs(
          deviceId,
          1000, // oldRating
          1012, // newRating (1000 + 12)
          1,    // Silver tier
          1     // gamesPlayed
        );
    });
  });

  // =========================================================================
  //  12. View functions
  // =========================================================================
  describe("View functions", function () {
    it("getSkillRating should return correct tuple", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({ monotonicCtr: 1 });
      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 200);

      const [rating, gamesPlayed, lastUpdated] = await oracle.getSkillRating(deviceId);
      expect(rating).to.be.gt(0);
      expect(gamesPlayed).to.equal(1);
      expect(lastUpdated).to.be.gt(0);
    });

    it("getSkillProfile should return full profile struct", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({ monotonicCtr: 1 });
      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 200);

      const profile = await oracle.getSkillProfile(deviceId);
      expect(profile.rating).to.be.gt(0);
      expect(profile.gamesPlayed).to.equal(1);
      expect(profile.cleanGames).to.equal(1);
      expect(profile.cheatFlags).to.equal(0);
      expect(profile.initialized).to.be.true;
    });

    it("processedRecords should track processed state", async function () {
      await registerDevice();
      const recordHash = await submitAndGetHash({ monotonicCtr: 1 });

      expect(await oracle.processedRecords(recordHash)).to.be.false;
      await oracle.updateSkillRating(deviceId, recordHash, 0x20, 200);
      expect(await oracle.processedRecords(recordHash)).to.be.true;
    });
  });

  // =========================================================================
  //  13. Rate limiting (Phase 6)
  // =========================================================================
  describe("Rate limiting", function () {
    it("DEFAULT_MIN_INTERVAL should be 1", async function () {
      expect(await oracle.DEFAULT_MIN_INTERVAL()).to.equal(1);
    });

    it("minUpdateInterval initialises to DEFAULT_MIN_INTERVAL", async function () {
      expect(await oracle.minUpdateInterval()).to.equal(1);
    });

    it("owner can set minUpdateInterval", async function () {
      await oracle.setMinUpdateInterval(10);
      expect(await oracle.minUpdateInterval()).to.equal(10);
    });

    it("non-owner cannot set minUpdateInterval", async function () {
      await expect(
        oracle.connect(alice).setMinUpdateInterval(10)
      ).to.be.revertedWithCustomError(oracle, "OwnableUnauthorizedAccount");
    });

    it("should reject update when interval not yet satisfied", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const hash1 = computeRecordHash(body1);

      const prevHash = hash1;
      const body2 = buildRawBody({ monotonicCtr: 2, timestampMs: ts, prevHash });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      const hash2 = computeRecordHash(body2);

      // Set interval to 2 blocks so next-block update is still too early
      await oracle.setMinUpdateInterval(2);

      // First update succeeds (block N; lastUpdateBlock[deviceId] = N)
      await oracle.updateSkillRating(deviceId, hash1, 0x20, 200);

      // Second update at block N+1: nextAllowed = N+2, so N+1 < N+2 → revert
      await expect(
        oracle.updateSkillRating(deviceId, hash2, 0x20, 200)
      ).to.be.revertedWithCustomError(oracle, "RateLimitExceeded");
    });

    it("should allow update after interval blocks have passed", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const hash1 = computeRecordHash(body1);

      const prevHash = hash1;
      const body2 = buildRawBody({ monotonicCtr: 2, timestampMs: ts, prevHash });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      const hash2 = computeRecordHash(body2);

      // Set interval to 2 blocks
      await oracle.setMinUpdateInterval(2);

      // First update at block N
      await oracle.updateSkillRating(deviceId, hash1, 0x20, 200);

      // Mine an extra block to satisfy the 2-block interval (now at block N+2)
      await network.provider.send("evm_mine");

      // Second update at block N+2: nextAllowed = N+2, N+2 >= N+2 → passes
      await expect(
        oracle.updateSkillRating(deviceId, hash2, 0x20, 200)
      ).to.not.be.reverted;
    });

    it("should not rate-limit updates for different devices", async function () {
      const pubkey2 = "0x04" + "ee".repeat(32) + "ff".repeat(32);
      const did2 = ethers.keccak256(pubkey2);

      await registerDevice();
      await registry.registerDevice(pubkey2, { value: MIN_DEPOSIT });

      const ts = await currentBlockTimestampMs();
      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      const body2 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + "ab".repeat(32),
      });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      await verifier.verifyPoAC(did2, body2, FAKE_SIG);
      const hash1 = computeRecordHash(body1);
      const hash2 = computeRecordHash(body2);

      // Set interval to 5 blocks
      await oracle.setMinUpdateInterval(5);

      // device1 update (block N)
      await oracle.updateSkillRating(deviceId, hash1, 0x20, 200);

      // device2 update (block N+1) — DIFFERENT device, rate limit is per-device
      await expect(
        oracle.updateSkillRating(did2, hash2, 0x20, 200)
      ).to.not.be.reverted;
    });

    it("should allow update when minUpdateInterval = 0 (rate limit disabled)", async function () {
      await registerDevice();
      const ts = await currentBlockTimestampMs();

      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const hash1 = computeRecordHash(body1);

      const prevHash = hash1;
      const body2 = buildRawBody({ monotonicCtr: 2, timestampMs: ts, prevHash });
      await verifier.verifyPoAC(deviceId, body2, FAKE_SIG);
      const hash2 = computeRecordHash(body2);

      // Disable rate limiting
      await oracle.setMinUpdateInterval(0);

      await oracle.updateSkillRating(deviceId, hash1, 0x20, 200);
      // Immediate second update in next block: passes because minUpdateInterval = 0
      await expect(
        oracle.updateSkillRating(deviceId, hash2, 0x20, 200)
      ).to.not.be.reverted;
    });
  });

  // =========================================================================
  //  14. Multiple devices independent
  // =========================================================================
  describe("Multiple devices", function () {
    it("should track independent profiles for different devices", async function () {
      const pubkey2 = "0x04" + "dd".repeat(32) + "ee".repeat(32);
      const did2 = ethers.keccak256(pubkey2);

      await registerDevice();
      await registry.registerDevice(pubkey2, { value: MIN_DEPOSIT });

      const ts = await currentBlockTimestampMs();

      // Device 1: NOMINAL
      const body1 = buildRawBody({ monotonicCtr: 1, timestampMs: ts, inferenceResult: 0x20, confidence: 255 });
      await verifier.verifyPoAC(deviceId, body1, FAKE_SIG);
      const hash1 = computeRecordHash(body1);
      await oracle.updateSkillRating(deviceId, hash1, 0x20, 255);

      // Device 2: SKILLED
      const body2 = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        inferenceResult: 0x21,
        confidence: 255,
        sensorCommitment: "0x" + "ab".repeat(32),
      });
      await verifier.verifyPoAC(did2, body2, FAKE_SIG);
      const hash2 = computeRecordHash(body2);
      await oracle.updateSkillRating(did2, hash2, 0x21, 255);

      const profile1 = await oracle.getSkillProfile(deviceId);
      const profile2 = await oracle.getSkillProfile(did2);

      expect(profile1.rating).to.equal(1005); // 1000 + 5
      expect(profile2.rating).to.equal(1012); // 1000 + 12
      expect(await oracle.totalProfileCount()).to.equal(2);
    });
  });
});
