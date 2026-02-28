# VAPI and the DualShock Edge: Flagship Device Overview

> **VAPI** (Verified Autonomous Physical Intelligence) is a trustless gaming
> intelligence protocol. Its primary certified device is the **DualShock Edge**.
> Proof of Human Gaming starts here.

---

## 1. What Is VAPI?

VAPI is a software protocol that attaches a cryptographically signed, on-chain
verifiable record to every inference made by a gaming AI agent. The record is called
a **Proof of Autonomous Cognition (PoAC)**.

A PoAC record proves three things simultaneously:

1. **Sensor authenticity** — the input came from a real physical device
2. **Inference integrity** — the agent made an honest decision from that input
3. **Identity continuity** — the signing key is hardware-rooted and stable across sessions

The protocol is device-agnostic by design. Any device that satisfies the **capability
taxonomy** is a valid VAPI node. The DualShock Edge satisfies all four capabilities in
the richest way currently available.

---

## 2. DualShock Edge as the Primary Certified Device

### 2.1 Device Capability Taxonomy

Every VAPI node must provide four capabilities:

| Capability | Description | DualShock Edge | IoTeX Pebble Tracker |
|---|---|---|---|
| `sensor_stream` | Continuous, tamper-evident physical sensor data | IMU + sticks + adaptive triggers + touchpad | BME680 (env) + GPS + accelerometer |
| `signing_key` | Hardware-rooted ECDSA-P256 private key | YubiKey/ATECC608A via Phase 9 bridge | CryptoCell-310 (on-chip) |
| `monotonic_counter` | Strictly increasing, replay-resistant counter | Persistent bridge counter (SQLite-backed) | Hardware RTC + firmware counter |
| `network_bridge` | Path to IoTeX chain for record submission | Python bridge (USB/BT -> asyncio -> Web3.py) | Firmware CoAP -> LPWAN -> relay |

The DualShock Edge delivers the densest sensor stream of any VAPI node: six-axis IMU,
dual analog sticks, touchpad, and — uniquely — **adaptive trigger force feedback** with
full resistance curve telemetry.

### 2.2 Adaptive Trigger Detection Surface

The DualShock Edge L2 and R2 triggers are motorized, programmable haptic surfaces. They
expose two signals that are structurally different from every other controller input:

- **Resistance curve**: The force required to depress the trigger changes dynamically
  across the travel range (0–255). Legitimate play produces characteristic pressure
  profiles tied to reaction time, grip fatigue, and motor control.
- **Feedback timing**: Haptic feedback injected by the game creates micro-vibrations in
  the trigger that are reflected in the sensor stream.

A software cheating tool — aimbot, macro, or script — operates on the logical HID layer.
It can inject button press events. It **cannot** reproduce the biomechanical pressure
dynamics of a human pressing a trigger against resistance. This makes the adaptive trigger
signal the strongest available evidence of physical human presence in a gaming session.

The VAPI sensor commitment includes the trigger resistance curve state at every PoAC
interval. Any injected press that lacks the matching pressure profile is detectable by
the PITL Layer 2 HID-XInput oracle (Phase 8 code `0x28: DRIVER_INJECT`).

### 2.3 Sensor Commitment Schema v2 (Kinematic/Haptic)

DualShock Edge sessions use **sensor commitment schema v2**. The 32-byte `sensor_commitment`
field in each PoAC record is:

```
sensor_commitment = SHA-256(
    left_stick_x     || left_stick_y   ||   # int16 x2
    right_stick_x    || right_stick_y  ||   # int16 x2
    l2_value         || r2_value       ||   # uint8 x2 (trigger depression 0-255)
    l2_resistance    || r2_resistance  ||   # uint8 x2 (trigger resistance state)
    gyro_x  || gyro_y  || gyro_z       ||   # int16 x3 (raw/1000 = rad/s)
    accel_x || accel_y || accel_z      ||   # int16 x3
    timestamp_ms                            # uint64
)
```

This is distinct from **schema v1** (environmental), used by Pebble Tracker:

