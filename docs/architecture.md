# VAPI System Architecture

## Overview

VAPI (Verified Autonomous Physical Intelligence) is a cryptographic anti-cheat
protocol that proves gaming sessions are played by a real human holding a physical
controller. The core primitive is the **Proof of Autonomous Cognition (PoAC)** — a
228-byte hash-chained record binding sensor commitments, model attestation,
world-model state, and inference output, signed with hardware-backed ECDSA-P256.

---

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        PHYSICAL CONTROLLER                               │
│                                                                          │
│  DualShock Edge CFI-ZCP1                                                 │
│  ┌──────────────────────────────────────┐                                │
│  │  Adaptive Triggers (L2/R2 motorized) │ ← Unforgeable biometric surface│
│  │  Stick Axes (4× uint8)               │                                │
│  │  IMU: Gyro + Accel (6× int16)        │                                │
│  │  Buttons, D-Pad                      │                                │
│  └──────────────────────────────────────┘                                │
│                    │                                                      │
│                    │ USB HID (64–128 byte reports @ up to 1kHz)          │
└────────────────────┼────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        BRIDGE SERVICE (Python asyncio)                   │
│                                                                          │
│  ┌─────────────────┐    ┌──────────────────────────────────────────┐    │
│  │  HID Transport  │───▶│  PITL — Physical Input Trust Layer       │    │
│  │  (hidapi)       │    │                                          │    │
│  └─────────────────┘    │  L2: HID-XInput Oracle    → 0x28        │    │
│                         │  L3: Behavioral ML         → 0x29/0x2A  │    │
│  ┌─────────────────┐    │  L4: Mahalanobis Biometric → 0x30       │    │
│  │FeatureExtractor │───▶│  L5: Temporal Rhythm       → 0x2B       │    │
│  │(30-dim vector)  │    │                                          │    │
│  └─────────────────┘    │  Fusion: humanity_prob = 0.4×L4 +       │    │
│                         │          0.4×L5 + 0.2×E4                │    │
│                         └────────────────────┬─────────────────────┘    │
│                                              │                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                   PoAC Record Builder                            │    │
│  │                                                                  │    │
│  │  228 bytes (FROZEN):                                             │    │
│  │  prev_hash(32) + sensor_commit(32) + model_hash(32) +           │    │
│  │  wm_hash(32) + inference(1) + action(1) + confidence(1) +       │    │
│  │  battery(1) + counter(4) + ts_ms(8) + lat(8) + lon(8) +         │    │
│  │  bounty_id(4) || ECDSA-P256 signature (64)                       │    │
│  │                                                                  │    │
│  │  record_hash = SHA-256(raw[:164])   ← used for deduplication    │    │
│  │  chain_hash  = SHA-256(raw[:228])   ← DIFFERENT from record_hash│    │
│  └─────────────────────────────────────┬────────────────────────────┘    │
│                                        │                                  │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Batcher (bounded asyncio.Queue, maxsize=1000)                   │    │
│  │  Batch → on-chain via Web3 + IoTeX RPC                           │    │
│  │  SQLite persistence: records, devices, pitl_*, phg_*, insight_*  │    │
│  └─────────────────────────────────────┬────────────────────────────┘    │
│                                        │                                  │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Intelligence Stack (asyncio background tasks)                     │  │
│  │  ┌──────────────────┐ ┌─────────────────┐ ┌────────────────────┐  │  │
│  │  │ ProactiveMonitor  │ │ FederationBus   │ │InsightSynthesizer  │  │  │
│  │  │ (60s real-time)   │ │ (120s cross-    │ │(6h retrospective)  │  │  │
│  │  │ 3 detection checks│ │  bridge privacy)│ │5 modes incl. Cred. │  │  │
│  │  └──────────────────┘ └─────────────────┘ │ Enforcement        │  │  │
│  │                                            └────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────┬──────────────────────────────┘
                                           │ IoTeX RPC (Web3)
                                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        IoTeX L1 SMART CONTRACTS                          │
