# VAPI Sybil Resistance Analysis
**Version:** Phase 7
**Date:** 2026-02-21

---

## 1. Problem Statement

A Sybil attack in VAPI means registering multiple device identities controlled by a
single actor to:

1. **Reward farming** — multiply bounty claim volume beyond hardware capability
2. **Reputation inflation** — generate corroboration between controlled devices to
   boost all their `reputationScore` values
3. **Governance capture** — achieve disproportionate weight in stake-weighted governance
4. **Rating ladder manipulation** — create "feeder" accounts at lower tiers to exploit
   team-based reward structures in `TeamProofAggregator`

The current system uses an **economic stake requirement** (minimum IOTX deposit per
device) as the primary Sybil barrier. This document analyzes its effectiveness and
compares it to the alternative: **hardware attestation** via IoTeX ioID.

---

## 2. Current Defense: Economic Stake

### 2.1 Mechanism

`DeviceRegistry.registerDevice()` requires `msg.value >= minimumDeposit` (default: 1 IOTX).
The deposit is held for the device lifetime and released after a 7-day cooldown following
deactivation. `deviceId = keccak256(pubkey)` ensures each keypair maps to one registry entry.

### 2.2 Cost Analysis

| Scenario | Deposit/device | Devices needed | Attack cost (at $0.04/IOTX) |
|---|---|---|---|
| Current testnet (1 IOTX) | 1 IOTX | 1,000 | ~$40 |
| Mainnet target (100 IOTX) | 100 IOTX | 1,000 | ~$4,000 |
| High-security (1,000 IOTX) | 1,000 IOTX | 1,000 | ~$40,000 |

**Assessment:** At current IOTX prices ($0.04), 1 IOTX/device is trivially cheap for
a well-funded attacker targeting high-value bounties. A bounty worth $100 can be
profitably attacked with $40 in stake, especially since stake is recoverable.

The 7-day cooldown limits DEPOSIT CYCLING but not concurrent Sybil farms (an attacker
simply holds deposits across all devices).

### 2.3 Advantages of Stake-Based Approach

| Property | Assessment |
|---|---|
| No hardware requirement | Any key pair registers — maximizes device pool |
| Capital-efficient scaling | Legitimate operators can register many devices |
| Simple to implement | Already implemented |
| Parametrically adjustable | `setMinimumDeposit()` admin function exists |

### 2.4 Weaknesses of Stake-Only Approach

| Weakness | Impact |
|---|---|
| Economically bounded, not physically bounded | Scales with attacker capital, not hardware availability |
| IOTX price volatility | A price drop makes the deposit trivially cheap |
| Deposit is recoverable | Attacker pays only the cost of capital (opportunity cost), not deposit |
| No proof of distinct hardware | Two devices on the same physical machine are indistinguishable |

---

## 3. Alternative: Hardware Attestation via IoTeX ioID

### 3.1 Mechanism

IoTeX's [ioID protocol](https://docs.iotex.io/the-iotex-stack/identity/ioid) provides
decentralized device identity anchored to a physical secure element:

1. Each Pebble Tracker ships with a provisioned ECDSA-P256 keypair burned into its
   CryptoCell-310 (Arm TrustZone protected, FIPS 140-2 Level 2).
2. The public key is registered in the IoTeX ioID registry on-chain, signed by the
   device manufacturer (IoTeX Foundation or authorized OEM).
3. `DeviceRegistry.registerDevice()` would additionally verify a certificate chain:
   `device_pubkey` ← signed by `manufacturer_attestation_key` ← signed by `IoTeX_ioID_root`.

### 3.2 Proposed Registration Flow with ioID

```
Device (Pebble) → Bridge:
    pubkey (65 bytes)
    manufacturer_cert (signature by IoTeX ioID over pubkey)

DeviceRegistry.registerDeviceAttested(pubkey, manufacturer_cert, deposit):
    1. Verify manufacturer_cert using IoTeX P256 precompile
    2. Check manufacturer key is in approved_manufacturers set (Ownable)
    3. Check pubkey not already registered (Sybil: one physical device = one pubkey)
    4. Require deposit >= minimumDeposit
    5. Register as before
```

### 3.3 Advantages of Hardware Attestation

| Property | Assessment |
|---|---|
| Physically bounded | Each Pebble Tracker has one non-extractable key |
| Manufacturer-verified | Cryptographic proof device is genuine hardware |
| Complementary to stake | Can combine both (attestation + deposit) |
| Aligns with VAPI's DePIN mission | Proves physical sensor presence, not just key ownership |

### 3.4 Weaknesses of Hardware Attestation

| Weakness | Impact |
|---|---|
| Requires Pebble hardware | Excludes DualShock and software emulation use cases |
| Manufacturer key management | IoTeX Foundation key compromise would invalidate all attestations |
| Certificate revocation | Compromised device keys need on-chain revocation mechanism |
| Implementation complexity | +2-3 weeks of development (Certificate chain verification in Solidity) |
| Supply chain attacks | Attacker with access to manufacturing could provision fake certificates |