```
sensor_commitment = SHA-256(
    temperature || humidity || pressure ||  # float32 x3 (BME680)
    latitude    || longitude            ||  # float64 x2 (GPS)
    altitude                            ||  # float32
    accel_x || accel_y || accel_z       ||  # int16 x3
    timestamp_ms                            # uint64
)
```

The commitment schema version is implicit in the `model_manifest_hash` — the TinyML
model manifest specifies which sensor fields it was trained on.

---

## 3. Proof of Human Gaming

### 3.1 The Core Claim

A PoAC chain rooted to a DualShock Edge is a **Proof of Human Gaming**: cryptographic
evidence that a human physically held and operated the controller during a gaming session.

This is not merely anti-cheat. It is a gaming-native Sybil resistance primitive with
on-chain verifiability. In gaming contexts, Sybil resistance means: one human, one
controller, one verified session. A player cannot run 100 bots and have each one produce
a clean PoAC chain. Each chain requires:

1. A physical controller with a unique hardware-rooted signing key
2. Real sensor data matching a human behavioral model
3. Adaptive trigger dynamics that are biomechanically plausible
4. No HID-XInput pipeline injection (PITL Layer 2)
5. No behavioral anomalies (PITL Layer 3: wallhack pre-aim, aimbot lock-on)

### 3.2 Why Software Injection Cannot Forge the Signal

| Signal component | Software injection capability | Why it fails |
|---|---|---|
| Button presses (HID) | Can inject | Detectable: no matching trigger resistance curve |
| Stick inputs (HID) | Can inject | Detectable: no matching IMU dynamics |
| Gyro/accelerometer | Cannot inject (hardware sensor) | Physical motion not present |
| Trigger resistance curve | Cannot inject (haptic motor state) | Motor state not controllable via HID |
| Reaction time distribution | Can approximate | Fails behavioral statistical model (Phase 8 Layer 3) |

The PITL 5-layer architecture catches injected inputs at every layer:

- Layer 0: Physical presence (controller is plugged in / connected)
- Layer 1: PoAC chain integrity (ECDSA-P256 signature, monotonic counter, hash chain)
- Layer 2: HID-XInput oracle (`0x28: DRIVER_INJECT` — discrepancy between raw HID and XInput reports)
- Layer 3: Behavioral model (`0x29: WALLHACK_PREAIM`, `0x2A: AIMBOT_BEHAVIORAL`)
- Layer 4: On-chain verification (IoTeX PoACVerifier.sol, batch submission)

### 3.3 On-Chain Verifiability

Every PoAC record is submitted to IoTeX via `PoACVerifier.verifyPoACBatch()`. The
on-chain verifier confirms:

- ECDSA-P256 signature valid against the registered device pubkey
- Record hash not previously submitted (replay resistance)
- Monotonic counter strictly increasing
- Device active in `TieredDeviceRegistry`

The result is a tamper-evident, publicly auditable session history. Any third party
(tournament organizer, anti-cheat vendor, bounty market participant) can verify the
complete proof chain from physical controller to on-chain record.

---

## 4. PHCI Certification and PITL

**PHCI** (Physical Human Controller Input) is the certification class VAPI targets.
The DualShock Edge achieves PHCI by satisfying all five PITL layers (see §3.2 above).

The `TieredDeviceRegistry` on-chain registry records three certification tiers:

| Tier | Deposit | Signing key | Use case |
|---|---|---|---|
| Emulated | 0.1 IOTX testnet | Software JSON | Development only |
| Standard | 1 IOTX testnet | Software JSON | Pre-production testing |
| Attested | 0.01 IOTX testnet | YubiKey / ATECC608A | Production PHCI |

Production DualShock Edge deployments register as **Attested** tier with a hardware
signing backend (Phase 9). The `attestationCertificateHashes` mapping stores the
SHA-256 of the hardware attestation cert on-chain for Phase 10 enforcement.

---

## 5. Pebble Tracker as DePIN Reference Extension

The IoTeX Pebble Tracker is a **DePIN (Decentralized Physical Infrastructure Network)**
sensor node. It is a valid VAPI device — it satisfies the capability taxonomy — but its
sensor stream and use case are different:

