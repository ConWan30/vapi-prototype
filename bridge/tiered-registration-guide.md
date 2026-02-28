# VAPI Tiered Device Registration Guide

**Phase 7 — Implemented in `TieredDeviceRegistry.sol`**

---

## Overview

Phase 7 introduces a three-tier registration model to the VAPI device registry. Tiers
provide Sybil resistance while preserving support for DualShock/software emulation use
cases. All downstream contracts (PoACVerifier, BountyMarket, SkillOracle) continue to
work through the existing `DeviceRegistry` interface — no changes required.

| Tier | Testnet Deposit | Mainnet Target | Bounty Claims | Reward Weight |
|------|----------------|----------------|---------------|---------------|
| **Emulated** | 0.1 IOTX | 10 IOTX | No | 0% |
| **Standard** | 1 IOTX | 100 IOTX | Yes | 50% |
| **Attested** | 0.01 IOTX | 1 IOTX | Yes | 100% |

---

## 1. Quick Start

No configuration change needed for Standard tier (the default):

```bash
# .env — Standard tier is the default, no extra vars required
DEVICE_REGISTRY_ADDRESS=0x...
BRIDGE_PRIVATE_KEY=0x...
```

On first startup, the bridge auto-registers at **Standard tier** using
`ensure_device_registered_tiered()`. Subsequent startups skip registration because
`PersistentIdentity.is_chain_registered` is `True`.

---

## 2. Tier Selection

Set `DEVICE_REGISTRATION_TIER` in your `.env` or environment:

```bash
DEVICE_REGISTRATION_TIER=Emulated   # DualShock / software dev
DEVICE_REGISTRATION_TIER=Standard   # IoT devices (default)
DEVICE_REGISTRATION_TIER=Attested   # Pebble Tracker with ioID cert
```

The bridge reads this via `config.device_registration_tier` and passes it to
`chain.register_device_tiered()`.

---

## 3. Emulated Tier

Use for **DualShock controllers and software simulations** during development.

**Characteristics:**
- Lowest deposit requirement (0.1 IOTX testnet / 10 IOTX mainnet)
- Participates in **SkillOracle** rating — ELO updates work normally
- Excluded from **BountyMarket** evidence submission (`canClaimBounties = false`)
- `rewardWeightBps = 0` — no reward weight in any reward distribution formula

**Configuration:**
```bash
DEVICE_REGISTRATION_TIER=Emulated
```

**Registration call (bridge):**
```python
await chain.register_device_tiered(pubkey_bytes, tier="Emulated")
# Uses registerTieredDevice(pubkey, 0) on-chain
```

---

## 4. Standard Tier

**Recommended for IoT devices** and any hardware that does not have a Pebble ioID
manufacturer certificate.

**Characteristics:**
- Standard deposit (1 IOTX testnet / 100 IOTX mainnet target)
- Full access to **BountyMarket** evidence submission
- `rewardWeightBps = 5000` — 50% reward weight in distribution calculations
- Full **SkillOracle** participation

**Configuration:**
```bash
DEVICE_REGISTRATION_TIER=Standard  # or omit — this is the default
```

**Registration call (bridge):**
```python
await chain.register_device_tiered(pubkey_bytes, tier="Standard")
# Uses registerTieredDevice(pubkey, 1) on-chain
# Also called by the backward-compat register_device() wrapper
```

---

## 5. Attested Tier (Preview — Phase 8 Crypto Pending)

For **Pebble Tracker hardware** with IoTeX ioID manufacturer attestation.

**Characteristics:**
- Lower deposit (0.01 IOTX testnet / 1 IOTX mainnet target) because hardware attestation
  provides the primary Sybil resistance
- `rewardWeightBps = 10000` — 100% reward weight (full rewards)
- Full **BountyMarket** and **SkillOracle** access
- Requires a 64-byte attestation proof from the manufacturer

**Current testnet behavior (`attestationEnforced = false`):**
Any 64-byte hex string is accepted as the attestation proof. This allows Attested-tier
registration during pre-hardware development and testing.