---

## 4. Comparative Analysis

| Criterion | Stake Only | Hardware Attestation | Combined |
|---|---|---|---|
| **Sybil resistance** | Weak (capital-bounded) | Strong (physics-bounded) | Very strong |
| **Implementation cost** | Done | High | High |
| **Operational complexity** | Low | High | High |
| **Device diversity** | Any key = device | Pebble hardware only | Tiered approach |
| **Gaming/DualShock use case** | Full support | Excluded | Partial support |
| **Mainnet readiness** | With higher deposit | Not yet | Phased |

---

## 5. Recommendation

### Short-term (testnet → mainnet launch)

**Increase Standard deposit to 100 IOTX** before mainnet deployment.

Rationale:
- $4 USD cost per device at current prices; $40 for 1,000 bots
- Combined with 7-day cooldown, Sybil farming becomes less profitable than legitimate operation
- Override at deploy time: `STANDARD_DEPOSIT=100 npx hardhat run scripts/deploy.js`

### Medium-term (Phase 7 — COMPLETE)

**Tiered registration implemented:** `contracts/contracts/TieredDeviceRegistry.sol`

`TieredDeviceRegistry` IS-A `DeviceRegistry` (Liskov-compliant inheritance). All downstream
contracts (PoACVerifier, BountyMarket, SkillOracle) accept it through the existing
`DeviceRegistry` interface — zero changes required to those contracts.

**Deployed tier deposits (testnet / mainnet targets):**

| Tier | Testnet Deposit | Mainnet Target | `rewardWeightBps` | `canClaimBounties` | `canUseSkillOracle` |
|------|----------------|----------------|-------------------|--------------------|---------------------|
| **Emulated** (DualShock/software) | 0.1 IOTX | 10 IOTX | 0 (0%) | false | true |
| **Standard** (IoT devices) | 1 IOTX | 100 IOTX | 5000 (50%) | true | true |
| **Attested** (Pebble ioID cert) | 0.01 IOTX | 1 IOTX | 10000 (100%) | true | true |

**Registration entry points:**
- `registerDevice(pubkey)` — backward-compatible; assigns Standard tier
- `registerTieredDevice(pubkey, tier)` — Emulated or Standard only
- `registerAttested(pubkey, attestationProof)` — Attested tier; 64-byte proof required

**Testnet permissive mode:** `attestationEnforced = false` — any 64-byte proof accepted
for the Attested tier, enabling pre-hardware testing. Set to `true` before mainnet via
`setAttestationEnforced(true)` once Phase 8 crypto is live.

**Bridge integration:** `DEVICE_REGISTRATION_TIER` env var selects tier at startup.
`chain.py::register_device_tiered()` reads `tierConfigs(tier)` on-chain to determine
the exact deposit, then calls `registerTieredDevice` or `registerAttested`.

### Long-term (Phase 8+)

**Full ioID integration** for Pebble Tracker devices:
- Full ECDSA-P256 certificate chain verification via IoTeX precompile 0x0100 in
  `TieredDeviceRegistry._validateAttestation()` (stub already present, reverts with
  `AttestationValidatorNotImplemented` when `attestationEnforced=true`)
- `BountyMarket` enforcement: check `canClaimBounty(deviceId)` before evidence submission
- `SkillOracle` enforcement: check `canUseSkillOracle(deviceId)` before rating update
- `approvedManufacturers` mapping: populate with IoTeX Foundation key before mainnet
- Certificate revocation via `deactivateDevice(deviceId)` (existing mechanism)
- Governance module with attestation-gated voting weight

---

## 6. Current Risk Posture (Phase 7)

| Risk | Level | Mitigation |
|---|---|---|
| Testnet Sybil farming | Low | Testnet IOTX is free; acceptable for testing |
| Mainnet Sybil farming (Standard 1 IOTX) | **High** | Set `STANDARD_DEPOSIT=100` before mainnet |
| Mainnet Sybil farming (Standard 100 IOTX) | Medium | Monitor, implement reputation decay |
| Emulated-tier Sybil (no bounty rewards) | Low | Emulated tier excluded from BountyMarket |
| False Attested registration (testnet) | Low | `attestationEnforced=false` intentional for pre-hardware testing |
| False Attested registration (mainnet) | **High** | Set `attestationEnforced=true` + implement Phase 8 crypto before mainnet |

**Actions required before mainnet:**
1. Set `STANDARD_DEPOSIT=100` and `EMULATED_DEPOSIT=10` in deploy environment.
2. Implement Phase 8 ioID P256 cert verification and call `setAttestationEnforced(true)`.
3. Populate `approvedManufacturers` with the IoTeX Foundation attestation key.