│                                                                          │
│  P256 precompile at 0x0100 (hardware-accelerated ECDSA-P256 verify)      │
│                                                                          │
│  ┌──────────────────┐  ┌─────────────────┐  ┌───────────────────────┐  │
│  │PoACVerifier      │  │TieredDeviceReg. │  │PHGRegistry            │  │
│  │Batch ECDSA verify│  │Identity + staking│  │On-chain humanity score│  │
│  │Replay prevention │  │tier + reputation │  │checkpoint chain       │  │
│  └──────────────────┘  └─────────────────┘  └───────────────────────┘  │
│                                                                          │
│  ┌──────────────────┐  ┌─────────────────┐  ┌───────────────────────┐  │
│  │PHGCredential     │  │TournamentGate   │  │PITLSessionRegistry    │  │
│  │Soulbound ERC-5192│  │V1/V2/V3 gates   │  │ZK PITL session proofs │  │
│  │suspend/reinstate │  │PHG + velocity   │  │nullifier anti-replay  │  │
│  └──────────────────┘  └─────────────────┘  └───────────────────────┘  │
│                                                                          │
│  ┌──────────────────┐  ┌─────────────────┐  ┌───────────────────────┐  │
│  │SkillOracle       │  │ProgressAttest.  │  │FederatedThreatReg.    │  │
│  │ELO [0-3000]      │  │BPS improvement  │  │Cross-bridge cluster   │  │
│  └──────────────────┘  └─────────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

```
InputSnapshot (HID) → Feature Extraction (30-dim) → PITL L2–L5
        ↓
PoAC Record (228B) → SHA-256 commitment → chain_hash linkage
        ↓
Batcher → batch transaction → PoACVerifier.submitBatch()
        ↓
P256 precompile (0x0100) → signature verification
        ↓
PHGRegistry.commitCheckpoint() → cumulative score + velocity
        ↓
TournamentGate.assertEligible() → tournament access
```

---

## Trust Boundaries

| Component | Trust Level | Verification Method |
|-----------|-------------|---------------------|
| DualShock Edge (HID) | Hardware-rooted | ECDSA-P256, IMU correlation, adaptive trigger biometric |
| Bridge service | **Weakest link** | ZK PITL proofs (currently mock mode) |
| IoTeX L1 contracts | Trustless | On-chain verification, immutable state |
| ZK PITL proofs | Cryptographic | Groth16 over BN254 (~1,820 constraints; dev ceremony keys) |
| PHGCredential | On-chain + provisional | `isActive()` gate; suspend/reinstate on-chain |
| InsightSynthesizer | Bridge-internal | Evidence hash referenced in `insight_digests` (immutable row) |

---

## Key Protocol Invariants

- **228-byte PoAC format is FROZEN** — changing field offsets breaks firmware↔contract↔bridge
- `record_hash = SHA-256(raw[:164])` ≠ `chain_hash = SHA-256(raw[:228])` — **never swap**
- `deviceId = keccak256(pubkey)` — **never confuse with record hash (SHA-256)**
- Hard cheat codes `0x28/0x29/0x2A` are in range `[0x28, 0x2A]` — advisory codes `0x2B/0x30` are OUTSIDE this range
- `humanity_prob` is SQLite-only — **never goes on-chain**
- Stable biometric track updates **only on clean NOMINAL sessions** — anomaly sessions must be excluded to prevent baseline poisoning

---

## PITL Stack (L0–L5)

| Layer | Name | Detection Target | Output Code |
|-------|------|-----------------|-------------|
| L0 | Physical presence | Hardware attestation anchor | — |
| L1 | PoAC chain integrity | Tamper detection, replay | — |
| L2 | HID-XInput Oracle | Driver injection | 0x28 DRIVER_INJECT |
| L3 | Behavioral ML | Wallhack, aimbot | 0x29 WALLHACK, 0x2A AIMBOT |
| L4 | Biometric Mahalanobis | Biometric anomaly | 0x30 BIOMETRIC_ANOMALY (advisory) |
| L5 | Temporal Rhythm Oracle | Temporal bot | 0x2B TEMPORAL_BOT (advisory) |
| E4 | Embedding (cognitive trajectory) | Session-to-session drift | feeds fusion |

**Fusion**: `humanity_prob = 0.4×p_L4 + 0.4×p_L5 + 0.2×p_E4`

---

## Temporal Intelligence Stack

| Monitor | Cycle | Role |
|---------|-------|------|
| ProactiveMonitor | 60s | Real-time cluster detection, trajectory checks, eligibility horizon alerts |
| FederationBus | 120s | Cross-bridge threat correlation via privacy-preserving cluster fingerprints |
| InsightSynthesizer | 6h | Retrospective 24h/7d/30d digests → risk labels → detection policy updates → credential enforcement |
