const { expect } = require("chai");
const { ethers, network } = require("hardhat");

// ---------------------------------------------------------------------------
//  Mock P256 precompile — always returns 1 (valid signature)
// ---------------------------------------------------------------------------
const MOCK_P256_BYTECODE = "0x600160005260206000f3";
const P256_PRECOMPILE_ADDR = "0x0000000000000000000000000000000000000100";

// ---------------------------------------------------------------------------
//  Test constants
// ---------------------------------------------------------------------------
const PUBKEY_A = "0x04" + "aa".repeat(32) + "bb".repeat(32);
const PUBKEY_B = "0x04" + "cc".repeat(32) + "dd".repeat(32);
const PUBKEY_C = "0x04" + "ee".repeat(32) + "ff".repeat(32);
const FAKE_SIG = "0x" + "cc".repeat(64);
const MAX_TIMESTAMP_SKEW = 300;

// Testnet tier deposits
const EMULATED_DEPOSIT  = ethers.parseEther("0.1");
const STANDARD_DEPOSIT  = ethers.parseEther("1");
const ATTESTED_DEPOSIT  = ethers.parseEther("0.01");

// Phase 10: ManufacturerKey test constants
const MOCK_P256_REJECT_BYTECODE = "0x600060005260206000f3"; // always returns uint256(0)
const MANUF_ADDR = "0x1234567890123456789012345678901234567890";
const MANUF_X = "0xb7e3c9a1d2f45678901234567890abcdef1234567890abcdef1234567890abcd";
const MANUF_Y = "0xef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab12";
const MANUF_NAME = "Yubico Inc";

// RegistrationTier enum values (must match Solidity enum order)
const TIER_EMULATED = 0;
const TIER_STANDARD = 1;
const TIER_ATTESTED = 2;

// ---------------------------------------------------------------------------
//  buildRawBody — minimal 164-byte PoAC body for integration test
// ---------------------------------------------------------------------------
function buildRawBody({ timestampMs } = {}) {
  const buf = Buffer.alloc(164);
  let offset = 0;
  // 4 x 32-byte hashes
  for (let i = 0; i < 4; i++) { Buffer.alloc(32).copy(buf, offset); offset += 32; }
  buf.writeUInt8(0x20, offset++); // inferenceResult
  buf.writeUInt8(0x01, offset++); // actionCode
  buf.writeUInt8(200,  offset++); // confidence
  buf.writeUInt8(75,   offset++); // batteryPct
  buf.writeUInt32BE(1, offset);   offset += 4; // monotonicCtr
  buf.writeBigInt64BE(BigInt(timestampMs || Date.now()), offset); offset += 8;
  buf.writeDoubleBE(0.0, offset); offset += 8; // latitude
  buf.writeDoubleBE(0.0, offset); offset += 8; // longitude
  buf.writeUInt32BE(0,   offset);              // bountyId
  return "0x" + buf.toString("hex");
}

async function currentBlockTimestampMs() {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp) * 1000n;
}

