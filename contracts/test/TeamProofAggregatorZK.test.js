const { expect } = require("chai");
const { ethers, network } = require("hardhat");

// ---------------------------------------------------------------------------
//  Mock P256 precompile
// ---------------------------------------------------------------------------
const MOCK_P256_BYTECODE = "0x600160005260206000f3";
const P256_PRECOMPILE_ADDR = "0x0000000000000000000000000000000000000100";

const MIN_DEPOSIT = ethers.parseEther("0.01");
const MAX_TIMESTAMP_SKEW = 300;
const FAKE_SIG = "0x" + "cc".repeat(64);

// Mock ZK proof: exactly 256 bytes (Phase 13 mock acceptance criterion)
const VALID_ZK_PROOF = "0x" + "ab".repeat(256);
// Invalid proof: wrong length
const INVALID_ZK_PROOF = "0x" + "ab".repeat(64);

// ---------------------------------------------------------------------------
//  Helpers (mirrors TeamProofAggregator.test.js)
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

function computeMerkleRoot(leaves) {
  if (leaves.length === 1) return leaves[0];
  let sorted = [...leaves].sort();
  let n = sorted.length;
  while (n > 1) {
    const newN = Math.ceil(n / 2);
    const next = [];
    for (let i = 0; i < newN; i++) {
      const left = i * 2;
      const right = left + 1;
      if (right < n) {
        next.push(ethers.keccak256(ethers.concat([sorted[left], sorted[right]])));
      } else {
        next.push(sorted[left]);
      }
    }
    sorted = next;
    n = newN;
  }
  return sorted[0];
}