| Property | DualShock Edge (schema v2) | Pebble Tracker (schema v1) |
|---|---|---|
| Primary sensor type | Kinematic / haptic | Environmental |
| Use case | Gaming PHCI certification | IoT / environmental attestation |
| Inference domain | Anti-cheat, reaction, mechanical skill | Anomaly detection, location trust |
| `sensor_commitment` | IMU + sticks + triggers | BME680 + GPS + accelerometer |
| Production signing | YubiKey / ATECC608A (bridge) | CryptoCell-310 (on-chip) |
| Network path | USB/BT bridge -> asyncio -> Web3.py | Firmware CoAP -> LPWAN |

The VAPI protocol is the same for both. The 228-byte PoAC wire format is identical.
The on-chain verifier does not distinguish sensor schemas — it verifies the signature
and the hash chain.

Pebble Tracker validates VAPI's claim to be a **universal verifiable inference protocol**,
not a gaming-specific system. DualShock Edge validates its claim to be the most
capable production device currently available.

---

## 6. Protocol Invariants (Device-Agnostic)

These properties hold for every VAPI node regardless of device type:

### 6.1 228-Byte PoAC Wire Format

```
Offset  Size  Field
 0      32    prev_poac_hash       (SHA-256 of previous record body)
32      32    sensor_commitment    (SHA-256 of sensor snapshot, schema v1 or v2)
64      32    model_manifest_hash  (SHA-256 of TinyML model binary + config)
96      32    world_model_hash     (SHA-256 of current world model state)
128      1    inference_result     (0x00-0x2A, gaming domain: 0x20-0x2A)
129      1    action_code          (agent action taken)
130      1    confidence           (0-255, INT8 from model output)
131      1    battery_pct          (0-100)
132      4    monotonic_ctr        (uint32, strictly increasing per device)
136      8    timestamp_ms         (uint64, Unix epoch ms)
144      8    latitude             (int64, fixed-point deg * 1e7, 0 if N/A)
152      8    longitude            (int64, fixed-point deg * 1e7, 0 if N/A)
160      4    bounty_id            (uint32, 0 if not participating)
164     64    ECDSA-P256 signature (raw r||s, 32+32 bytes)
```

Total: 228 bytes. The signature covers bytes 0-163 (the 164-byte body).

### 6.2 ECDSA-P256 Signing

Every record is signed with ECDSA over P-256 (secp256r1) using SHA-256.
The signing key is bound to a `deviceId = keccak256(pubkey)` in `TieredDeviceRegistry`.
Hardware backends (Phase 9): YubiKey PIV slot 9c or ATECC608A slot 0.
Software backend: plaintext JSON key file (development only, not PHCI-eligible).

### 6.3 Three-Layer Agent Architecture

| Layer | Cycle | Responsibility |
|---|---|---|
| Reflexive | 30 s | Fast heuristic response, immediate action |
| Deliberative | 5 min | TinyML inference, world model update |
| Strategic | 1 hr | Bounty selection, ELO-aware skill planning |

### 6.4 TinyML Anti-Cheat Model

INT8-quantized dense network: 30 input features -> 64 -> 32 -> 6 output classes.
Classes: NOMINAL, SKILLED, CHEAT:REACTION, CHEAT:MACRO, CHEAT:AIMBOT, CHEAT:RECOIL.
Phase 8 adds heuristic PITL codes: 0x26 (IMU_MISS), 0x27 (INJECTION), 0x28 (DRIVER_INJECT),
0x29 (WALLHACK_PREAIM), 0x2A (AIMBOT_BEHAVIORAL).

### 6.5 On-Chain Contract Stack

| Contract | Role |
|---|---|
| `TieredDeviceRegistry` | Device identity, tier, pubkey, attestation cert hash |
| `PoACVerifier` | Batch ECDSA-P256 verification, replay prevention |
| `BountyMarket` | Task posting, evidence submission, reward distribution |
| `SkillOracle` | ELO-inspired on-chain rating [0-3000], 5 tiers |
| `ProgressAttestation` | BPS improvement tracking between verified PoAC pairs |
| `TeamProofAggregator` | Merkle root of sorted team PoAC hashes (2-6 members) |
