# VAPI DualShock Companion App — Product Specification

**Document Version:** 1.0.0
**Date:** 2026-02-16
**Classification:** Internal — Engineering
**Target Platforms:** DualSense Edge (CFI-ZCP1) — Primary, DualSense (CFI-ZCT1), Custom ESP32-S3 Mod

---

## 1. Executive Summary

The VAPI DualShock Companion App transforms a standard PlayStation controller into a **verifiable, autonomous anti-cheat agent** by running the full VAPI protocol stack (PoAC, three-layer cognitive agent, TinyML inference, economic bounty engine) on the controller's hardware. Every button press, joystick deflection, gyroscope reading, and trigger pull is committed into a cryptographically signed, hash-chained PoAC evidence record. A companion app (PC + mobile) serves as the human interface: live anti-cheat verification, bounty marketplace, replay forensics, and development tools.

**Core Value Proposition:** Tournament-grade, cryptographically verifiable proof of fair play—impossible to fake, trivial to verify, and economically incentivized through on-chain bounties.

### 1.1 Why This Matters

Current anti-cheat systems (EAC, BattlEye, Vanguard) operate as kernel-level software on the host PC. They are:
- **Privacy-invasive** — ring-0 access to the entire system
- **Bypassable** — hardware-level cheats (USB injection, DMA attacks) operate below the kernel
- **Opaque** — players cannot independently verify that opponents are clean
- **Centralized** — publisher-controlled, no independent audit

VAPI DualShock inverts the paradigm: verification happens **at the input device**, below the host OS, using cryptographic proofs that anyone can verify on a public blockchain. No kernel driver. No trust in the game publisher. No privacy compromise beyond gameplay inputs.

---

## 2. Hardware Requirements and Controller Anatomy

### 2.1 DualShock 4 (CUH-ZCT2) — Baseline Target

| Component | Specification | VAPI Usage |
|-----------|--------------|------------|
| Main MCU | Custom Sony SoC (ARM Cortex-M based) | Replaced by ESP32-S3 mod board |
| IMU | BMI055 (6-axis: accel + gyro) | Motion skill verification, tilt cheat detection |
| Touchpad | Capacitive, 2-point multitouch, 1920×942 | Gesture input, touch-pattern PoAC |
| Light Bar | RGB LED | Anti-cheat status indicator |
| Haptics | 2× ERM vibration motors | Cheat alert feedback, bounty notification |
| Audio | Mono speaker + 3.5mm jack | Audio beacon for proximity proof |
| Battery | 1000 mAh Li-ion (3.7V) | Energy budget for agent + crypto |
| Connectivity | Bluetooth 2.1+EDR, USB (Micro-B) | BLE 5.0 via ESP32-S3 mod |
| Buttons | 18 inputs (D-pad, face, shoulder, sticks, PS, Share, Options, Touchpad click) | Per-input timing PoAC |

### 2.2 DualSense (CFI-ZCT1) — Premium Target

| Component | Upgrade over DS4 | VAPI Usage |
|-----------|-------------------|------------|
| Haptics | Dual voice-coil actuators (LRA) | High-fidelity cheat alerts, skill feedback |
| Triggers | Adaptive triggers with motor resistance | Trigger-pull force profiling |
| Microphone | Built-in mic array | Voice command, audio proof-of-presence |
| USB | USB-C | Faster firmware flash, data dump |
| Battery | 1560 mAh | Extended agent runtime |
| Bluetooth | BT 5.1 | Lower latency, better range |

### 2.3 ESP32-S3 Mod Board — VAPI Compute Module

The stock controller MCU lacks the cryptographic acceleration and programmability needed. We interpose a custom compute module:

| Component | Specification |
|-----------|--------------|
| MCU | ESP32-S3-WROOM-1 (Xtensa LX7 dual-core @ 240 MHz) |
| SRAM | 512 KB internal + 8 MB PSRAM |
| Flash | 16 MB QSPI |
| Crypto | AES-XTS, SHA-256, RSA (hardware), **mbedTLS ECDSA-P256 (software)** |
| Wireless | WiFi 802.11 b/g/n + BLE 5.0 (on-chip) |
| GPIO | Intercepts controller bus (SPI/I²C to stock MCU) |
| Power | Fed from controller battery via LDO (3.3V, ~50 mA avg) |
| Physical | Custom flex PCB, mounts inside controller shell, <3g added weight |

**Architecture**: The ESP32-S3 sits between the stock MCU and the BLE/USB output. It reads all inputs from the stock MCU via SPI bus sniffing, runs the VAPI agent stack, generates PoAC records, and forwards inputs transparently to the host. The stock MCU continues to manage analog-to-digital conversion, haptics, and LED control — the ESP32-S3 is purely additive.

### 2.4 Hardware Block Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     DualSense Edge Shell                      │
│                                                              │
│  ┌──────────┐    SPI Bus    ┌──────────────────────────┐    │
│  │ Stock MCU│◄─────────────►│     ESP32-S3 Module       │    │
│  │          │    (sniff)    │                            │    │
│  │ - ADC    │               │  ┌──────────┐  ┌────────┐│    │
│  │ - Buttons│               │  │VAPI Agent│  │PoAC    ││    │
│  │ - IMU    │───────────────│  │ 3-Layer  │  │Engine  ││    │
│  │ - Touch  │  (input data) │  │          │  │        ││    │
│  │ - Haptic │◄──────────────│  │L1:Gaming │  │Sign    ││    │
│  │   driver │  (haptic cmd) │  │L2:AntiCheat│ │Chain  ││    │
│  │          │               │  │L3:Economic│  │Commit ││    │
│  └──────────┘               │  └──────────┘  └────────┘│    │
│       │                     │        │                  │    │
│       │                     │  ┌─────┴──────┐          │    │
│       │                     │  │   TinyML    │          │    │
│       │                     │  │ Anti-Cheat  │          │    │
│       │                     │  │ Classifier  │          │    │
│       │                     │  └─────────────┘          │    │
│       │                     │        │                  │    │
│       │                     │  ┌─────┴──────┐          │    │
│       │                     │  │  BLE 5.0   │          │    │
│       │                     │  │ (to App)   │          │    │
│       │                     │  └────────────┘          │    │
│       │                     └──────────────────────────┘    │
│       │                              │                      │
│       └──────────────────────────────┘                      │
│                  USB/BT to Host (transparent passthrough)    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Anti-Cheat Architecture — The Core Innovation

### 3.1 Threat Landscape

| Cheat Type | Description | Detection Method |
|-----------|-------------|------------------|
| **Aimbot** | Software auto-aims by injecting mouse/stick inputs | Impossible precision: <2ms reaction, sub-pixel tracking patterns |
| **Macro/Turbo** | Automated button sequences with perfect timing | Zero-variance inter-press timing, inhuman repetition consistency |
| **Cronus/XIM** | Hardware adapter translating M+KB to controller | Missing IMU correlation, impossible stick velocity profiles |
| **Recoil Script** | Automated recoil compensation | Perfectly inverse compensation pattern, no jitter variance |
| **Input Injection** | USB/DMA level input fabrication | Inputs lack corresponding IMU motion, PoAC chain breaks |
| **Replay Attack** | Re-submitting old gameplay inputs | Monotonic counter + timestamp freshness |
| **Lag Switch** | Intentional network disruption | Temporal gaps in PoAC chain, impossible action clustering |

### 3.2 PoAC Anti-Cheat Record — Gaming Extension

The standard 228-byte PoAC record is extended with gaming-specific fields for the `sensor_commitment` payload:

**Gaming Input Snapshot (committed via SHA-256 into `sensor_commitment`):**