// ---------------------------------------------------------------------------
//  Test Suite
// ---------------------------------------------------------------------------
describe("TieredDeviceRegistry", function () {
  let owner, alice, bob;
  let registry, verifier;

  beforeEach(async function () {
    [owner, alice, bob] = await ethers.getSigners();

    // Inject mock P256 precompile
    await network.provider.send("hardhat_setCode", [
      P256_PRECOMPILE_ADDR,
      MOCK_P256_BYTECODE,
    ]);

    // Deploy TieredDeviceRegistry
    const RegistryFactory = await ethers.getContractFactory("TieredDeviceRegistry");
    registry = await RegistryFactory.deploy(
      EMULATED_DEPOSIT, STANDARD_DEPOSIT, ATTESTED_DEPOSIT
    );
    await registry.waitForDeployment();

    // Deploy PoACVerifierTestable against TieredDeviceRegistry
    const VerifierFactory = await ethers.getContractFactory("PoACVerifierTestable");
    verifier = await VerifierFactory.deploy(
      await registry.getAddress(),
      MAX_TIMESTAMP_SKEW
    );
    await verifier.waitForDeployment();
    await registry.setReputationUpdater(await verifier.getAddress(), true);
  });

  // =========================================================================
  //  1. Deployment (4 tests)
  // =========================================================================
  describe("Deployment", function () {
    it("deployer is owner", async function () {
      expect(await registry.owner()).to.equal(owner.address);
    });

    it("attestationEnforced defaults to false", async function () {
      expect(await registry.attestationEnforced()).to.be.false;
    });

    it("minimumDeposit equals Standard deposit (backward-compat)", async function () {
      expect(await registry.minimumDeposit()).to.equal(STANDARD_DEPOSIT);
    });

    it("deviceCount starts at 0", async function () {
      expect(await registry.deviceCount()).to.equal(0);
    });
  });

  // =========================================================================
  //  2. Tier config values (6 tests)
  // =========================================================================
  describe("Tier config values", function () {
    it("Emulated tier: depositWei correct", async function () {
      const cfg = await registry.tierConfigs(TIER_EMULATED);
      expect(cfg.depositWei).to.equal(EMULATED_DEPOSIT);
    });

    it("Emulated tier: rewardWeightBps = 0, canClaimBounties = false, canUseSkillOracle = true", async function () {
      const cfg = await registry.tierConfigs(TIER_EMULATED);
      expect(cfg.rewardWeightBps).to.equal(0);
      expect(cfg.canClaimBounties).to.be.false;
      expect(cfg.canUseSkillOracle).to.be.true;
    });

    it("Standard tier: depositWei correct", async function () {
      const cfg = await registry.tierConfigs(TIER_STANDARD);
      expect(cfg.depositWei).to.equal(STANDARD_DEPOSIT);
    });

    it("Standard tier: rewardWeightBps = 5000, canClaimBounties = true, canUseSkillOracle = true", async function () {
      const cfg = await registry.tierConfigs(TIER_STANDARD);
      expect(cfg.rewardWeightBps).to.equal(5000);
      expect(cfg.canClaimBounties).to.be.true;
      expect(cfg.canUseSkillOracle).to.be.true;
    });

    it("Attested tier: depositWei correct", async function () {
      const cfg = await registry.tierConfigs(TIER_ATTESTED);
      expect(cfg.depositWei).to.equal(ATTESTED_DEPOSIT);
    });

    it("Attested tier: rewardWeightBps = 10000, canClaimBounties = true, canUseSkillOracle = true", async function () {
      const cfg = await registry.tierConfigs(TIER_ATTESTED);
      expect(cfg.rewardWeightBps).to.equal(10000);
      expect(cfg.canClaimBounties).to.be.true;
      expect(cfg.canUseSkillOracle).to.be.true;
    });
  });

  // =========================================================================
  //  3. registerDevice backward compat (3 tests)
  // =========================================================================
  describe("registerDevice backward compat", function () {
    it("registerDevice assigns Standard tier", async function () {
      await registry.registerDevice(PUBKEY_A, { value: STANDARD_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      expect(await registry.getDeviceTier(deviceId)).to.equal(TIER_STANDARD);
    });

    it("registerDevice succeeds with Standard deposit", async function () {
      await expect(
        registry.registerDevice(PUBKEY_A, { value: STANDARD_DEPOSIT })
      ).to.not.be.reverted;
      expect(await registry.deviceCount()).to.equal(1);
    });

    it("registerDevice reverts with insufficient deposit", async function () {
      await expect(
        registry.registerDevice(PUBKEY_A, { value: EMULATED_DEPOSIT })
      ).to.be.revertedWithCustomError(registry, "InvalidTierDeposit");
    });
  });

  // =========================================================================
  //  4. registerTieredDevice — Emulated (3 tests)
  // =========================================================================
  describe("registerTieredDevice — Emulated tier", function () {
    it("Emulated deposit succeeds", async function () {
      await expect(
        registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED, { value: EMULATED_DEPOSIT })
      ).to.not.be.reverted;
    });

    it("deviceTiers set to Emulated", async function () {
      await registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED, { value: EMULATED_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      expect(await registry.getDeviceTier(deviceId)).to.equal(TIER_EMULATED);
    });

    it("emits TierRegistered with Emulated tier", async function () {
      const deviceId = ethers.keccak256(PUBKEY_A);
      await expect(
        registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED, { value: EMULATED_DEPOSIT })
      )
        .to.emit(registry, "TierRegistered")
        .withArgs(deviceId, TIER_EMULATED, EMULATED_DEPOSIT);
    });
  });

  // =========================================================================
  //  5. registerTieredDevice — Standard (2 tests)
  // =========================================================================
  describe("registerTieredDevice — Standard tier", function () {
    it("Standard tier assigned correctly", async function () {
      await registry.registerTieredDevice(PUBKEY_A, TIER_STANDARD, { value: STANDARD_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      expect(await registry.getDeviceTier(deviceId)).to.equal(TIER_STANDARD);
    });

    it("emits TierRegistered with Standard tier", async function () {
      const deviceId = ethers.keccak256(PUBKEY_A);
      await expect(
        registry.registerTieredDevice(PUBKEY_A, TIER_STANDARD, { value: STANDARD_DEPOSIT })
      )
        .to.emit(registry, "TierRegistered")
        .withArgs(deviceId, TIER_STANDARD, STANDARD_DEPOSIT);
    });
  });

  // =========================================================================
  //  6. registerTieredDevice — Attested blocked (1 test)
  // =========================================================================
  describe("registerTieredDevice — Attested blocked", function () {
    it("reverts with InvalidTierForFunction when Attested tier is passed", async function () {
      await expect(
        registry.registerTieredDevice(PUBKEY_A, TIER_ATTESTED, { value: ATTESTED_DEPOSIT })
      ).to.be.revertedWithCustomError(registry, "InvalidTierForFunction");
    });
  });

  // =========================================================================
  //  7. registerAttested (4 tests)
  // =========================================================================
  describe("registerAttested", function () {
    const VALID_PROOF = "0x" + "ab".repeat(64); // 64-byte proof

    it("64-byte proof accepted when attestationEnforced=false", async function () {
      await expect(
        registry.registerAttested(PUBKEY_A, VALID_PROOF, { value: ATTESTED_DEPOSIT })
      ).to.not.be.reverted;
    });

    it("tier stored as Attested after registerAttested", async function () {
      await registry.registerAttested(PUBKEY_A, VALID_PROOF, { value: ATTESTED_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      expect(await registry.getDeviceTier(deviceId)).to.equal(TIER_ATTESTED);
    });

    it("reverts InvalidAttestationProof when proof.length != 64", async function () {
      const BAD_PROOF = "0x" + "ab".repeat(32); // 32 bytes — wrong length
      await expect(
        registry.registerAttested(PUBKEY_A, BAD_PROOF, { value: ATTESTED_DEPOSIT })
      ).to.be.revertedWithCustomError(registry, "InvalidAttestationProof");
    });

    it("reverts AttestationValidatorNotImplemented after setAttestationEnforced(true)", async function () {
      await registry.setAttestationEnforced(true);
      await expect(
        registry.registerAttested(PUBKEY_A, VALID_PROOF, { value: ATTESTED_DEPOSIT })
      ).to.be.revertedWithCustomError(registry, "AttestationValidatorNotImplemented");
    });
  });

  // =========================================================================
  //  8. Deposit enforcement (3 tests)
  // =========================================================================
  describe("Deposit enforcement", function () {
    it("Emulated tier: zero deposit fails", async function () {
      await expect(
        registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED, { value: 0 })
      ).to.be.revertedWithCustomError(registry, "InvalidTierDeposit");
    });

    it("Emulated deposit sent to Standard tier fails", async function () {
      // EMULATED_DEPOSIT (0.1) < STANDARD_DEPOSIT (1.0)
      await expect(
        registry.registerTieredDevice(PUBKEY_A, TIER_STANDARD, { value: EMULATED_DEPOSIT })
      ).to.be.revertedWithCustomError(registry, "InvalidTierDeposit");
    });

    it("Attested deposit (0.01) sent to Standard tier fails", async function () {
      // ATTESTED_DEPOSIT (0.01) < STANDARD_DEPOSIT (1.0)
      await expect(
        registry.registerTieredDevice(PUBKEY_A, TIER_STANDARD, { value: ATTESTED_DEPOSIT })
      ).to.be.revertedWithCustomError(registry, "InvalidTierDeposit");
    });
  });

  // =========================================================================
  //  9. View functions (5 tests)
  // =========================================================================
  describe("View functions", function () {
    it("getDeviceTier returns correct tier", async function () {
      await registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED, { value: EMULATED_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      expect(await registry.getDeviceTier(deviceId)).to.equal(TIER_EMULATED);
    });

    it("getDeviceRewardWeightBps returns 0 for Emulated, 5000 for Standard, 10000 for Attested", async function () {
      await registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED,  { value: EMULATED_DEPOSIT });
      await registry.registerTieredDevice(PUBKEY_B, TIER_STANDARD,  { value: STANDARD_DEPOSIT });
      const VALID_PROOF = "0x" + "ab".repeat(64);
      await registry.registerAttested(PUBKEY_C, VALID_PROOF, { value: ATTESTED_DEPOSIT });

      expect(await registry.getDeviceRewardWeightBps(ethers.keccak256(PUBKEY_A))).to.equal(0);
      expect(await registry.getDeviceRewardWeightBps(ethers.keccak256(PUBKEY_B))).to.equal(5000);
      expect(await registry.getDeviceRewardWeightBps(ethers.keccak256(PUBKEY_C))).to.equal(10000);
    });

    it("canClaimBounty: false for Emulated, true for Standard", async function () {
      await registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED, { value: EMULATED_DEPOSIT });
      await registry.registerTieredDevice(PUBKEY_B, TIER_STANDARD, { value: STANDARD_DEPOSIT });
      expect(await registry.canClaimBounty(ethers.keccak256(PUBKEY_A))).to.be.false;
      expect(await registry.canClaimBounty(ethers.keccak256(PUBKEY_B))).to.be.true;
    });

    it("canUseSkillOracle: true for all active tiers", async function () {
      await registry.registerTieredDevice(PUBKEY_A, TIER_EMULATED, { value: EMULATED_DEPOSIT });
      await registry.registerTieredDevice(PUBKEY_B, TIER_STANDARD, { value: STANDARD_DEPOSIT });
      expect(await registry.canUseSkillOracle(ethers.keccak256(PUBKEY_A))).to.be.true;
      expect(await registry.canUseSkillOracle(ethers.keccak256(PUBKEY_B))).to.be.true;
    });

    it("canClaimBounty returns false for inactive device", async function () {
      await registry.registerTieredDevice(PUBKEY_A, TIER_STANDARD, { value: STANDARD_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      await registry.deactivateDevice(deviceId);
      expect(await registry.canClaimBounty(deviceId)).to.be.false;
    });
  });

  // =========================================================================
  //  10. Admin: setTierConfig (2 tests)
  // =========================================================================
  describe("Admin: setTierConfig", function () {
    it("owner can update Emulated deposit", async function () {
      const newCfg = {
        depositWei:        ethers.parseEther("0.5"),
        rewardWeightBps:   0,
        canClaimBounties:  false,
        canUseSkillOracle: true,
      };
      await registry.setTierConfig(TIER_EMULATED, newCfg);
      const stored = await registry.tierConfigs(TIER_EMULATED);
      expect(stored.depositWei).to.equal(ethers.parseEther("0.5"));
    });

    it("non-owner reverts on setTierConfig", async function () {
      const cfg = {
        depositWei: ethers.parseEther("99"), rewardWeightBps: 0,
        canClaimBounties: false, canUseSkillOracle: false,
      };
      await expect(
        registry.connect(alice).setTierConfig(TIER_EMULATED, cfg)
      ).to.be.revertedWithCustomError(registry, "OwnableUnauthorizedAccount");
    });
  });

  // =========================================================================
  //  11. Admin: setApprovedManufacturer (2 tests)
  // =========================================================================
  describe("Admin: setApprovedManufacturer", function () {
    it("owner can approve a manufacturer address", async function () {
      await registry.setApprovedManufacturer(alice.address, true);
      expect(await registry.approvedManufacturers(alice.address)).to.be.true;
    });

    it("setApprovedManufacturer emits ManufacturerApproved", async function () {
      await expect(registry.setApprovedManufacturer(alice.address, true))
        .to.emit(registry, "ManufacturerApproved")
        .withArgs(alice.address, true);
    });
  });

  // =========================================================================
  //  12. Admin: setAttestationEnforced (1 test)
  // =========================================================================
  describe("Admin: setAttestationEnforced", function () {
    it("toggles attestationEnforced and emits event", async function () {
      await expect(registry.setAttestationEnforced(true))
        .to.emit(registry, "AttestationEnforcementChanged")
        .withArgs(true);
      expect(await registry.attestationEnforced()).to.be.true;
      await registry.setAttestationEnforced(false);
      expect(await registry.attestationEnforced()).to.be.false;
    });
  });

  // =========================================================================
  //  13. PoACVerifier compatibility (1 test)
  // =========================================================================
  describe("PoACVerifier compatibility", function () {
    it("PoACVerifier can verify a PoAC from a TieredDeviceRegistry-registered device", async function () {
      // Register at Standard tier
      await registry.registerDevice(PUBKEY_A, { value: STANDARD_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);

      // Build a valid PoAC body
      const ts = await currentBlockTimestampMs();
      const rawBody = buildRawBody({ timestampMs: ts });

      // Verify via PoACVerifierTestable (accepts any sig via mock precompile)
      await expect(
        verifier.verifyPoAC(deviceId, rawBody, FAKE_SIG)
      ).to.not.be.reverted;

      // Confirm record was verified
      const recordHash = ethers.sha256(rawBody);
      expect(await verifier.isRecordVerified(recordHash)).to.be.true;
    });
  });

  // =========================================================================
  //  14. Phase 9: attestation certificate hash (4 tests)
  // =========================================================================
  describe("Phase 9: attestation certificate hash", function () {
    const VALID_PROOF = "0x" + "ab".repeat(64);
    const CERT_HASH   = "0x" + "de".repeat(32);

    it("registerAttestedWithCert stores cert hash", async function () {
      const deviceId = ethers.keccak256(PUBKEY_A);
      await registry.registerAttestedWithCert(PUBKEY_A, VALID_PROOF, CERT_HASH,
          { value: ATTESTED_DEPOSIT });
      expect(await registry.attestationCertificateHashes(deviceId)).to.equal(CERT_HASH);
    });

    it("registerAttestedWithCert emits AttestationCertHashSet", async function () {
      const deviceId = ethers.keccak256(PUBKEY_A);
      await expect(
          registry.registerAttestedWithCert(PUBKEY_A, VALID_PROOF, CERT_HASH,
              { value: ATTESTED_DEPOSIT })
      ).to.emit(registry, "AttestationCertHashSet").withArgs(deviceId, CERT_HASH);
    });

    it("setAttestationCertHash allows device owner to update", async function () {
      await registry.registerAttestedWithCert(PUBKEY_A, VALID_PROOF, CERT_HASH,
          { value: ATTESTED_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      const NEW_HASH  = "0x" + "ff".repeat(32);
      await registry.setAttestationCertHash(deviceId, NEW_HASH);
      expect(await registry.attestationCertificateHashes(deviceId)).to.equal(NEW_HASH);
    });

    it("setAttestationCertHash reverts for non-owner", async function () {
      await registry.registerAttestedWithCert(PUBKEY_A, VALID_PROOF, CERT_HASH,
          { value: ATTESTED_DEPOSIT });
      const deviceId = ethers.keccak256(PUBKEY_A);
      await expect(
          registry.connect(alice).setAttestationCertHash(deviceId, CERT_HASH)
      ).to.be.revertedWithCustomError(registry, "UnauthorizedCertHashUpdate");
    });
  });

  // =========================================================================
  //  15. ManufacturerKey Registry (5 tests)
  // =========================================================================
  describe("15. ManufacturerKey Registry", function () {
    it("owner can set a manufacturer key", async function () {
      await registry.setManufacturerKey(MANUF_ADDR, MANUF_X, MANUF_Y, MANUF_NAME);
      const mk = await registry.getManufacturerKey(MANUF_ADDR);
      expect(mk.pubkeyX).to.equal(MANUF_X);
      expect(mk.pubkeyY).to.equal(MANUF_Y);
      expect(mk.active).to.be.true;
      expect(mk.name).to.equal(MANUF_NAME);
    });

    it("setManufacturerKey emits ManufacturerKeySet event", async function () {
      await expect(registry.setManufacturerKey(MANUF_ADDR, MANUF_X, MANUF_Y, MANUF_NAME))
        .to.emit(registry, "ManufacturerKeySet")
        .withArgs(MANUF_ADDR, MANUF_X, MANUF_Y, MANUF_NAME);
    });

    it("owner can revoke a manufacturer key", async function () {
      await registry.setManufacturerKey(MANUF_ADDR, MANUF_X, MANUF_Y, MANUF_NAME);
      await registry.revokeManufacturerKey(MANUF_ADDR);
      const mk = await registry.getManufacturerKey(MANUF_ADDR);
      expect(mk.active).to.be.false;
    });

    it("revokeManufacturerKey emits ManufacturerKeyRevoked event", async function () {
      await registry.setManufacturerKey(MANUF_ADDR, MANUF_X, MANUF_Y, MANUF_NAME);
      await expect(registry.revokeManufacturerKey(MANUF_ADDR))
        .to.emit(registry, "ManufacturerKeyRevoked")
        .withArgs(MANUF_ADDR);
    });

    it("non-owner cannot setManufacturerKey", async function () {
      const [, , nonOwner] = await ethers.getSigners();
      await expect(
        registry.connect(nonOwner).setManufacturerKey(MANUF_ADDR, MANUF_X, MANUF_Y, MANUF_NAME)
      ).to.be.revertedWithCustomError(registry, "OwnableUnauthorizedAccount");
    });
  });

  // =========================================================================
  //  16. Attestation Enforcement V2 (7 tests)
  //
  //  Uses TieredDeviceRegistryV2Testable which overrides _p256Verify() to
  //  return a configurable boolean rather than calling the IoTeX P256 precompile
  //  (unavailable on Hardhat EVM). This follows the same pattern as
  //  PoACVerifierTestable which overrides _requireValidSignature().
  // =========================================================================
  describe("16. Attestation Enforcement V2", function () {
    let v2registry;  // TieredDeviceRegistryV2Testable instance

    beforeEach(async function () {
      const V2Factory = await ethers.getContractFactory("TieredDeviceRegistryV2Testable");
      v2registry = await V2Factory.deploy(EMULATED_DEPOSIT, STANDARD_DEPOSIT, ATTESTED_DEPOSIT);
      await v2registry.waitForDeployment();
      await v2registry.setManufacturerKey(MANUF_ADDR, MANUF_X, MANUF_Y, MANUF_NAME);
    });

    it("registerAttestedV2 succeeds when attestationEnforced=false (any 64-byte proof)", async function () {
      await v2registry.registerAttestedV2(PUBKEY_A, FAKE_SIG, MANUF_ADDR, { value: ATTESTED_DEPOSIT });
    });

    it("registerAttestedV2 succeeds when enforced=true + _p256Verify returns true", async function () {
      await v2registry.setAttestationEnforced(true);
      // mockP256Result defaults to true in TieredDeviceRegistryV2Testable
      await v2registry.registerAttestedV2(PUBKEY_B, FAKE_SIG, MANUF_ADDR, { value: ATTESTED_DEPOSIT });
    });

    it("registerAttestedV2 reverts NoApprovedManufacturer when key not registered", async function () {
      await v2registry.setAttestationEnforced(true);
      const unknownAddr = "0x9999999999999999999999999999999999999999";
      await expect(
        v2registry.registerAttestedV2(PUBKEY_A, FAKE_SIG, unknownAddr, { value: ATTESTED_DEPOSIT })
      ).to.be.revertedWithCustomError(v2registry, "NoApprovedManufacturer").withArgs(unknownAddr);
    });

    it("registerAttestedV2 reverts ManufacturerKeyInactive after revocation", async function () {
      await v2registry.setAttestationEnforced(true);
      await v2registry.revokeManufacturerKey(MANUF_ADDR);
      await expect(
        v2registry.registerAttestedV2(PUBKEY_A, FAKE_SIG, MANUF_ADDR, { value: ATTESTED_DEPOSIT })
      ).to.be.revertedWithCustomError(v2registry, "ManufacturerKeyInactive").withArgs(MANUF_ADDR);
    });

    it("registerAttestedV2 reverts AttestationVerificationFailed when _p256Verify returns false", async function () {
      await v2registry.setAttestationEnforced(true);
      await v2registry.setMockP256Result(false);
      await expect(
        v2registry.registerAttestedV2(PUBKEY_A, FAKE_SIG, MANUF_ADDR, { value: ATTESTED_DEPOSIT })
      ).to.be.revertedWithCustomError(v2registry, "AttestationVerificationFailed");
    });

    it("registerAttestedWithCertV2 stores cert hash and emits AttestationCertHashSet", async function () {
      const certHash = "0x" + "ab".repeat(32);
      const tx = await v2registry.registerAttestedWithCertV2(
        PUBKEY_C, FAKE_SIG, certHash, MANUF_ADDR, { value: ATTESTED_DEPOSIT }
      );
      const receipt = await tx.wait();
      const deviceId = receipt.logs.find(
        l => l.fragment && l.fragment.name === "DeviceRegistered"
      ).args[0];
      expect(await v2registry.attestationCertificateHashes(deviceId)).to.equal(certHash);
    });

    it("registerAttestedV2 emits ManufacturerAttested event", async function () {
      await expect(
        v2registry.registerAttestedV2(PUBKEY_A, FAKE_SIG, MANUF_ADDR, { value: ATTESTED_DEPOSIT })
      ).to.emit(v2registry, "ManufacturerAttested");
    });
  });
});
