# Physical Input Trust Layer (PITL) — User Guide

## Phase 8 Bridge Documentation

---

## 1. What Is The Physical Input Trust Layer?

The Physical Input Trust Layer is a **player-owned, hardware-rooted attestation
system** that produces cryptographic proof that your controller inputs are legitimate.
It sits below existing anti-cheat systems (VAC, EAC, BattlEye) as an independent
Layer 0 trust anchor, not competing with them but complementing them.

### The 5-Layer Architecture

| Layer | What It Proves | Status |
|-------|---------------|--------|
| **L1** Hardware Input Attestation | Controller present; human timing; no XIM/Cronus | Active |
| **L2** Input Pipeline Integrity | No vJoy/virtual driver injection between HID and game | Phase 8 |
| **L3** Behavioral Backend Detection | No wallhack/aimbot behavioral fingerprints | Phase 8 |
| **L4** Game Telemetry Correlation | Controller input → game state causality | Phase 9 |
| **L5** Side-Channel Integrity | CPU timing, emulator-level artifacts | Phase 10 |

### Who Benefits?

- **Competitive players**: Accumulate a cryptographic proof of legitimate play per session
- **Falsely banned players**: Present on-chain audit trail as ban appeal evidence
- **Tournament organizers**: Gate entry with verified SkillOracle tier
- **Content creators**: Prove runs are legitimate without trusting a central authority

---

## 2. Quick Start

Enable Phase 8 mechanisms with two environment variables — no code changes required:

```bash
# Enable HID-XInput oracle (Windows only; graceful no-op on Linux/macOS)
HID_ORACLE_ENABLED=true

# Enable behavioral backend cheat detection
BACKEND_CHEAT_ENABLED=true

# Then start the bridge as normal
python -m vapi_bridge.main
```

Both mechanisms are **disabled by default**. They activate only when explicitly
enabled, and degrade gracefully if dependencies are unavailable.

---

## 3. Proof of Innocence: Using Your PoAC Chain

### What Your Chain Proves

Every session you run with VAPI produces a chain of 228-byte PoAC records, each
ECDSA-P256 signed with a key bound to your device. The chain is anchored on-chain
(IoTeX) and is independently verifiable.

A clean session — all records NOMINAL (0x20) or SKILLED (0x21) — proves:

| Absent Code | Meaning |
|-------------|---------|
| `CHEAT:INJECTION` (0x27) | No macro/script injection detected |
| `CHEAT:MACRO` (0x23) | No hardware macro device detected |
| `CHEAT:IMU_MISS` (0x26) | No XIM adapter / Cronus emulator detected |
| `DRIVER_INJECT` (0x28) | No driver-level input pipeline modification |
| `WALLHACK_PREAIM` (0x29) | No wallhack behavioral fingerprint |
| `AIMBOT_BEHAVIORAL` (0x2A) | No aimbot lock-on behavioral fingerprint |

### How to Export Your Chain for a Ban Appeal

1. **Get your device ID:**
   ```bash
   python -c "
   from pathlib import Path
   from vapi_bridge.config import Config
   import sys; sys.path.insert(0, 'controller')
   from persistent_identity import PersistentIdentity
   cfg = Config()
   ident = PersistentIdentity(key_dir=Path(cfg.dualshock_key_dir)).load_or_create()
   print('Device ID:', ident.device_id.hex())
   "
   ```

2. **Export session records from the bridge database:**
   ```bash
   sqlite3 ~/.vapi/bridge.db \
     "SELECT hex(raw_record), submitted_at FROM poac_records \
      WHERE device_id = '<your_device_id>' \
      ORDER BY submitted_at DESC LIMIT 50;" > my_session_proof.txt
   ```

3. **Verify on-chain:** Use the IoTeX explorer at `testnet.iotexscan.io` (testnet)
   or `iotexscan.io` (mainnet) with your device_id and the transaction hash from
   your session.

4. **Present the chain hash:** The PoACVerifier contract stores a hash of each batch.
   A verifier can independently confirm your records were accepted on-chain.

---

## 4. Mechanism 1: Input Pipeline Integrity (HID-XInput Oracle)

### What It Detects

Software injection tools (vJoy, x360ce, reWASD in injection mode, custom drivers)
sit between your physical controller and the game. They can intercept and modify
inputs before the game sees them. The HID-XInput oracle detects this by comparing:

- **Raw HID values** — what your physical controller actually sent over USB
  (read directly by pydualsense)
- **XInput values** — what the game receives via Windows DirectX/XInput API

On an unmodified system, these match within tolerance. A persistent discrepancy
indicates the input pipeline has been modified.

### System Requirements