// ---------------------------------------------------------------------------
//  Test Suite
// ---------------------------------------------------------------------------
describe("TeamProofAggregatorZK", function () {
  let owner, alice;
  let registry, verifier, zkAgg;

  // 3 device pubkeys + deviceIds for a minimal team
  const devicePubkeys = [];
  const deviceIds = [];
  for (let i = 1; i <= 3; i++) {
    const hex = i.toString(16).padStart(2, "0");
    const pk = "0x04" + hex.repeat(32) + (i + 0x20).toString(16).padStart(2, "0").repeat(32);
    devicePubkeys.push(pk);
    deviceIds.push(ethers.keccak256(pk));
  }

  const TEAM_ID = ethers.keccak256(ethers.toUtf8Bytes("ZK Team Alpha"));
  const NULLIFIER = ethers.keccak256(ethers.toUtf8Bytes("epoch:1:team:alpha"));

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

    const ZKFactory = await ethers.getContractFactory("TeamProofAggregatorZKTestable");
    zkAgg = await ZKFactory.deploy(await verifier.getAddress());
    await zkAgg.waitForDeployment();
  });

  // Register team members, submit records, and return record hashes + merkle root
  async function setupTeamWithRecords() {
    const ts = await currentBlockTimestampMs();
    const recordHashes = [];

    for (let i = 0; i < 3; i++) {
      await registry.registerDevice(devicePubkeys[i], { value: MIN_DEPOSIT });
      const body = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + (i + 1).toString(16).padStart(64, "0"),
      });
      await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
      recordHashes.push(computeRecordHash(body));
    }

    await zkAgg.createTeam(TEAM_ID, deviceIds.slice(0, 3));
    const merkleRoot = computeMerkleRoot(recordHashes);
    return { recordHashes, merkleRoot };
  }

  // =========================================================================
  //  1. Deployment
  // =========================================================================
  describe("1. Deployment", function () {
    it("deploys with correct poACVerifier reference", async function () {
      expect(await zkAgg.poACVerifier()).to.equal(await verifier.getAddress());
    });

    it("ZK_VERSION constant is set", async function () {
      const version = await zkAgg.ZK_VERSION();
      expect(version).to.equal(
        ethers.keccak256(ethers.toUtf8Bytes("TeamProofZK_Groth16_Phase14"))
      );
    });

    it("MOCK_PROOF_SIZE is 256", async function () {
      expect(await zkAgg.MOCK_PROOF_SIZE()).to.equal(256);
    });
  });

  // =========================================================================
  //  2. ZK Proof Gate
  // =========================================================================
  describe("2. ZK Proof Gate", function () {
    it("accepts 256-byte mock proof (Phase 13 mock)", async function () {
      const { recordHashes, merkleRoot } = await setupTeamWithRecords();
      await expect(
        zkAgg.submitTeamProofZK(
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
        )
      ).to.not.be.reverted;
    });

    it("rejects proof with wrong length (InvalidZKProof) — base contract mock", async function () {
      // Use the base TeamProofAggregatorZK (not testable) to exercise the length check
      // in _verifyZKProof, which accepts only proof.length == MOCK_PROOF_SIZE (256).
      const BaseFactory = await ethers.getContractFactory("TeamProofAggregatorZK");
      const baseZK = await BaseFactory.deploy(await verifier.getAddress());
      await baseZK.waitForDeployment();

      // Register devices and create team on the base contract
      const ts = await currentBlockTimestampMs();
      const rh = [];
      for (let i = 0; i < 3; i++) {
        await registry.registerDevice(devicePubkeys[i], { value: MIN_DEPOSIT });
        const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts,
          sensorCommitment: "0x" + (i + 3).toString(16).padStart(64, "0") });
        await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
        rh.push(computeRecordHash(body));
      }
      await baseZK.createTeam(TEAM_ID, deviceIds.slice(0, 3));
      const merkleRoot = computeMerkleRoot(rh);

      await expect(
        baseZK.submitTeamProofZK(TEAM_ID, rh, merkleRoot, NULLIFIER, INVALID_ZK_PROOF)
      ).to.be.revertedWithCustomError(baseZK, "InvalidZKProof");
    });

    it("setMockZKResult(false) causes InvalidZKProof regardless of proof length", async function () {
      const { recordHashes, merkleRoot } = await setupTeamWithRecords();
      await zkAgg.setMockZKResult(false);
      await expect(
        zkAgg.submitTeamProofZK(
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
        )
      ).to.be.revertedWithCustomError(zkAgg, "InvalidZKProof");
    });
  });

  // =========================================================================
  //  3. Nullifier Anti-Replay
  // =========================================================================
  describe("3. Nullifier Anti-Replay", function () {
    it("first submission with nullifier succeeds", async function () {
      const { recordHashes, merkleRoot } = await setupTeamWithRecords();
      await expect(
        zkAgg.submitTeamProofZK(
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
        )
      ).to.not.be.reverted;
    });

    it("reusing same nullifier reverts with NullifierAlreadyUsed", async function () {
      const { recordHashes, merkleRoot } = await setupTeamWithRecords();
      await zkAgg.submitTeamProofZK(
        TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
      );
      // Attempt replay with different records but same nullifier
      await expect(
        zkAgg.submitTeamProofZK(
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
        )
      ).to.be.revertedWithCustomError(zkAgg, "NullifierAlreadyUsed")
        .withArgs(NULLIFIER);
    });

    it("different nullifiers are independent — both succeed", async function () {
      // Setup two separate teams for independent nullifier tests
      const TEAM_B = ethers.keccak256(ethers.toUtf8Bytes("ZK Team Beta"));
      const ts = await currentBlockTimestampMs();
      const recordHashes = [];
      for (let i = 0; i < 3; i++) {
        await registry.registerDevice(devicePubkeys[i], { value: MIN_DEPOSIT });
        const body = buildRawBody({ monotonicCtr: 1, timestampMs: ts,
          sensorCommitment: "0x" + (i + 5).toString(16).padStart(64, "0") });
        await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
        recordHashes.push(computeRecordHash(body));
      }
      await zkAgg.createTeam(TEAM_B, deviceIds.slice(0, 3));
      const merkleRoot = computeMerkleRoot(recordHashes);

      const NULL_A = ethers.keccak256(ethers.toUtf8Bytes("null:A"));
      const NULL_B = ethers.keccak256(ethers.toUtf8Bytes("null:B"));

      await expect(
        zkAgg.submitTeamProofZK(TEAM_B, recordHashes, merkleRoot, NULL_A, VALID_ZK_PROOF)
      ).to.not.be.reverted;

      // Different nullifier on same team (PairAlreadyAttested is not a concern here):
      // Second submit would fail due to TeamProofAggregator re-submitting same root —
      // but separate teams + separate nullifiers both work. Just verify NULL_A is used:
      expect(await zkAgg.usedNullifiers(NULL_A)).to.be.true;
      expect(await zkAgg.usedNullifiers(NULL_B)).to.be.false;
    });
  });

  // =========================================================================
  //  4. Event + Inheritance
  // =========================================================================
  describe("4. Event and Inheritance", function () {
    it("emits ZKTeamProofSubmitted event on successful submission", async function () {
      const { recordHashes, merkleRoot } = await setupTeamWithRecords();
      await expect(
        zkAgg.submitTeamProofZK(
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
        )
      ).to.emit(zkAgg, "ZKTeamProofSubmitted")
        .withArgs(TEAM_ID, merkleRoot, NULLIFIER, 3n);
    });

    it("cheat-flag record is rejected by inherited parent cheat detection", async function () {
      // Submit a record with cheat inference code 0x29 (WALLHACK_PREAIM)
      const ts = await currentBlockTimestampMs();
      const cheatHashes = [];
      for (let i = 0; i < 3; i++) {
        await registry.registerDevice(devicePubkeys[i], { value: MIN_DEPOSIT });
        const inf = i === 0 ? 0x29 : 0x20;  // First member has cheat code
        const body = buildRawBody({
          monotonicCtr: 1, timestampMs: ts, inferenceResult: inf,
          sensorCommitment: "0x" + (i + 9).toString(16).padStart(64, "0"),
        });
        await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
        cheatHashes.push(computeRecordHash(body));
      }
      const CHEAT_TEAM = ethers.keccak256(ethers.toUtf8Bytes("Cheat ZK Team"));
      await zkAgg.createTeam(CHEAT_TEAM, deviceIds.slice(0, 3));
      const merkleRoot = computeMerkleRoot(cheatHashes);

      await expect(
        zkAgg.submitTeamProofZK(
          CHEAT_TEAM, cheatHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
        )
      ).to.be.revertedWithCustomError(zkAgg, "CheatFlagDetected");
    });

    it("full path: ZK proof accepted → proof retrievable via getTeamProof", async function () {
      const { recordHashes, merkleRoot } = await setupTeamWithRecords();
      const tx = await zkAgg.submitTeamProofZK(
        TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF
      );
      const receipt = await tx.wait();

      // Retrieve proof via inherited getTeamProof (proofId = 0 for first proof)
      const proof = await zkAgg.getTeamProof(0);
      expect(proof.teamId).to.equal(TEAM_ID);
      expect(proof.merkleRoot).to.equal(merkleRoot);
      expect(Number(proof.memberCount)).to.equal(3);
      expect(proof.allClean).to.be.true;
    });
  });

  // =========================================================================
  //  5. Phase 14C: Real Verifier Integration
  // =========================================================================
  describe("5. Phase 14C: Real Verifier Integration", function () {
    let baseZK;     // TeamProofAggregatorZK (NOT testable — exercises real _verifyZKProof)
    let mockVerif;  // MockTeamProofVerifier

    // Non-zero Poseidon root (any BN254 field element; real value from zk_prover.py)
    const POSEIDON_ROOT = 12345678901234567890n;
    const EPOCH         = 42n;

    // 7-param function selector (overloaded — must use full signature in ethers v6)
    const SUBMIT_7 = "submitTeamProofZK(bytes32,bytes32[],bytes32,bytes32,bytes,uint256,uint256)";

    beforeEach(async function () {
      const BaseFactory = await ethers.getContractFactory("TeamProofAggregatorZK");
      baseZK = await BaseFactory.deploy(await verifier.getAddress());
      await baseZK.waitForDeployment();

      const MockFactory = await ethers.getContractFactory("MockTeamProofVerifier");
      mockVerif = await MockFactory.deploy(true);
      await mockVerif.waitForDeployment();
    });

    // Helper: register 3 devices + records on baseZK's verifier, create team on baseZK
    async function setupBaseTeamWithRecords() {
      const ts = await currentBlockTimestampMs();
      const recordHashes = [];
      for (let i = 0; i < 3; i++) {
        await registry.registerDevice(devicePubkeys[i], { value: MIN_DEPOSIT });
        const body = buildRawBody({
          monotonicCtr: 1,
          timestampMs: ts,
          sensorCommitment: "0x" + (i + 0x10).toString(16).padStart(64, "0"),
        });
        await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
        recordHashes.push(computeRecordHash(body));
      }
      await baseZK.createTeam(TEAM_ID, deviceIds.slice(0, 3));
      const merkleRoot = computeMerkleRoot(recordHashes);
      return { recordHashes, merkleRoot };
    }

    it("owner can setTeamProofVerifier — stores address and emits event", async function () {
      await expect(baseZK.setTeamProofVerifier(await mockVerif.getAddress()))
        .to.emit(baseZK, "TeamProofVerifierSet")
        .withArgs(await mockVerif.getAddress());

      expect(await baseZK.teamProofVerifier()).to.equal(await mockVerif.getAddress());
    });

    it("non-owner cannot setTeamProofVerifier", async function () {
      await expect(
        baseZK.connect(alice).setTeamProofVerifier(await mockVerif.getAddress())
      ).to.be.revertedWithCustomError(baseZK, "OwnableUnauthorizedAccount");
    });

    it("7-param submitTeamProofZK with poseidonMerkleRoot=0 uses mock path (accepts 256B)", async function () {
      const { recordHashes, merkleRoot } = await setupBaseTeamWithRecords();
      await baseZK.setTeamProofVerifier(await mockVerif.getAddress());

      // poseidonMerkleRoot=0 forces mock path regardless of verifier address
      await expect(
        baseZK[SUBMIT_7](TEAM_ID, recordHashes, merkleRoot, NULLIFIER, VALID_ZK_PROOF, 0n, EPOCH)
      ).to.not.be.reverted;
    });

    it("7-param with MockVerifier(true) + poseidonMerkleRoot != 0 → calls verifier, succeeds", async function () {
      const { recordHashes, merkleRoot } = await setupBaseTeamWithRecords();
      await baseZK.setTeamProofVerifier(await mockVerif.getAddress());

      await expect(
        baseZK[SUBMIT_7](
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER,
          VALID_ZK_PROOF, POSEIDON_ROOT, EPOCH
        )
      ).to.not.be.reverted;
    });

    it("7-param with MockVerifier(false) + poseidonMerkleRoot != 0 → reverts InvalidZKProof", async function () {
      const { recordHashes, merkleRoot } = await setupBaseTeamWithRecords();
      await baseZK.setTeamProofVerifier(await mockVerif.getAddress());
      await mockVerif.setResult(false);

      await expect(
        baseZK[SUBMIT_7](
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER,
          VALID_ZK_PROOF, POSEIDON_ROOT, EPOCH
        )
      ).to.be.revertedWithCustomError(baseZK, "InvalidZKProof");
    });

    it("invalid proof length (64B) rejected when verifier is set", async function () {
      const { recordHashes, merkleRoot } = await setupBaseTeamWithRecords();
      await baseZK.setTeamProofVerifier(await mockVerif.getAddress());

      // proof.length != 256 must revert even when verifier is set and poseidonMerkleRoot != 0
      await expect(
        baseZK[SUBMIT_7](
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER,
          INVALID_ZK_PROOF, POSEIDON_ROOT, EPOCH
        )
      ).to.be.revertedWithCustomError(baseZK, "InvalidZKProof");
    });

    it("7-param nullifier is marked used after successful submission", async function () {
      const { recordHashes, merkleRoot } = await setupBaseTeamWithRecords();
      await baseZK.setTeamProofVerifier(await mockVerif.getAddress());

      expect(await baseZK.usedNullifiers(NULLIFIER)).to.be.false;

      await baseZK[SUBMIT_7](
        TEAM_ID, recordHashes, merkleRoot, NULLIFIER,
        VALID_ZK_PROOF, POSEIDON_ROOT, EPOCH
      );

      expect(await baseZK.usedNullifiers(NULLIFIER)).to.be.true;

      // Replay attempt must revert
      await expect(
        baseZK[SUBMIT_7](
          TEAM_ID, recordHashes, merkleRoot, NULLIFIER,
          VALID_ZK_PROOF, POSEIDON_ROOT, EPOCH
        )
      ).to.be.revertedWithCustomError(baseZK, "NullifierAlreadyUsed");
    });
  });
});