| Field | Size | Description |
|-------|------|-------------|
| `button_state` | 3 B | Bitfield of all 18 button states |
| `left_stick_x` | 2 B | int16, left stick X deflection [-32768, 32767] |
| `left_stick_y` | 2 B | int16, left stick Y |
| `right_stick_x` | 2 B | int16, right stick X |
| `right_stick_y` | 2 B | int16, right stick Y |
| `l2_trigger` | 1 B | uint8, left trigger pressure [0, 255] |
| `r2_trigger` | 1 B | uint8, right trigger pressure [0, 255] |
| `gyro_x` | 4 B | float, gyroscope X (rad/s) |
| `gyro_y` | 4 B | float, gyroscope Y |
| `gyro_z` | 4 B | float, gyroscope Z |
| `accel_x` | 4 B | float, accelerometer X (g) |
| `accel_y` | 4 B | float, accelerometer Y |
| `accel_z` | 4 B | float, accelerometer Z |
| `touch_0_x` | 2 B | uint16, touch point 0 X [0, 1919] |
| `touch_0_y` | 2 B | uint16, touch point 0 Y [0, 941] |
| `touch_1_x` | 2 B | uint16, touch point 1 X |
| `touch_1_y` | 2 B | uint16, touch point 1 Y |
| `touch_active` | 1 B | Bitfield: touch 0 active, touch 1 active |
| `battery_mv` | 2 B | uint16, battery voltage in mV |
| `frame_counter` | 4 B | uint32, monotonic input frame number |
| `inter_frame_us` | 4 B | uint32, microseconds since last frame |
| **Total** | **50 B** | Deterministic, big-endian serialization |

This 50-byte snapshot is hashed into the PoAC `sensor_commitment` field. The full snapshot is stored locally for forensic replay; only the hash goes on-chain.

### 3.3 Inference Result Codes — Gaming Extension

| Code | Name | Description |
|------|------|-------------|
| `0x20` | `PLAY_NOMINAL` | Normal human gameplay detected |
| `0x21` | `PLAY_SKILLED` | High-skill play within human bounds |
| `0x22` | `CHEAT_REACTION` | Impossible reaction time detected (<150 ms sustained) |
| `0x23` | `CHEAT_MACRO` | Macro/turbo pattern detected (σ < 1ms timing) |
| `0x24` | `CHEAT_AIMBOT` | Aimbot-like stick movement (ballistic snap) |
| `0x25` | `CHEAT_RECOIL` | Perfect recoil compensation |
| `0x26` | `CHEAT_IMU_MISMATCH` | Stick input without corresponding controller motion |
| `0x27` | `CHEAT_INJECTION` | Input appears fabricated (no IMU, no touch, impossible timing) |
| `0x28` | `SKILL_COMBO_PERFECT` | Perfect combo execution (for bounty verification) |
| `0x29` | `SKILL_SPEEDRUN_SPLIT` | Speedrun split timestamp commitment |

### 3.4 Action Codes — Gaming Extension

| Code | Name | Description |
|------|------|-------------|
| `0x10` | `GAME_SESSION_START` | New gameplay session begins |
| `0x11` | `GAME_SESSION_END` | Session concludes |
| `0x12` | `CHEAT_ALERT` | Cheat detected, alert sent to companion app |
| `0x13` | `SKILL_PROOF` | Skill verification bounty evidence |
| `0x14` | `TOURNAMENT_FRAME` | Tournament-mode high-frequency PoAC |
| `0x15` | `CALIBRATION` | IMU/stick calibration event |

### 3.5 Anti-Cheat Detection Pipeline