- **Windows only.** The oracle gracefully disables itself on Linux/macOS.
- **XInput1_4.dll** (Windows 8+, built-in) or XInput9_1_0.dll (Windows 7)
- Your controller must be detected as a gamepad by Windows XInput (Steam may
  present the DualSense Edge as a virtual XInput device — this is the expected
  scenario; the oracle compares against whatever the game sees)

### Configuration

```bash
HID_ORACLE_ENABLED=true            # Enable oracle (default: false)
HID_ORACLE_THRESHOLD=0.15          # Normalized discrepancy threshold (default: 0.15)
HID_ORACLE_GAMEPAD_INDEX=0         # XInput controller slot 0-3 (default: 0)
```

**Threshold guidance:**
- `0.15` (default) — appropriate for typical setups; Steam virtual gamepad adds
  ~5–8% remapping overhead, well below this threshold
- `0.25` — use if you get false positives with a remapping layer you trust
- `0.10` — stricter; use in tournament environments

### How It Works

The oracle runs in the same polling thread as frame collection. Each raw HID frame
is compared against XInput state in real-time. Sustained discrepancy across
`window_size` consecutive frames (default: 30 frames ≈ 250 ms at 120 Hz) triggers
a `DRIVER_INJECT` (0x28) classification.

---

## 5. Mechanism 2: Behavioral Backend Detection

### What Patterns It Looks For

The behavioral classifier (`tinyml_backend_cheat.py`) analyzes stick velocity patterns
over a 5-second rolling window to identify:

**WALLHACK_PREAIM (0x29):**
Wallhack users physically aim toward occluded enemies before they're visible. This
creates a characteristic stop-start pattern: smooth tracking velocity that stops
precisely when the enemy appears (aim already on target). Detectable as:
- High count of abrupt velocity-to-zero transitions
- Consistently sustained tracking runs (not random exploration)
- Pre-aim movement before direction changes

**AIMBOT_BEHAVIORAL (0x2A):**
Aimbot-assisted players typically show micro-correction patterns after the aimbot
snaps: a large rapid movement followed by a tiny human correction. Detectable as:
- High jerk magnitude events followed by micro-velocity tails (0.01–0.05 normalized)
- Very short lag between snap and correction (< 50 ms)
- Near-zero velocity variance after snapping to target

### Limitations — Read This First

> **IMPORTANT:** The behavioral models in Phase 8 are trained entirely on
> **synthetically generated data** derived from analytical models of expected
> behavior patterns. They have **not been validated against real gameplay data**
> with labeled cheating/clean sessions.
>
> The heuristic fallback rules (in `_heuristic_classify`) are interpretable and
> auditable — if flagged, you can inspect exactly which thresholds were triggered.
>
> These detectors should be treated as **advisory signals** that contribute to
> your overall attestation chain, not as definitive proof of cheating.

### Configuration

```bash
BACKEND_CHEAT_ENABLED=true                        # Enable (default: false)
BACKEND_CHEAT_MODEL_PATH=backend_cheat_model.tflite  # Optional TFLite model
```

If `BACKEND_CHEAT_MODEL_PATH` is empty or the file doesn't exist, the heuristic
fallback classifier runs automatically. To train the TFLite model:

```bash
cd controller
python tinyml_backend_cheat.py --train --output backend_cheat_model.tflite
```

Requires TensorFlow: `pip install tensorflow`

---

## 6. Configuration Reference

All Phase 8 configuration is via environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `HID_ORACLE_ENABLED` | `false` | Enable HID-XInput discrepancy oracle |
| `HID_ORACLE_THRESHOLD` | `0.15` | Normalized discrepancy threshold [0, 1] |
| `HID_ORACLE_GAMEPAD_INDEX` | `0` | XInput controller slot (0–3) |
| `BACKEND_CHEAT_ENABLED` | `false` | Enable behavioral backend detection |
| `BACKEND_CHEAT_MODEL_PATH` | `""` | Path to TFLite model (empty = heuristic) |

Add to your `.env` file (copy from `bridge/.env.testnet` for testnet setup):

```bash
# Phase 8: Physical Input Trust Layer
HID_ORACLE_ENABLED=true
HID_ORACLE_THRESHOLD=0.15
HID_ORACLE_GAMEPAD_INDEX=0
BACKEND_CHEAT_ENABLED=true
BACKEND_CHEAT_MODEL_PATH=
```

---

## 7. Understanding Inference Codes

Complete table of all VAPI inference codes (0x20–0x2A):

