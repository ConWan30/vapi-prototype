# VAPI: Physical Input Trust Layer (PITL)

## Strategic Positioning Document — Phase 8

---

## 1. Executive Summary

VAPI is a **Physical Input Trust Layer (PITL)**: a player-owned, hardware-rooted
attestation layer that produces cryptographic proof of legitimate controller input.
It is **not** a replacement for existing kernel-level anti-cheat systems (VAC, EAC,
BattlEye). It operates below and alongside them as an independent trust anchor.

The key insight: VAC/EAC/BattlEye protect the *game process* from unauthorized
modification. VAPI protects the *controller input pipeline* from injection, and
attests that a real human hand produced the inputs the game received. These are
orthogonal and complementary guarantees.

**VAPI's certification scope is precisely defined:**

> "Physical Human Controller Input (PHCI) certification: cryptographic proof that
> inputs reaching this game session originated from a physically-operated, hardware-
> identified controller without driver-level injection or macro automation."

This framing positions VAPI as **Layer 0 trust** — the ground truth beneath any
game anti-cheat layer — rather than a competing system.

---

## 2. The Proof of Innocence Model

A VAPI session produces an on-chain Proof of Autonomous Cognition (PoAC) chain.
Each 228-byte record is ECDSA-P256 signed with a key bound to the physical device's
secure element. At session end, the chain constitutes a signed audit trail that a
player can present as *proof of innocence* in a ban appeal.

### What the PoAC chain proves per session

| Absence of Code | Proves | Mechanism |
|-----------------|--------|-----------|
| No `INFER_CHEAT_INJ` (0x27) | No macro/script button injection | Timing pattern + IMU correlation |
| No `INFER_CHEAT_MAC` (0x23) | No hardware macro device | Sub-millisecond timing regularity detector |
| No `INFER_CHEAT_IMU` (0x26) | No XIM adapter / Cronus emulator | IMU-input phase correlation |
| No `INFER_DRIVER_INJECT` (0x28) | No vJoy/virtual device pipeline modification | HID-XInput discrepancy oracle |
| No `INFER_WALLHACK_PREAIM` (0x29) | No wallhack behavioral fingerprint | Backend behavioral model |
| No `INFER_AIMBOT_BEHAVIORAL` (0x2A) | No aimbot lock-on behavioral fingerprint | Backend behavioral model |

A clean session chain — all records NOMINAL (0x20) or SKILLED (0x21) — is a
machine-verifiable attestation that none of the above anomalies were detected.

### What the PoAC chain does NOT prove