```
Input Frame (1ms poll)
    │
    ├─► Ring Buffer (256 frames, ~12.8 KB)
    │
    ├─► Feature Extraction (every 50 frames = 50ms)
    │   ├── Reaction Time Analysis
    │   │   └── Time from stimulus-proxy (stick idle→active) to action (button)
    │   ├── Timing Variance Analysis
    │   │   └── σ(inter-press intervals) for repeated actions
    │   ├── Stick Velocity Profile
    │   │   └── Derivative of stick position, jerk analysis
    │   ├── IMU Correlation Score
    │   │   └── Cross-correlation(stick_movement, gyro_movement)
    │   └── Touch Pattern Entropy
    │       └── Shannon entropy of touch coordinates
    │
    ├─► TinyML Classifier (every 100 frames = 100ms)
    │   ├── Input: 30 features × 100-frame window
    │   ├── Model: INT8 quantized CNN, <60 KB flash, <24 KB RAM
    │   ├── Output: 8-class probability (nominal, skilled, 6 cheat types)
    │   └── Latency: <5 ms on ESP32-S3 @ 240 MHz
    │
    ├─► Decision + PoAC (every 500ms = 2 Hz in normal, 10 Hz in tournament)
    │   ├── Commit sensor snapshot hash
    │   ├── Commit world model (player skill profile over session)
    │   ├── Record inference result + action code
    │   ├── Sign with ECDSA-P256
    │   └── Queue for BLE transmission to companion app
    │
    └─► Haptic Alert (on cheat detection)
        ├── Light bar flash: RED
        └── Haptic pulse: 3× short bursts
```

### 3.6 World Model — Player Skill Profile

The gaming world model replaces environmental baselines with player performance metrics:

```c
typedef struct {
    /* Rolling statistics (circular buffer, 64 entries) */
    float    avg_reaction_ms[64];       /* Per-window average reaction times */
    float    avg_stick_precision[64];   /* Stick control precision score */
    float    avg_timing_variance[64];   /* Button timing consistency */
    float    imu_correlation[64];       /* IMU-input correlation score */
    uint8_t  head;
    uint8_t  count;

    /* Session aggregates */
    float    session_avg_reaction;      /* EMA, α=0.05 */
    float    session_avg_precision;
    float    session_skill_rating;      /* Composite skill score [0, 1000] */
    uint32_t total_frames;
    uint32_t total_sessions;
    uint32_t cheat_flags_triggered;     /* Cumulative cheat detections */
} gaming_world_model_t;
```

Hashed before each PoAC generation — captures evolving player profile for forensic analysis.

---

## 4. Sensor Mapping for Gaming

### 4.1 IMU → Motion Skill Verification

| Motion Feature | IMU Signal | Gaming Application |
|---------------|------------|-------------------|
| Controller tilt | Gyro X/Y | Motion-aim verification (Splatoon, BOTW) |
| Controller shake | Accel magnitude spike | Quick-time event proof |
| Steady hands | Gyro variance < threshold | Sniper accuracy verification |
| Left-right swing | Gyro Z | Racing wheel emulation proof |
| Physical presence | Baseline IMU noise floor | Confirms human is holding controller |

**Key Insight**: A controller on a desk (used with Cronus/XIM adapter) has a near-zero IMU noise floor. A hand-held controller always exhibits micro-tremor at 8–12 Hz. This physiological signal is the most powerful anti-cheat discriminator.

### 4.2 Button Timing → Macro Detection

Human button presses exhibit:
- **Inter-press interval variance**: σ > 5 ms for repeated actions
- **Press-release asymmetry**: Hold duration varies ±15% per press
- **Reaction time distribution**: Log-normal, μ ≈ 250 ms, σ ≈ 40 ms

Macros/turbos produce:
- **Zero or near-zero variance**: σ < 1 ms
- **Perfect periodicity**: FFT shows sharp spike at macro frequency
- **Inhuman speed**: Sustained >15 presses/second on a single button

### 4.3 Touchpad → Gesture Proof

The DualSense Edge touchpad provides 1920×942 resolution at ~133 Hz. VAPI commits touch trajectories into the sensor snapshot, enabling:
- **Gesture bounties**: "Draw a specific pattern" as proof-of-human
- **CAPTCHA alternative**: Touchpad challenges during suspicious play
- **Touch entropy**: Random touch input has high entropy; programmatic input is low-entropy