| Code | Hex | Name | ELO Effect | Description |
|------|-----|------|------------|-------------|
| 32 | 0x20 | `NOMINAL` | +1–5 | Clean session, normal human input |
| 33 | 0x21 | `SKILLED` | +1–12 | Exceptionally clean, high precision |
| 34 | 0x22 | `CHEAT:REACTION` | -200 | Superhuman reaction time pattern |
| 35 | 0x23 | `CHEAT:MACRO` | -200 | Hardware macro device detected |
| 36 | 0x24 | `CHEAT:AIMBOT` | -200 | Aimbot snap-to-target pattern |
| 37 | 0x25 | `CHEAT:RECOIL` | -200 | Automated recoil compensation |
| 38 | 0x26 | `CHEAT:IMU_MISS` | -200 | IMU-input mismatch (XIM/Cronus) |
| 39 | 0x27 | `CHEAT:INJECTION` | -200 | Button/stick injection detected |
| 40 | 0x28 | `CHEAT:DRIVER_INJECT` | -200 | HID-XInput pipeline modified |
| 41 | 0x29 | `CHEAT:WALLHACK_PREAIM` | -200 | Behavioral wallhack fingerprint |
| 42 | 0x2A | `CHEAT:AIMBOT_BEHAVIORAL` | -200 | Behavioral aimbot fingerprint |

---

## 8. Limitations and Honest Scope

**What VAPI PITL can detect:**
- Driver-level input injection (vJoy, virtual devices) — Layer 2
- Hardware macro devices and XIM/Cronus adapters — Layer 1
- Behavioral patterns consistent with wallhack/aimbot usage — Layer 3 (simulation-trained)

**What VAPI PITL cannot detect:**
- Game memory modification (ESP, aim assists operating in-game) — VAC/EAC domain
- Server-side cheating (unauthorized game server access) — game developer domain
- GPU-based cheats that operate at the rendering level — undetectable from controller
- Cheating on future sessions after attestation period
- Whether a player is "skilled" or "legitimate" by game standards (that's SkillOracle)

**VAPI is not a replacement for VAC/EAC/BattlEye.** It certifies the physical input
pipeline only. A player could have a modified game binary and still produce clean VAPI
attestations. The systems are orthogonal and intended to be used together.

**The behavioral models are simulation-trained.** Phase 8 behavioral detectors
(WALLHACK_PREAIM, AIMBOT_BEHAVIORAL) have not been validated on real adversarial
gameplay data. Treat their output as advisory, not definitive.

---

## 9. For Esports Operators

### SkillOracle Tier Gating

Require a minimum SkillOracle tier for tournament entry. The tier is computed
from accumulated on-chain ELO, which is penalized (-200) for every cheat detection.
A player who has cheated will have a degraded tier.

```python
# Example: require Silver (tier ≥ 1, rating ≥ 1000) for tournament entry
from web3 import Web3
w3 = Web3(Web3.HTTPProvider("https://babel-api.mainnet.iotex.io"))
oracle = w3.eth.contract(address=SKILL_ORACLE_ADDRESS, abi=SKILL_ORACLE_ABI)
rating, tier = oracle.functions.getRating(device_id_bytes32).call()
# tier: 0=Bronze, 1=Silver, 2=Gold, 3=Platinum, 4=Diamond
assert tier >= 1, "Player does not meet minimum tier requirement"
```

### Audit Trail Export

Export a player's PoAC chain for a specific match window:

```bash
# Export records from the last 60 minutes for a given device ID
sqlite3 ~/.vapi/bridge.db \
  "SELECT hex(raw_record), submitted_at, tx_hash
   FROM poac_records
   WHERE device_id = '<device_id>'
     AND submitted_at > datetime('now', '-60 minutes')
   ORDER BY submitted_at;" > match_audit.csv
```

### On-Chain Verification

The `PoACVerifier.sol` contract stores a hash of every submitted batch. To verify:

```bash
# Verify a record was accepted on-chain
cast call $POAC_VERIFIER_ADDRESS \
  "submittedHashes(bytes32)(bool)" <record_sha256_hash> \
  --rpc-url https://babel-api.mainnet.iotex.io
```

Returns `true` if the record was accepted; `false` if not found or rejected.

---

## 10. Roadmap

### Phase 9: Game Telemetry Correlation (Planned)
- Pluggable game telemetry adapter interface
- Input-output discrepancy detection: does your controller input produce the expected
  character movement delta in the game?
- Detects cases where inputs are silently discarded or amplified by cheat software
- New inference codes in 0x2B–0x2F space

### Phase 10: Side-Channel Integrity (Research)
- CPU timing anomaly detection — real hardware has analog jitter; emulators don't
- IMU-input phase relationship — physical motion precedes button press
- Timestamp quantization analysis — emulators have quantized clock artifacts
- Potential hardware partnership for dedicated secure element controller peripheral

---

*Guide version: Phase 8.0 — February 2026*
*Feedback: github.com/[project]/issues*