**Configuration:**
```bash
DEVICE_REGISTRATION_TIER=Attested
ATTESTATION_PROOF_HEX=abcdef...  # 64-byte (128 hex char) manufacturer proof
```

**Registration call (bridge):**
```python
proof = bytes.fromhex(os.environ["ATTESTATION_PROOF_HEX"])
await chain.register_device_tiered(pubkey_bytes, tier="Attested",
                                   attestation_proof=proof)
# Uses registerAttested(pubkey, proof) on-chain
```

**Phase 8 roadmap:** When `setAttestationEnforced(true)` is called (mainnet launch),
the contract will verify the proof as an ECDSA-P256 signature over `keccak256(pubkey)`
using the IoTeX precompile at `0x0100`, and check the signing key against
`approvedManufacturers`.

---

## 6. Testnet Deployment

Deploy `TieredDeviceRegistry` along with all other contracts:

```bash
cd contracts

# Default testnet deposits (0.1 / 1 / 0.01 IOTX)
npx hardhat run scripts/deploy.js --network iotex_testnet

# Custom deposits via env
EMULATED_DEPOSIT=0.5 STANDARD_DEPOSIT=5 ATTESTED_DEPOSIT=0.05 \
    npx hardhat run scripts/deploy.js --network iotex_testnet
```

Console output includes a tier deposit summary:

```
Tier Deposits:
  Emulated: 0.1 IOTX (mainnet target: 10 IOTX)
  Standard: 1.0 IOTX (mainnet target: 100 IOTX)
  Attested: 0.01 IOTX (mainnet target: 1 IOTX)
```

The generated `bridge/.env.testnet` includes:
```
DEVICE_REGISTRATION_TIER=Standard
ATTESTATION_PROOF_HEX=
```

---

## 7. Mainnet Checklist

Before mainnet launch, complete the following:

- [ ] Set mainnet-target deposits in deployment environment:
  ```bash
  EMULATED_DEPOSIT=10 STANDARD_DEPOSIT=100 ATTESTED_DEPOSIT=1
  ```
- [ ] Implement Phase 8 ioID P256 certificate verification in
  `TieredDeviceRegistry._validateAttestation()`
- [ ] Call `setApprovedManufacturer(iotexFoundationKey, true)` after deployment
- [ ] Call `setAttestationEnforced(true)` to activate full attestation enforcement
- [ ] Verify `attestationEnforced == true` on-chain before opening Attested registrations
- [ ] Update `DEVICE_REGISTRATION_TIER` in production bridge `.env` for each device type

---

## 8. Contract Interface Reference

```solidity
// Backward-compatible (Standard tier)
function registerDevice(bytes calldata pubkey) external payable returns (bytes32);

// Explicit tier (Emulated or Standard only)
function registerTieredDevice(bytes calldata pubkey, RegistrationTier tier)
    external payable returns (bytes32);

// Attested tier (requires 64-byte proof)
function registerAttested(bytes calldata pubkey, bytes calldata attestationProof)
    external payable returns (bytes32);

// View helpers
function getDeviceTier(bytes32 deviceId) external view returns (RegistrationTier);
function getDeviceRewardWeightBps(bytes32 deviceId) external view returns (uint16);
function canClaimBounty(bytes32 deviceId) external view returns (bool);
function canUseSkillOracle(bytes32 deviceId) external view returns (bool);
```

---

## 9. Persistent Identity Integration

The `registration_tier` is persisted in `~/.vapi/dualshock_device_key.json`:

```json
{
  "private_der_hex": "...",
  "public_key_hex": "...",
  "registered_tx": "0xabc...",
  "registry_address": "0x123...",
  "registered_at_iso": "2026-02-21T...",
  "registration_tier": "Standard"
}
```

Read it back via:
```python
identity = PersistentIdentity().load_or_create()
print(identity.registration_tier)  # "Standard"
```