### 4.4 Haptics → Feedback Channel

| Event | DualShock 4 (ERM) | DualSense (LRA) |
|-------|-------------------|------------------|
| Cheat detected | 3× 100ms pulses | Distinct "alarm" waveform |
| Bounty accepted | 1× long rumble | Ascending haptic tone |
| PoAC chain break | Continuous vibration | Error pattern |
| Skill proof verified | 2× short pulses | "Success" waveform |
| Tournament mode active | Periodic soft pulse | Heartbeat pattern |

---

## 5. Economic Bounties — Gaming Applications

### 5.1 Bounty Types

| Category | Example | Verification Method |
|----------|---------|---------------------|
| **Anti-Cheat Proof** | "Play 100 clean matches" | 100 PoAC chains with all `PLAY_NOMINAL` inference |
| **Speedrun Verification** | "Complete level X in <Y seconds" | PoAC chain from `SESSION_START` to `SESSION_END` with timestamps |
| **Combo Mastery** | "Execute 50-hit combo in Tekken" | Frame-perfect button sequence in PoAC window |
| **Motion Skill** | "Score 3 goals using motion aiming" | IMU-correlated aim + goal events in PoAC |
| **Tournament Integrity** | "Provide PoAC for entire tournament bracket" | Complete chain, no gaps, no cheat flags |
| **Training Data** | "Contribute 1 hour of labeled gameplay data" | Sensor snapshots with game-state labels |

### 5.2 Bounty Lifecycle on DualShock

```
Discovery (BLE from companion app)
    │
    ├─► L2 Deliberative evaluates:
    │   ├── Battery sufficient? (need >20% for session)
    │   ├── Game compatible? (sensor requirements match)
    │   ├── Skill feasible? (world model says player can do it)
    │   └── Utility positive? (reward > energy + opportunity cost)
    │
    ├─► BOUNTY_ACCEPT PoAC record generated
    │
    ├─► L1 Reflexive collects evidence during gameplay
    │   └── Each PoAC with bounty_id set
    │
    ├─► Companion app relays to bridge → IoTeX
    │
    └─► BountyMarket.claimReward() on completion
```

### 5.3 Energy Cost Model — Gaming Profile

| Operation | Current Draw | Duration | mAh |
|-----------|-------------|----------|-----|
| Input poll (1 kHz) | 2 mA | Continuous | 2.0/hr |
| TinyML inference (10 Hz) | 15 mA | 5 ms each | 0.21/hr |
| SHA-256 (2 Hz PoAC) | 8 mA | 0.5 ms each | 0.002/hr |
| ECDSA-P256 sign (2 Hz) | 12 mA | 3 ms each | 0.02/hr |
| BLE transmission (2 Hz) | 10 mA | 2 ms each | 0.011/hr |
| ESP32-S3 baseline | 35 mA | Continuous | 35.0/hr |
| **Total VAPI overhead** | — | — | **~37.2 mAh/hr** |
| **Stock controller** | — | — | **~50 mAh/hr** |
| **Combined** | — | — | **~87.2 mAh/hr** |

With a 1000 mAh battery (DS4), VAPI reduces battery life from ~20 hours to ~11.5 hours. With the DualSense's 1560 mAh, runtime is ~17.9 hours. This is acceptable for gaming sessions (2–4 hours typical).

---

## 6. Companion App Architecture

### 6.1 Platform Support

| Platform | Framework | Transport |
|----------|-----------|-----------|
| Windows PC | Python + FastAPI + Electron shell | BLE (bleak) + USB serial |
| macOS | Same + native BLE via bleak | BLE + USB |
| Android | Kotlin + Jetpack Compose | BLE native |
| iOS | Swift + SwiftUI | BLE CoreBluetooth |

### 6.2 Core Modules