- That the player is good at the game (that is SkillOracle's role)
- That game software was unmodified (that is VAC/EAC/BattlEye's role)
- That the player has never cheated (only this session)
- That the behavioral models are infallible (all are simulation-trained)

---

## 3. Five-Layer Attestation Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 5 (Phase 10): Side-Channel Integrity                         │
│  CPU timing anomaly, IMU-input phase relationship, timestamp        │
│  quantization — detects emulator artifacts at sub-millisecond scale │
│  Status: PLANNED                                                    │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4 (Phase 9): Game Telemetry Correlation                      │
│  Input-output discrepancy via game telemetry APIs — proves that     │
│  controller inputs actually produced expected game-state changes    │
│  Status: PLANNED (game-specific, pluggable adapter design)          │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3 (Phase 8 / Mechanism 2): Behavioral Backend Detection      │
│  tinyml_backend_cheat.py — 3-class model for wallhack/aimbot        │
│  behavioral fingerprints derived from stick velocity patterns       │
│  New codes: 0x29 WALLHACK_PREAIM, 0x2A AIMBOT_BEHAVIORAL           │
│  Status: IMPLEMENTED (simulation-trained; real-world validation TBD)│
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2 (Phase 8 / Mechanism 1): Input Pipeline Integrity          │
│  hid_xinput_oracle.py — compares raw HID vs XInput/DirectInput      │
│  game-visible values; detects driver-level vJoy injection           │
│  New code: 0x28 DRIVER_INJECT                                       │
│  Status: IMPLEMENTED (Windows; graceful no-op on Linux/macOS)       │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1 (Phases 1–7): Hardware Input Attestation         [CURRENT] │
│  Raw HID PoAC record — ECDSA-P256 signed — proves human timing,     │
│  IMU correlation, and absence of macro/injection/XIM patterns       │
│  Codes: 0x20 NOMINAL through 0x27 INJECTION                        │
│  Status: COMPLETE — 228-byte wire format, 169/169 contract tests    │
└─────────────────────────────────────────────────────────────────────┘
```

| Layer | What It Proves | Implementation | Status |
|-------|---------------|----------------|--------|
| 1 | Physical controller present; human timing; IMU correlation | `dualshock_emulator.py` + `train_anticheat_model.py` | Complete |
| 2 | Driver pipeline not modified (no vJoy/virtual injection) | `hid_xinput_oracle.py` | Phase 8 |
| 3 | No wallhack/aimbot behavioral fingerprints | `tinyml_backend_cheat.py` | Phase 8 |
| 4 | Controller inputs produced expected game-state changes | Game telemetry adapter (TBD) | Phase 9 |
| 5 | No side-channel artifacts (emulator-level anomalies) | CPU timing + quantization analysis | Phase 10 |

---

## 4. Critique Responses

### 4.1 Scope: "Anti-cheat" is too broad

**Position:** VAPI does not claim to detect all cheating. The correct framing is:

> **Physical Human Controller Input (PHCI) Certification**

VAPI certifies that:
- A hardware-identified physical controller was present
- Inputs were not injected at the driver level
- No macro or XIM emulation was detected
- No behavioral fingerprints of wallhack/aimbot patterns were observed

VAPI does not certify:
- That game memory was not modified
- That aimbots operating at the graphics/rendering level were absent
- That the player was "legitimate" by game server standards

This narrowed scope is a strength: it is achievable, verifiable, and complementary
to existing systems rather than competing with them.

### 4.2 Economics: Not a token speculation play

VAPI's economic model is **B2B data infrastructure**, not retail token speculation:

- **Esports operators**: Pay for on-chain SkillOracle tier attestation as a
  tournament eligibility gate (verified human controller input required)
- **Game studios**: License aggregated behavioral datasets for ML training
  (labeled by on-chain inference codes, player-consented, privacy-preserving)
- **Insurance / wagering platforms**: Use PoAC chain as dispute evidence
  (verifiable audit trail for contested match outcomes)
- **Players**: Zero-cost access to their own attestation chain; sell data opt-in

Token mechanics (if any) serve as settlement rail for B2B licensing, not as a
speculative asset. The attestation value is independent of token price.

### 4.3 DualSense Edge: PHCI Certified Primary Device

The DualSense Edge (CFI-ZCP1) is the **primary certified device** for VAPI's Physical
Input Trust Layer. It is a production PHCI implementation, not a development proxy.

Its adaptive triggers (L2/R2 motorized resistance surfaces) create a detection boundary
that is structurally unavailable to software injection: the trigger resistance curve state
is captured in every PoAC record's `sensor_commitment` field (sensor schema v2). A
software cheating tool can inject HID button events; it cannot reproduce the biomechanical
pressure profile of a human pressing a trigger against resistance. This is the adaptive
trigger detection surface — the strongest evidence of physical human presence currently
available in any certified controller.

Hardware-rooted signing (Phase 9) is achieved via a YubiKey PIV slot or ATECC608A I2C
secure element connected to the bridge host. The DualShock Edge provides the sensor
stream; the SE provides the unexportable signing key. Together they satisfy all four
capabilities of the VAPI device taxonomy:
- `sensor_stream`: IMU + sticks + adaptive triggers (kinematic/haptic schema v2)
- `signing_key`: YubiKey / ATECC608A (hardware-rooted, PIN/OTP-protected)
- `monotonic_counter`: SQLite-backed persistent counter in the bridge
- `network_bridge`: Python asyncio + Web3.py → IoTeX

The PITL architecture is controller-agnostic in principle: any HID-compliant device can
feed raw input to the attestation pipeline. The DualShock Edge is certified because it
provides the richest available detection surface, including the adaptive trigger signal
that no other production controller currently exposes.

### 4.4 TinyML accuracy: Simulation-trained models

**All performance metrics in Phase 8 are derived from synthetic training data.**
The heuristic classifiers and neural models in `train_anticheat_model.py` and
`tinyml_backend_cheat.py` are trained on analytical behavioral models, not labeled
real-world gameplay data.

**Qualification language applies throughout:**
- "Detects patterns consistent with..." (not "detects")
- "Behavioral fingerprints associated with..." (not "proves")
- "Simulation-validated; real-world performance requires labeled gameplay corpus"

The heuristic fallback classifiers provide interpretable, auditable rules that
can be reviewed and challenged — which is architecturally preferable to opaque
neural network decisions in an adversarial trust context.

---

## 5. Gamer Benefits

### Proof of Innocence

Players who run VAPI accumulate an on-chain chain of PoAC records per session.
If falsely accused of cheating, the player can:

1. Export their session's PoAC chain hash
2. Reference the on-chain record for independent verification
3. Present the chain as cryptographic evidence that no injection/macro/behavioral
   anomaly was detected by the PITL during the contested session

The chain is verifiable by anyone with access to the IoTeX blockchain — no
trusted intermediary required.

### Opt-In Voluntary Attestation

VAPI is entirely opt-in. Players who run it gain:
- SkillOracle tier badge (on-chain, persistent, transferable proof of consistent
  clean play)
- Eligibility for fair-play bounties (paid by esports operators for verified
  competitive sessions)
- Ban appeal evidence (clean chain weakens accusations of cheating)

Players who don't run it are unaffected. VAPI produces no negative signal about
players who are not enrolled — absence of attestation is not evidence of cheating.

### Data Sovereignty

Players own their attestation chain. Behavioral data collected by VAPI is:
- Stored locally (SQLite bridge database, player-controlled)
- Only submitted on-chain in aggregated hash form (not raw sensor data)
- Shared with third parties only via explicit player opt-in data licensing

---

## 6. Ecosystem Role

```
Game Server / VAC / EAC / BattlEye
        │ (game-process integrity)
        ▼
   ┌─────────────────────┐
   │   VAPI PITL Layer   │  ← Player-owned, hardware-rooted
   │  (PHCI Attestation) │     controller input trust anchor
   └─────────────────────┘
        │ (controller input integrity)
        ▼
Physical Controller Hardware
```

VAPI operates as **Layer 0 trust anchor**:

- Sits **below** game anti-cheat (which operates at process/OS level)
- Sits **above** raw controller hardware (attesting the HID→Game pipeline)
- Interoperable with **any game engine** (reads raw HID, not game APIs)
- Integrable with **any chain** (PoAC records are chain-agnostic; IoTeX is the
  reference deployment due to P256 precompile efficiency)

**SkillOracle integration:** On-chain ELO-inspired rating provides:
- Tournament eligibility gating (esports operator queries tier before seeding)
- Persistent reputation separate from any single game's ranking system
- Cross-game attestation (same device_id, same rating, any supported title)

**DAO integration:** Governance of inference thresholds, bounty parameters, and
tier requirements can be delegated to a DAO of enrolled players — creating a
community-governed fair-play standard rather than a corporate-controlled one.

---

## 7. Economic Model

### Revenue Streams (B2B)

| Customer | Value Proposition | Mechanism |
|----------|------------------|-----------|
| Esports tournament operators | PHCI-verified player eligibility | Query SkillOracle tier; gate entry |
| Game studios | Labeled behavioral training data | Opt-in data licensing API |
| Wagering / insurance platforms | Dispute evidence trail | PoAC chain export + on-chain verification |
| Anti-cheat vendors | Complementary layer 0 signal | Integration API (not competition) |

### Fair-Play Bounties

Players earn bounties for contributing verified clean sessions to high-demand
competitive pools. Operators post bounties (via BountyMarket.sol) specifying:
- Minimum SkillOracle tier required
- Session duration
- Reward amount (IOTX or operator token)

Players with matching tier and clean session inference codes automatically qualify
for bounty submission.

### What VAPI Is NOT

- Not a replacement for VAC/EAC/BattlEye (different attack surface)
- Not a token investment opportunity (no speculative tokenomics)
- Not a surveillance tool (player data stays local, on-chain only hashes)
- Not a centralized cheating judgment system (all logic on-chain and auditable)

---

## 8. Roadmap

### Phase 8 (Current): Input Pipeline Trust
- [x] Mechanism 1: HID-XInput Discrepancy Oracle (`hid_xinput_oracle.py`)
  - Detects driver-level injection (vJoy/virtual device)
  - Windows-only; graceful no-op on Linux/macOS
  - New inference code: 0x28 `DRIVER_INJECT`
- [x] Mechanism 2: Behavioral Backend Detection (`tinyml_backend_cheat.py`)
  - 3-class model: CLEAN / WALLHACK_PREAIM / AIMBOT_BEHAVIORAL
  - New inference codes: 0x29 `WALLHACK_PREAIM`, 0x2A `AIMBOT_BEHAVIORAL`
  - Simulation-trained; heuristic fallback for interpretability

### Phase 9 (Planned): Game Telemetry Correlation
- Pluggable game telemetry adapter interface
- Input-output discrepancy detection (controller input vs. character movement delta)
- Game-specific adapters for major titles (opt-in, game API dependent)
- Expected new inference codes in 0x2B–0x2F space

### Phase 10 (Research): Side-Channel Integrity
- CPU timing anomaly detection (emulator artifacts have quantized timing)
- IMU-input phase relationship analysis (physical motion precedes button press)
- Timestamp quantization analysis (real hardware has analog jitter; emulators don't)
- Potential hardware partnership for dedicated SE-equipped controller peripheral

---

*Document version: Phase 8.0 — February 2026*
*Status: Internal strategic positioning — pre-publication review*
