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
//  Helper: Compute Merkle root matching the contract's algorithm
//  (sort leaves lexicographically, pairwise keccak256, promote odd leaf)
// ---------------------------------------------------------------------------
function computeMerkleRoot(leaves) {
  if (leaves.length === 1) return leaves[0];

  // Sort lexicographically (as hex strings)
  let sorted = [...leaves].sort();

  let n = sorted.length;
  while (n > 1) {
    const newN = Math.ceil(n / 2);
    const next = [];
    for (let i = 0; i < newN; i++) {
      const left = i * 2;
      const right = left + 1;
      if (right < n) {
        next.push(
          ethers.keccak256(
            ethers.concat([sorted[left], sorted[right]])
          )
        );
      } else {
        next.push(sorted[left]); // Promote odd leaf
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
describe("TeamProofAggregator", function () {
  let owner, alice, bob;
  let registry, verifier, aggregator;

  // Create 4 device pubkeys + deviceIds
  const devicePubkeys = [];
  const deviceIds = [];
  for (let i = 1; i <= 6; i++) {
    const hex = i.toString(16).padStart(2, "0");
    const pk = "0x04" + hex.repeat(32) + (i + 0x10).toString(16).padStart(2, "0").repeat(32);
    devicePubkeys.push(pk);
    deviceIds.push(ethers.keccak256(pk));
  }

  beforeEach(async function () {
    [owner, alice, bob] = await ethers.getSigners();

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

    const AggFactory = await ethers.getContractFactory("TeamProofAggregator");
    aggregator = await AggFactory.deploy(await verifier.getAddress());
    await aggregator.waitForDeployment();
  });

  // Register N devices and submit one PoAC record each, returning hashes
  async function registerAndSubmitRecords(count) {
    const ts = await currentBlockTimestampMs();
    const recordHashes = [];

    for (let i = 0; i < count; i++) {
      await registry.registerDevice(devicePubkeys[i], { value: MIN_DEPOSIT });

      const body = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        sensorCommitment: "0x" + (i + 1).toString(16).padStart(64, "0"),
      });
      await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
      recordHashes.push(computeRecordHash(body));
    }

    return recordHashes;
  }

  const TEAM_ID = ethers.keccak256(ethers.toUtf8Bytes("Team Alpha"));

  // =========================================================================
  //  1. Deployment
  // =========================================================================
  describe("Deployment", function () {
    it("should deploy with correct PoACVerifier reference", async function () {
      expect(await aggregator.poACVerifier()).to.equal(await verifier.getAddress());
    });

    it("should set deployer as owner", async function () {
      expect(await aggregator.owner()).to.equal(owner.address);
    });

    it("should start with zero proofs", async function () {
      expect(await aggregator.proofCount()).to.equal(0);
    });
  });

  // =========================================================================
  //  2. Constants
  // =========================================================================
  describe("Constants", function () {
    it("MIN_TEAM_SIZE should be 2", async function () {
      expect(await aggregator.MIN_TEAM_SIZE()).to.equal(2);
    });

    it("MAX_TEAM_SIZE should be 6", async function () {
      expect(await aggregator.MAX_TEAM_SIZE()).to.equal(6);
    });
  });

  // =========================================================================
  //  3. Team creation
  // =========================================================================
  describe("Team creation", function () {
    it("should create a team with 2 members", async function () {
      const members = deviceIds.slice(0, 2);
      const tx = await aggregator.createTeam(TEAM_ID, members);

      await expect(tx)
        .to.emit(aggregator, "TeamCreated")
        .withArgs(TEAM_ID, owner.address, 2);

      expect(await aggregator.teamExists(TEAM_ID)).to.be.true;
    });

    it("should create a team with 6 members (max)", async function () {
      const members = deviceIds.slice(0, 6);
      await expect(aggregator.createTeam(TEAM_ID, members)).to.not.be.reverted;
    });

    it("should store correct team data", async function () {
      const members = deviceIds.slice(0, 3);
      await aggregator.createTeam(TEAM_ID, members);

      const [memberDeviceIds, captain, createdAt, active] =
        await aggregator.getTeam(TEAM_ID);

      expect(memberDeviceIds.length).to.equal(3);
      expect(captain).to.equal(owner.address);
      expect(createdAt).to.be.gt(0);
      expect(active).to.be.true;
    });

    it("should revert with TeamAlreadyExists on duplicate", async function () {
      const members = deviceIds.slice(0, 2);
      await aggregator.createTeam(TEAM_ID, members);

      await expect(
        aggregator.createTeam(TEAM_ID, members)
      ).to.be.revertedWithCustomError(aggregator, "TeamAlreadyExists");
    });

    it("should revert with InvalidTeamSize for 1 member", async function () {
      await expect(
        aggregator.createTeam(TEAM_ID, [deviceIds[0]])
      ).to.be.revertedWithCustomError(aggregator, "InvalidTeamSize");
    });

    it("should revert with InvalidTeamSize for 7+ members", async function () {
      // Create 7 device IDs
      const sevenIds = [
        ...deviceIds,
        ethers.keccak256("0x04" + "ff".repeat(64)),
      ];
      await expect(
        aggregator.createTeam(TEAM_ID, sevenIds)
      ).to.be.revertedWithCustomError(aggregator, "InvalidTeamSize");
    });

    it("should revert with InvalidTeamSize for 0 members", async function () {
      await expect(
        aggregator.createTeam(TEAM_ID, [])
      ).to.be.revertedWithCustomError(aggregator, "InvalidTeamSize");
    });
  });

  // =========================================================================
  //  4. Team proof submission
  // =========================================================================
  describe("Team proof submission", function () {
    it("should submit a valid team proof for 2 members", async function () {
      const memberCount = 2;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);

      const merkleRoot = computeMerkleRoot(recordHashes);

      const tx = await aggregator.submitTeamProof(
        TEAM_ID, recordHashes, merkleRoot
      );

      await expect(tx)
        .to.emit(aggregator, "TeamProofSubmitted")
        .withArgs(TEAM_ID, 0, merkleRoot, memberCount, true);

      expect(await aggregator.proofCount()).to.equal(1);
    });

    it("should submit a valid team proof for 4 members", async function () {
      const memberCount = 4;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);

      const merkleRoot = computeMerkleRoot(recordHashes);

      await expect(
        aggregator.submitTeamProof(TEAM_ID, recordHashes, merkleRoot)
      ).to.not.be.reverted;
    });

    it("should store correct proof data", async function () {
      const memberCount = 3;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);
      const merkleRoot = computeMerkleRoot(recordHashes);

      await aggregator.submitTeamProof(TEAM_ID, recordHashes, merkleRoot);

      const proof = await aggregator.getTeamProof(0);
      expect(proof.teamId).to.equal(TEAM_ID);
      expect(proof.merkleRoot).to.equal(merkleRoot);
      expect(proof.memberCount).to.equal(memberCount);
      expect(proof.allClean).to.be.true;
      expect(proof.submitter).to.equal(owner.address);
      expect(proof.submittedAt).to.be.gt(0);
      expect(proof.recordHashes.length).to.equal(memberCount);
    });
  });

  // =========================================================================
  //  5. Proof submission rejections
  // =========================================================================
  describe("Proof submission rejections", function () {
    it("should revert with TeamNotFound for unknown team", async function () {
      const fakeTeamId = ethers.keccak256(ethers.toUtf8Bytes("Unknown"));
      await expect(
        aggregator.submitTeamProof(fakeTeamId, [], ethers.ZeroHash)
      ).to.be.revertedWithCustomError(aggregator, "TeamNotFound");
    });

    it("should revert with MemberCountMismatch for wrong number of records", async function () {
      const memberCount = 3;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);

      // Submit only 2 of 3 hashes
      await expect(
        aggregator.submitTeamProof(
          TEAM_ID, recordHashes.slice(0, 2), ethers.ZeroHash
        )
      ).to.be.revertedWithCustomError(aggregator, "MemberCountMismatch");
    });

    it("should revert with RecordNotVerified for unverified hash", async function () {
      const members = deviceIds.slice(0, 2);
      await aggregator.createTeam(TEAM_ID, members);

      // Use fake record hashes that aren't in the PoACVerifier
      const fakeHashes = [
        ethers.keccak256("0x01"),
        ethers.keccak256("0x02"),
      ];

      await expect(
        aggregator.submitTeamProof(TEAM_ID, fakeHashes, ethers.ZeroHash)
      ).to.be.revertedWithCustomError(aggregator, "RecordNotVerified");
    });

    it("should revert with InvalidMerkleRoot for wrong root", async function () {
      const memberCount = 2;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);

      const wrongRoot = ethers.keccak256("0xbadbadbadbadbad0");

      await expect(
        aggregator.submitTeamProof(TEAM_ID, recordHashes, wrongRoot)
      ).to.be.revertedWithCustomError(aggregator, "InvalidMerkleRoot");
    });
  });

  // =========================================================================
  //  6. Merkle root computation
  // =========================================================================
  describe("Merkle root computation", function () {
    it("should match off-chain computation for 2 leaves", async function () {
      const leaves = [ethers.keccak256("0x01"), ethers.keccak256("0x02")];
      const expectedRoot = computeMerkleRoot(leaves);

      const contractRoot = await aggregator.computeMerkleRoot(leaves);
      expect(contractRoot).to.equal(expectedRoot);
    });

    it("should match off-chain computation for 3 leaves (odd)", async function () {
      const leaves = [
        ethers.keccak256("0x01"),
        ethers.keccak256("0x02"),
        ethers.keccak256("0x03"),
      ];
      const expectedRoot = computeMerkleRoot(leaves);

      const contractRoot = await aggregator.computeMerkleRoot(leaves);
      expect(contractRoot).to.equal(expectedRoot);
    });

    it("should match off-chain computation for 4 leaves", async function () {
      const leaves = [
        ethers.keccak256("0x01"),
        ethers.keccak256("0x02"),
        ethers.keccak256("0x03"),
        ethers.keccak256("0x04"),
      ];
      const expectedRoot = computeMerkleRoot(leaves);

      const contractRoot = await aggregator.computeMerkleRoot(leaves);
      expect(contractRoot).to.equal(expectedRoot);
    });

    it("should match for 5 leaves (odd)", async function () {
      const leaves = [
        ethers.keccak256("0x01"),
        ethers.keccak256("0x02"),
        ethers.keccak256("0x03"),
        ethers.keccak256("0x04"),
        ethers.keccak256("0x05"),
      ];
      const expectedRoot = computeMerkleRoot(leaves);

      const contractRoot = await aggregator.computeMerkleRoot(leaves);
      expect(contractRoot).to.equal(expectedRoot);
    });

    it("should match for 6 leaves (max team size)", async function () {
      const leaves = [
        ethers.keccak256("0x01"),
        ethers.keccak256("0x02"),
        ethers.keccak256("0x03"),
        ethers.keccak256("0x04"),
        ethers.keccak256("0x05"),
        ethers.keccak256("0x06"),
      ];
      const expectedRoot = computeMerkleRoot(leaves);

      const contractRoot = await aggregator.computeMerkleRoot(leaves);
      expect(contractRoot).to.equal(expectedRoot);
    });

    it("should return the leaf itself for 1 leaf", async function () {
      const leaf = ethers.keccak256("0x01");
      const contractRoot = await aggregator.computeMerkleRoot([leaf]);
      expect(contractRoot).to.equal(leaf);
    });
  });

  // =========================================================================
  //  7. View functions
  // =========================================================================
  describe("View functions", function () {
    it("getTeamProofCount should return correct count", async function () {
      const memberCount = 2;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);
      expect(await aggregator.getTeamProofCount(TEAM_ID)).to.equal(0);

      const merkleRoot = computeMerkleRoot(recordHashes);
      await aggregator.submitTeamProof(TEAM_ID, recordHashes, merkleRoot);

      expect(await aggregator.getTeamProofCount(TEAM_ID)).to.equal(1);
    });

    it("getTeamProofIds should return correct IDs", async function () {
      const memberCount = 2;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);
      const merkleRoot = computeMerkleRoot(recordHashes);
      await aggregator.submitTeamProof(TEAM_ID, recordHashes, merkleRoot);

      const ids = await aggregator.getTeamProofIds(TEAM_ID);
      expect(ids.length).to.equal(1);
      expect(ids[0]).to.equal(0);
    });
  });

  // =========================================================================
  //  8. Multiple proofs for same team
  // =========================================================================
  describe("Multiple proofs", function () {
    it("should allow multiple proofs for the same team (different records)", async function () {
      // Register 2 devices
      const memberCount = 2;
      const members = deviceIds.slice(0, memberCount);

      // Register devices
      for (let i = 0; i < memberCount; i++) {
        await registry.registerDevice(devicePubkeys[i], { value: MIN_DEPOSIT });
      }

      await aggregator.createTeam(TEAM_ID, members);

      const ts = await currentBlockTimestampMs();

      // Submit first batch of records
      const hashes1 = [];
      for (let i = 0; i < memberCount; i++) {
        const body = buildRawBody({
          monotonicCtr: 1,
          timestampMs: ts,
          sensorCommitment: "0x" + (i + 1).toString(16).padStart(64, "0"),
        });
        await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
        hashes1.push(computeRecordHash(body));
      }

      const root1 = computeMerkleRoot(hashes1);
      await aggregator.submitTeamProof(TEAM_ID, hashes1, root1);

      // Submit second batch of records (counter = 2, needs chain link)
      const hashes2 = [];
      for (let i = 0; i < memberCount; i++) {
        const body = buildRawBody({
          monotonicCtr: 2,
          timestampMs: ts,
          prevHash: hashes1[i],
          sensorCommitment: "0x" + (i + 10).toString(16).padStart(64, "0"),
        });
        await verifier.verifyPoAC(deviceIds[i], body, FAKE_SIG);
        hashes2.push(computeRecordHash(body));
      }

      const root2 = computeMerkleRoot(hashes2);
      await aggregator.submitTeamProof(TEAM_ID, hashes2, root2);

      expect(await aggregator.proofCount()).to.equal(2);
      expect(await aggregator.getTeamProofCount(TEAM_ID)).to.equal(2);
    });
  });

  // =========================================================================
  //  9. Any caller can submit proof
  // =========================================================================
  describe("Permissionless submission", function () {
    it("should allow non-captain to submit proof", async function () {
      const memberCount = 2;
      const recordHashes = await registerAndSubmitRecords(memberCount);
      const members = deviceIds.slice(0, memberCount);

      await aggregator.createTeam(TEAM_ID, members);
      const merkleRoot = computeMerkleRoot(recordHashes);

      // Alice (not team captain) submits
      await expect(
        aggregator.connect(alice).submitTeamProof(TEAM_ID, recordHashes, merkleRoot)
      ).to.not.be.reverted;

      const proof = await aggregator.getTeamProof(0);
      expect(proof.submitter).to.equal(alice.address);
    });
  });

  // =========================================================================
  //  10. Cheat Flag Enforcement
  // =========================================================================
  describe("Cheat Flag Enforcement", function () {
    const CHEAT_TEAM_ID = ethers.keccak256(ethers.toUtf8Bytes("Cheat Test Team"));

    async function registerDeviceWithCheatRecord(index, inferenceCode) {
      const ts = await currentBlockTimestampMs();
      await registry.registerDevice(devicePubkeys[index], { value: MIN_DEPOSIT });
      const body = buildRawBody({
        monotonicCtr: 1,
        timestampMs: ts,
        inferenceResult: inferenceCode,
        sensorCommitment: "0x" + (index + 0xf0).toString(16).padStart(64, "0"),
      });
      await verifier.verifyPoAC(deviceIds[index], body, FAKE_SIG);
      return computeRecordHash(body);
    }

    it("submitTeamProof succeeds when all records have clean inference (0x00)", async function () {
      const hash0 = await registerDeviceWithCheatRecord(0, 0x00);
      const hash1 = await registerDeviceWithCheatRecord(1, 0x00);
      await aggregator.createTeam(CHEAT_TEAM_ID, [deviceIds[0], deviceIds[1]]);
      const root = computeMerkleRoot([hash0, hash1]);
      await expect(
        aggregator.submitTeamProof(CHEAT_TEAM_ID, [hash0, hash1], root)
      ).to.not.be.reverted;
    });

    it("submitTeamProof reverts CheatFlagDetected for inference 0x28 (DRIVER_INJECT)", async function () {
      const hash0 = await registerDeviceWithCheatRecord(0, 0x00);
      const hash1 = await registerDeviceWithCheatRecord(1, 0x28);
      await aggregator.createTeam(CHEAT_TEAM_ID, [deviceIds[0], deviceIds[1]]);
      const root = computeMerkleRoot([hash0, hash1]);
      await expect(
        aggregator.submitTeamProof(CHEAT_TEAM_ID, [hash0, hash1], root)
      ).to.be.revertedWithCustomError(aggregator, "CheatFlagDetected");
    });

    it("submitTeamProof reverts CheatFlagDetected for inference 0x29 (WALLHACK_PREAIM)", async function () {
      const hash0 = await registerDeviceWithCheatRecord(0, 0x00);
      const hash1 = await registerDeviceWithCheatRecord(1, 0x29);
      await aggregator.createTeam(CHEAT_TEAM_ID, [deviceIds[0], deviceIds[1]]);
      const root = computeMerkleRoot([hash0, hash1]);
      await expect(
        aggregator.submitTeamProof(CHEAT_TEAM_ID, [hash0, hash1], root)
      ).to.be.revertedWithCustomError(aggregator, "CheatFlagDetected");
    });

    it("submitTeamProof reverts CheatFlagDetected for inference 0x2A (AIMBOT_BEHAVIORAL)", async function () {
      const hash0 = await registerDeviceWithCheatRecord(0, 0x00);
      const hash1 = await registerDeviceWithCheatRecord(1, 0x2a);
      await aggregator.createTeam(CHEAT_TEAM_ID, [deviceIds[0], deviceIds[1]]);
      const root = computeMerkleRoot([hash0, hash1]);
      await expect(
        aggregator.submitTeamProof(CHEAT_TEAM_ID, [hash0, hash1], root)
      ).to.be.revertedWithCustomError(aggregator, "CheatFlagDetected");
    });

    it("stored proof.allClean is true for successfully submitted clean proof", async function () {
      const hash0 = await registerDeviceWithCheatRecord(0, 0x00);
      const hash1 = await registerDeviceWithCheatRecord(1, 0x00);
      await aggregator.createTeam(CHEAT_TEAM_ID, [deviceIds[0], deviceIds[1]]);
      const root = computeMerkleRoot([hash0, hash1]);
      await aggregator.submitTeamProof(CHEAT_TEAM_ID, [hash0, hash1], root);
      const proof = await aggregator.getTeamProof(0);
      expect(proof.allClean).to.equal(true);
    });
  });
});