```
┌─────────────────────────────────────────────────────┐
│                 VAPI Companion App                    │
│                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │  BLE Manager │  │  PoAC Viewer │  │ Bounty Mgr  │ │
│  │             │  │              │  │             │ │
│  │ - Pair      │  │ - Live chain │  │ - Browse    │ │
│  │ - Reconnect │  │ - Verify     │  │ - Accept    │ │
│  │ - OTA flash │  │ - Export     │  │ - Track     │ │
│  │ - Raw debug │  │ - Forensics  │  │ - Claim     │ │
│  └──────┬──────┘  └──────┬───────┘  └──────┬──────┘ │
│         │                │                  │        │
│  ┌──────┴────────────────┴──────────────────┴──────┐ │
│  │              Core Service Layer                   │ │
│  │                                                   │ │
│  │  - PoAC chain validation (local)                  │ │
│  │  - Bridge relay (to IoTeX via MQTT/HTTP)          │ │
│  │  - SQLite local storage                           │ │
│  │  - WebSocket real-time updates                    │ │
│  └──────────────────────┬────────────────────────────┘ │
│                         │                              │
│  ┌──────────────────────┴────────────────────────────┐ │
│  │              Developer Tools                       │ │
│  │                                                    │ │
│  │  - Firmware flasher (esptool wrapper)              │ │
│  │  - TinyML model trainer (Edge Impulse API)         │ │
│  │  - Input visualizer (real-time stick/IMU plots)    │ │
│  │  - Anti-cheat tuning (threshold editor + replay)   │ │
│  │  - PoAC chain inspector (search, filter, export)   │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 6.3 BLE Protocol — VAPI Gaming Service

| Characteristic UUID | Name | Properties | Description |
|-------------------|------|------------|-------------|
| `0xVA01` | PoAC Record | Notify | 228-byte PoAC records streamed at 2 Hz |
| `0xVA02` | Input Snapshot | Notify | 50-byte raw input (optional, for dev mode) |
| `0xVA03` | Anti-Cheat Status | Read/Notify | Current inference result + confidence |
| `0xVA04` | Agent State | Read | Current agent state machine state |
| `0xVA05` | Bounty Command | Write | Inject bounty, configure agent |
| `0xVA06` | Config | Read/Write | Agent config (thresholds, intervals) |
| `0xVA07` | Firmware OTA | Write/Indicate | OTA firmware update channel |
| `0xVA08` | World Model | Read | Serialized gaming world model |

**Throughput**: At 2 Hz PoAC (228 B each) + metadata overhead, BLE 5.0 at 2 Mbps PHY provides >10× headroom.

### 6.4 Dashboard Views

**Live Session View**: Real-time anti-cheat status, PoAC chain visualization (blocks linked by arrows), current inference confidence, skill rating trend graph.

**Chain Explorer**: Searchable/filterable table of all PoAC records. Click to expand: full sensor snapshot, world model at decision time, on-chain verification status.

**Bounty Marketplace**: Browse available gaming bounties from IoTeX. Filter by game, reward, difficulty. One-click accept sends to controller via BLE.

**Developer Console**: Serial monitor, firmware flash (drag-and-drop .bin), TinyML model upload, threshold tuning sliders with live preview, raw input oscilloscope.

---

## 7. Tournament Integration

### 7.1 Tournament Mode

When activated (via companion app or physical button combo), the controller enters high-frequency PoAC mode:

| Parameter | Normal Mode | Tournament Mode |
|-----------|-------------|-----------------|
| PoAC generation rate | 2 Hz | 10 Hz |
| TinyML inference rate | 10 Hz | 20 Hz |
| BLE stream rate | 2 Hz | 10 Hz |
| Input buffer depth | 256 frames | 1024 frames |
| Battery drain | ~37 mAh/hr overhead | ~65 mAh/hr overhead |

### 7.2 Tournament Oracle

Multiple controllers in a tournament match submit PoAC chains to the same `bounty_id` (tournament match). The `BountyMarket.aggregateSwarmReport()` function provides:
- **Match integrity score**: Derived from all participants' clean PoAC chains
- **Consensus**: All devices agree on "no cheat detected"
- **Timestamp correlation**: Chains overlap in time, confirming simultaneous play
- **PhysicalOracleReport event**: Other contracts can consume this as proof-of-fair-match

### 7.3 Speedrun Verification

Speedrun PoAC chains provide:
1. `GAME_SESSION_START` record with timestamp T₀
2. Continuous chain of gameplay PoAC records (every 500ms)
3. `SKILL_SPEEDRUN_SPLIT` records at key checkpoints
4. `GAME_SESSION_END` record with timestamp T₁
5. All records signed by the same device key
6. All records show `PLAY_NOMINAL` or `PLAY_SKILLED` inference (no cheat flags)
7. Verifiable on-chain: anyone can check T₁ − T₀ and chain integrity

This makes VAPI the first **cryptographically verifiable speedrun timer** — no video evidence needed, no moderator trust required.

---

## 8. Privacy Considerations

### 8.1 What Goes On-Chain

- PoAC record hashes (32 bytes each)
- Inference results (cheat/clean classification)
- Action codes (session start/end, bounty events)
- Timestamps (session-level, not real-time)
- Device public key (pseudonymous)

### 8.2 What Stays Local

- Raw sensor snapshots (button states, stick positions, IMU)
- Touch coordinates
- World model (player skill profile)
- Full PoAC records (only hashes go on-chain unless evidence is submitted)
- Replay data

### 8.3 Privacy Guarantees

- **Pseudonymous**: Device key is not linked to PSN/Xbox/Steam identity unless the user chooses to link
- **Selective disclosure**: Users choose which PoAC chains to submit (for bounties or tournaments)
- **No gameplay content**: The system proves *how* inputs were made, not *what game* was played
- **Opt-in only**: VAPI does not transmit anything without explicit user activation

---

## 9. Regulatory and Compatibility

### 9.1 Console Compatibility

| Console | Status | Notes |
|---------|--------|-------|
| PS4 | Compatible | DS4 mod board, transparent passthrough |
| PS5 | Compatible | DualSense mod board, must preserve DualSense auth chip |
| PC (Steam) | Fully compatible | No auth requirements for PC controllers |
| Xbox (via adapter) | Partial | Requires Titan/CronusMAX adapter (ironic, but needed for Xbox input) |
| Nintendo Switch | Compatible | Pro controller protocol adapter |

### 9.2 Terms of Service

Sony's PS4/PS5 TOS prohibits hardware modification of controllers. VAPI DualShock is therefore **primarily a PC peripheral** for competitive PC gaming (CS2, Valorant, Fortnite, etc.) and esports tournaments. Console use is at the user's own risk.

### 9.3 FCC/CE Compliance

The ESP32-S3 module is FCC/CE pre-certified (ESP32-S3-WROOM-1 has FCC ID: 2AC7Z-ESP32S3). The mod does not add new RF emissions beyond the module's certified parameters. However, integration inside the controller shell may require intentional radiator re-testing depending on jurisdiction.

---

## 10. Roadmap

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| **Phase 0: Prototype** | Weeks 1–4 | ESP32-S3 dev board + DS4 controller, wired SPI tap, PC companion app |
| **Phase 1: Integrated** | Weeks 5–10 | Custom flex PCB inside DS4 shell, BLE pairing, TinyML anti-cheat v1 |
| **Phase 2: Smart Contracts** | Weeks 8–12 | Deploy gaming PoACVerifier + BountyMarket on IoTeX testnet |
| **Phase 3: Companion App** | Weeks 10–16 | Full PC app (Electron + FastAPI), Android app (Kotlin) |
| **Phase 4: Tournament** | Weeks 14–20 | Tournament mode, speedrun verification, multi-controller oracle |
| **Phase 5: Production** | Weeks 18–26 | Manufacturing, certification, launch |

---

**Document End — VAPI DualShock Product Specification v1.0.0**
