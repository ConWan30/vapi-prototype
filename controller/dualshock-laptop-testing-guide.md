# VAPI DualSense Edge -- Hardware Testing Guide v3.0

**Hardware Required:** DualSense Edge controller (CFI-ZCP1) + Windows/macOS/Linux laptop
**No additional purchases.** The laptop runs the VAPI agent stack; the controller provides real inputs.

---

## 1. Prerequisites

### 1.1 Install Python Dependencies

```bash
cd C:\Users\Contr\vapi-pebble-prototype\controller

# Required -- controller access + cryptographic signing:
pip install cryptography pydualsense

# Optional -- for companion app dashboard:
pip install fastapi uvicorn websockets aiosqlite
```

### 1.2 Connect DualSense Edge

**USB (recommended for first test):**
1. Plug the DualSense Edge into your laptop via USB-C cable.
2. Windows should auto-install the HID driver. No extra software needed.
3. Verify: Device Manager -> Human Interface Devices -> "Wireless Controller"

**Bluetooth:**
1. Put the DualSense Edge in pairing mode: Hold **Create** + **PS** until the light bar flashes blue rapidly.
2. On your laptop: Settings -> Bluetooth -> Add Device -> Select "Wireless Controller."
3. The light bar should turn solid when connected.

### 1.3 Verify Controller Connection

```bash
python -c "from pydualsense import pydualsense; ds = pydualsense(); ds.init(); print('Connected!'); ds.close()"
```

If this prints "Connected!" you are ready to test.

**Troubleshooting:**
- **Windows:** Install [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases) if `pydualsense` can't find the controller.
- **Linux:** You may need `sudo` or a udev rule: `echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="054c", MODE="0666"' | sudo tee /etc/udev/rules.d/99-dualsense.rules && sudo udevadm control --reload`
- **macOS:** Grant Terminal/IDE access in System Settings -> Privacy -> Input Monitoring.

---

## 2. Quick Start

### 2.1 Run the Full Hardware Test Suite

```bash
# Auto-detect controller and run all 72 tests:
python dualshock-laptop-testing-suite.py

# With real controller connected -- full hardware validation:
python dualshock-laptop-testing-suite.py --interactive --verbose
```

### 2.2 Run Without Controller (Simulation Mode)

```bash
# All 72 tests pass; hardware tests use synthetic data:
python dualshock-laptop-testing-suite.py --simulate
```

### 2.3 Run the Live Emulator

```bash
# Simulated inputs (no controller):
python dualshock_emulator.py --simulate --duration 30

# Real controller inputs:
python dualshock_emulator.py --duration 60 --export session.json
```

---

## 3. Test Suite Modes

| Mode | Flag | Controller Needed | HW Tests |
|------|------|-------------------|----------|
| Auto-detect | *(none)* | Optional | PASS if connected, SKIP if not |
| Simulate | `--simulate` | No | Use synthetic data (PASS/sim) |
| Interactive | `--interactive` | Yes | Prompts to move sticks, press buttons |
| Verbose | `--verbose` | Either | Show detailed diagnostics |
| Phase select | `--phase N` | Either | Run only phase N (1-11) |

**Examples:**
```bash
python dualshock-laptop-testing-suite.py --phase 1               # HW discovery only
python dualshock-laptop-testing-suite.py --phase 3               # Anti-cheat only
python dualshock-laptop-testing-suite.py --phase 9               # Live PoAC monitoring
python dualshock-laptop-testing-suite.py --phase 10              # HW anti-cheat validation
python dualshock-laptop-testing-suite.py --phase 11              # Bounty + export
python dualshock-laptop-testing-suite.py --interactive --phase 2  # Interactive input tests
python dualshock-laptop-testing-suite.py --simulate --verbose     # Full sim with details
```

---

## 4. Phase-by-Phase Guide

### Phase 1: Hardware Discovery & Connection (5 tests)

Tests controller detection, connection type (USB/BT), battery level, polling latency, and device state initialization.

**With controller:** All 5 tests run against real hardware.
**Without controller:** All 5 tests SKIP (or PASS in `--simulate` mode).

| Test | What It Validates | Pass Criteria |
|------|-------------------|---------------|
| 1.1 Controller detection | HID device found via pydualsense | Connected or simulated |
| 1.2 Connection type | USB vs Bluetooth (inferred from latency) | Identified |
| 1.3 Battery level | Battery voltage readable | 0-100% reported |
| 1.4 Polling latency | Input read round-trip time | < 5ms average |
| 1.5 Device state init | All fields have valid types | int/float types correct |

### Phase 2: Live Input Capture & Fidelity (8 tests)

**Non-interactive:** Reads current state, validates types and ranges.
**Interactive (`--interactive`):** Prompts you to move sticks, press triggers, tilt controller.

| Test | Non-Interactive | Interactive |
|------|-----------------|-------------|
| 2.1 Left stick | Validate int16 range | "Move LEFT stick in full circle" -> verify span > 40000 |
| 2.2 Right stick | Validate int16 range | "Move RIGHT stick in full circle" -> verify span > 40000 |
| 2.3 Triggers | Validate [0, 255] | "Press L2/R2 fully" -> verify max > 200 |
| 2.4 IMU gyro | Validate non-NaN | "Tilt controller left/right" -> verify peak > 0.05 |
| 2.5 Gravity vector | Read accelerometer -> ~1g | "Hold still" -> verify |accel| ~ 1.0 |
| 2.6 Button bitmap | 24-bit encoding test | Same (algorithmic) |
| 2.7 Serialization | Deterministic round-trip | Same (algorithmic) |
| 2.8 Frame timing | 100-frame timing consistency | Same (live or synthetic) |

### Phase 3: Anti-Cheat -- Synthetic Patterns (8 tests)

All algorithmic -- no controller needed. Uses the TestClassifier with frame-based timing for deterministic results.

| Test | Synthetic Pattern | Expected Detection |
|------|------------------|--------------------|
| 3.1 NOMINAL | Gentle stick + IMU + random buttons | NOMINAL (220/255) |
| 3.2 SKILLED | Faster, more precise stick + IMU | NOMINAL (220/255) |
| 3.3 MACRO | Perfect 10-frame button periodicity | CHEAT:MACRO (230/255) |
| 3.4 AIMBOT | Full-range stick snaps every 3 frames | CHEAT:AIMBOT (180/255) |
| 3.5 IMU_MISS | Jerky stick + zero gyro (adapter) | CHEAT:IMU_MISS (200/255) |
| 3.6 INJECTION | Smooth sine stick + zero IMU | CHEAT:INJECTION (210/255) |
| 3.7 FPR | 10x normal gameplay trials | 0% false positive rate |
| 3.8 Confidence | Macro pattern confidence check | >= 180/255 |

### Phase 4: PoAC Record Generation (8 tests)

7 algorithmic + 1 hardware test. Validates the 228-byte record format, SHA-256 chain linkage, ECDSA signing, and live PoAC generation from real input.

| Test | What It Validates |
|------|-------------------|
| 4.1 Record size | body=164B, full=228B |
| 4.2 SHA-256 commitment | sensor_commitment matches SHA-256(input) |
| 4.3 World model hash | Hash changes after update |
| 4.4 Chain linkage | 50 records linked via SHA-256(prev_full) |
| 4.5 Monotonic counter | Strictly increasing across chain |
| 4.6 ECDSA-P256 | Sign body + verify with public key |
| 4.7 Binary export | 228B-aligned concatenation |
| 4.8 [HW] Live PoAC | Generate 228B record from live controller input |

### Phase 5: Contract Simulation -- SkillOracle (6 tests)

All algorithmic. Python simulation mirrors SkillOracle.sol exactly.

| Test | What It Validates |
|------|-------------------|
| 5.1 Initial profile | rating=1000, tier=Silver |
| 5.2 NOMINAL gain | +floor(5 * 220 / 255) = +4 |
| 5.3 SKILLED gain | +floor(12 * 200 / 255) = +9 |
| 5.4 CHEAT penalty | -200 (floor at 0) |
| 5.5 Tier progression | Silver -> Gold -> Platinum -> Diamond |
| 5.6 Rating ceiling | Clamped at 3000 |

### Phase 6: Contract Simulation -- Progress & Teams (6 tests)

All algorithmic. Tests ProgressAttestation and TeamProofAggregator.

| Test | What It Validates |
|------|-------------------|
| 6.1 Progress attestation | Baseline vs current record pair |
| 6.2 All 4 MetricTypes | REACTION_TIME, ACCURACY, CONSISTENCY, COMBO_EXECUTION |
| 6.3 Duplicate rejection | Same pair cannot be attested twice |
| 6.4 Team creation | 4-member team registration |
| 6.5 Merkle root | Sort + pairwise SHA-256 for 4 leaves |
| 6.6 Team proof lifecycle | Create team -> verify records -> submit -> get proof ID |

### Phase 7: Feedback, Haptics & Bounties (5 tests)

4 hardware + 1 algorithmic. Tests LED color changes, haptic rumble, and bounty fulfillment.

**With controller connected:**
- Light bar turns **GREEN** (clean state)
- Light bar turns **RED** (cheat alert)
- Controller **RUMBLES** for 0.5 seconds
- Light bar resets to **BLUE** (idle)

**Without controller:** Tests SKIP or use simulation.

| Test | What You See/Feel |
|------|-------------------|
| 7.1 LED green | Light bar turns green |
| 7.2 LED red | Light bar turns red |
| 7.3 Haptic rumble | Controller vibrates for 0.5s |
| 7.4 LED reset | Light bar returns to blue |
| 7.5 Bounty fulfillment | Bounty accepted -> 10 samples -> complete |

### Phase 8: End-to-End Pipeline (8 tests)

4 algorithmic + 2 hardware + 2 benchmarks. Full session lifecycle with optional live controller session.

| Test | What It Validates |
|------|-------------------|
| 8.1 Session lifecycle | BOOT -> START -> PLAY -> END (4 records) |
| 8.2 Chain integrity | All 4 records linked correctly |
| 8.3 Bridge codec | Raw export parseable by bridge codec |
| 8.4 JSON round-trip | JSON serialization/deserialization |
| 8.5 [HW] Live session | 3s real-time session with live PoAC generation |
| 8.6 Throughput | >100 records/sec benchmark |
| 8.7 Memory profiling | <10MB peak for 100 records |
| 8.8 SkillOracle E2E | Rating update from session records |

### Phase 9: Live PoAC Monitoring (6 tests) -- NEW

Real-time PoAC streaming with continuous chain validation. Generates records every 25 frames during a 5-second live session and validates the entire chain in real time.

**With controller:** Captures real input at ~125Hz and generates PoAC records.
**Without controller:** All 6 tests SKIP (or stream synthetic data in `--simulate` mode).

| Test | What It Validates |
|------|-------------------|
| 9.1 [HW] PoAC stream (5s) | >=6 records generated from live input stream |
| 9.2 [HW] Stream chain integrity | All records in the stream are hash-linked |
| 9.3 [HW] Timestamp monotonicity | All timestamps are non-decreasing |
| 9.4 [HW] Sensor commit diversity | >50% of sensor commitments are unique |
| 9.5 [HW] World model evolution | World model hash changes as frames accumulate |
| 9.6 [HW] Stream binary export | Stream serializes to 228B-aligned binary |

### Phase 10: Hardware Anti-Cheat Validation (6 tests) -- NEW

Validates the anti-cheat classifier against live controller input and verifies integration between the classifier and PoAC record generation.

**With controller:** Tests real input classification (should be NOMINAL during normal hold).
**Mixed:** Some tests are purely algorithmic (pattern sweeps, reset isolation).

| Test | What It Validates |
|------|-------------------|
| 10.1 [HW] Live nominal detection | Live input classified as NOMINAL/SKILLED (no false positives) |
| 10.2 [HW] Classifier stability (3x) | 3 consecutive 2-second windows all classify clean |
| 10.3 Aimbot in-stream detection | Synthetic aimbot pattern detected with confidence >= 180 |
| 10.4 Classifier reset isolation | MACRO detection -> reset -> NOMINAL (no state leak) |
| 10.5 [HW] PoAC+AC integration | PoAC records carry correct inference codes from classifier |
| 10.6 Multi-pattern sweep (5x) | All 5 cheat patterns correctly classified in sequence |

### Phase 11: Bounty Simulation & Session Export (6 tests) -- NEW

Full bounty lifecycle simulation including profitability evaluation, progress tracking, PoAC encoding, session export, and SkillOracle integration.

**5 algorithmic + 1 hardware test.**

| Test | What It Validates |
|------|-------------------|
| 11.1 Multi-bounty evaluation | Profitable bounties accepted, unprofitable rejected |
| 11.2 Bounty progress tracking | 10 PoAC records complete a bounty (10/10) |
| 11.3 PoAC bounty_id encoding | bounty_id correctly serialized in last 4 bytes of body |
| 11.4 Session JSON export/import | Full session round-trips through JSON encode/decode |
| 11.5 SkillOracle from bounty session | Skill rating updates correctly from bounty session records |
| 11.6 [HW] Live bounty fulfillment | Live controller input generates records that fulfill a bounty |

---

## 5. Expected Output

### With Controller Connected (72/72)

```
==============================================================================
  VAPI DualShock Edge Hardware Testing Suite v3.0
  11 Phases | 72 Tests | Full Hardware Integration
==============================================================================

  Controller:  CONNECTED (DualSense Edge via USB/BT)
  Emulator:    OK (dualshock_emulator)
  Crypto:      OK (ECDSA-P256)
  Interactive: disabled (use --interactive)

  Phase 1: Hardware Discovery & Connection
  --------------------------------------------------------------------------
    PASS  1.1 [HW] Controller detection              | Connected              | DualSense Edge detected via HID
    PASS  1.2 [HW] Connection type                    | USB or BT              | USB (avg poll: 120us)
    ...
    Phase 1: 5/5 passed

  ...

  Phase 9: Live PoAC Monitoring
  --------------------------------------------------------------------------
    PASS  9.1 [HW] PoAC stream (5s)                  | >=6 records            | 26 records from 588 frames
    PASS  9.2 [HW] Stream chain integrity             | All linked             | 26 records verified
    PASS  9.3 [HW] Timestamp monotonicity             | Non-decreasing         | span=5004ms
    PASS  9.4 [HW] Sensor commit diversity            | >50% unique            | 23/23 unique (100%)
    PASS  9.5 [HW] World model evolution              | Hash changes           | 23 distinct across 23 reports
    PASS  9.6 [HW] Stream binary export               | 26x228B                | 5928B = 26 records
    Phase 9: 6/6 passed

  Phase 10: Hardware Anti-Cheat Validation
  --------------------------------------------------------------------------
    PASS  10.1 [HW] Live nominal detection            | NOMINAL/SKILLED        | NOMINAL (220/255)
    PASS  10.2 [HW] Classifier stability (3x)         | All clean              | NOMINAL | NOMINAL | NOMINAL
    PASS  10.3 Aimbot in-stream detection              | CHEAT:AIMBOT >=180     | CHEAT:AIMBOT (180/255)
    PASS  10.4 Classifier reset isolation              | MACRO->reset->NOMINAL  | CHEAT:MACRO -> reset -> NOMINAL
    PASS  10.5 [HW] PoAC+AC integration               | Valid inferences       | 7 records, all valid codes
    PASS  10.6 Multi-pattern sweep (5x)                | All correct            | NOMINAL=OK MACRO=OK AIMBOT=OK ...
    Phase 10: 6/6 passed

  Phase 11: Bounty Simulation & Session Export
  --------------------------------------------------------------------------
    PASS  11.1 Multi-bounty evaluation                 | Accept profitable      | accepted=[2001,2002] rejected=[2003]
    PASS  11.2 Bounty progress tracking                | 10/10 complete         | 10/10 bounty_id=3001
    PASS  11.3 PoAC bounty_id encoding                 | bounty_id=4001         | record=4001 parsed=4001
    PASS  11.4 Session JSON export/import              | Round-trip valid       | 7 records re-imported
    PASS  11.5 SkillOracle from bounty session         | >1000 rating           | rating=1022 tier=Silver games=7
    PASS  11.6 [HW] Live bounty fulfillment            | >=5 samples            | 5/5 from 354 frames
    Phase 11: 6/6 passed

==============================================================================
  FINAL: 72/72 passed
  All tests passed!

  >>> READY FOR FULL HARDWARE TESTING <<<
  All 72 tests passed with real DualSense Edge hardware.
==============================================================================
```

### Without Controller (46/46 + 26 skipped)

```
==============================================================================
  FINAL: 46/46 passed  (26 skipped -- connect controller for full coverage)
  All executed tests passed! (26 hardware tests skipped)

  >>> PARTIAL -- connect DualSense Edge for full validation <<<
==============================================================================
```

### Simulation Mode (72/72)

```
==============================================================================
  FINAL: 72/72 passed
  All tests passed!

  >>> READY FOR FULL HARDWARE TESTING <<<
  All simulation tests passed. Connect controller for full validation.
==============================================================================
```

---

## 6. Testing Scenarios with Real Controller

### 6.1 Legitimate Gameplay Test

**Goal:** Verify NO false positives during normal play.

1. Connect controller, start emulator:
   ```bash
   python dualshock_emulator.py --duration 120 --export clean_session.json
   ```
2. Move the sticks gently, press buttons with natural timing, tilt the controller.
3. After 2 minutes, check the summary:
   - `Cheat Detections: 0` -- No false positives
   - `Chain Integrity: VALID`
   - All records show `NOMINAL` or `SKILLED`

### 6.2 Macro/Turbo Detection Test

**Goal:** Verify macro detection triggers on robotic button timing.

1. Start emulator:
   ```bash
   python dualshock_emulator.py --duration 30
   ```
2. Press X as fast as possible with perfectly regular timing.
3. If you achieve near-zero timing variance (s < 1ms), the classifier triggers `CHEAT:MACRO`.
4. The light bar flashes **RED** and you see:
   ```
   !!! CHEAT  #   47 | CHEAT:MACRO  (90.2%) | CHEAT_ALERT    | ...
   ```

### 6.3 IMU Mismatch Test (Cronus/XIM Detection)

**Goal:** Verify detection when stick moves but controller is stationary.

1. Place the controller flat on a desk. Do NOT hold it.
2. Start emulator.
3. Reach over and move the sticks WITHOUT picking up the controller.
4. The classifier detects the mismatch: stick velocity > 0, but gyro = 0.
   ```
   !!! CHEAT  #   23 | CHEAT:IMU_MISS (78.4%) | CHEAT_ALERT    | ...
   ```

### 6.4 No-Input Baseline Test

**Goal:** Verify idle input produces no false positives.

1. Connect controller but don't touch it:
   ```bash
   python dualshock_emulator.py --duration 15
   ```
2. All records should show `NOMINAL`. No cheat flags from idle input.

### 6.5 Live PoAC Monitoring Test (NEW)

**Goal:** Verify real-time PoAC chain generation during live play.

1. Run Phase 9 with the controller connected:
   ```bash
   python dualshock-laptop-testing-suite.py --phase 9 --verbose
   ```
2. The suite captures 5 seconds of input, generating a PoAC record every 25 frames.
3. Verify:
   - At least 6 records generated (boot + start + reports + end)
   - Chain integrity: all records hash-linked
   - Timestamps are monotonically increasing
   - Sensor commitments are diverse (different input -> different hash)
   - World model evolves as frames accumulate

### 6.6 Live Anti-Cheat Stability Test (NEW)

**Goal:** Verify the classifier produces no false positives across multiple windows.

1. Connect the controller and hold it naturally:
   ```bash
   python dualshock-laptop-testing-suite.py --phase 10 --interactive --verbose
   ```
2. Phase 10 runs 3 consecutive 2-second classification windows.
3. All 3 should report `NOMINAL`. Any cheat flag is a false positive.

### 6.7 Live Bounty Fulfillment Test (NEW)

**Goal:** Verify a bounty can be fulfilled with live controller input.

1. Run Phase 11:
   ```bash
   python dualshock-laptop-testing-suite.py --phase 11 --verbose
   ```
2. Test 11.6 captures 3 seconds of live input and generates PoAC records with bounty_id.
3. Verify the bounty reaches its required sample count (5/5).

---

## 7. PoAC Chain Verification

### 7.1 Export and Inspect

```bash
python dualshock_emulator.py --simulate --duration 30 --export chain.json
python -c "
import json
chain = json.load(open('chain.json'))
print(f'Records: {chain[\"record_count\"]}')
print(f'Device key: {chain[\"device_pubkey\"][:32]}...')
for r in chain['records'][:5]:
    print(f'  #{r[\"ctr\"]:>4d} | {r[\"inference\"]:18s} | {r[\"hash\"]}')
"
```

### 7.2 Binary Export for Bridge

```bash
python dualshock_emulator.py --simulate --duration 30 --export-binary chain.bin
python -c "
data = open('chain.bin', 'rb').read()
print(f'File size: {len(data)} bytes')
print(f'Records: {len(data) // 228}')
print(f'Valid format: {len(data) % 228 == 0}')
"
```

### 7.3 Cross-Verify with Pebble Bridge

```bash
cd ../bridge
python -c "
from vapi_bridge.codec import parse_record
data = open('../controller/chain.bin', 'rb').read()
for i in range(0, len(data), 228):
    record = parse_record(data[i:i+228])
    print(f'Counter: {record.monotonic_ctr}, Inference: 0x{record.inference_result:02x}')
"
```

---

## 8. Enhancement Contract Testing

### 8.1 Run All Contract Tests (120 tests)

```bash
cd contracts && npx hardhat test
```

### 8.2 Individual Enhancement Tests

```bash
npx hardhat test test/SkillOracle.test.js          # 24 tests
npx hardhat test test/ProgressAttestation.test.js   # 16 tests
npx hardhat test test/TeamProofAggregator.test.js   # 24 tests
```

### 8.3 How Laptop Tests Mirror Contract Logic

| Contract Feature | Solidity | Python Simulation |
|-----------------|----------|-------------------|
| SkillOracle rating delta | `(GAIN * uint32(confidence)) / 255` | `(gain * confidence) // 255` |
| SkillOracle tier brackets | `if (rating >= 2500) return Diamond` | `if rating >= 2500: return "Diamond"` |
| ProgressAttestation pair key | `keccak256(abi.encodePacked(base, curr))` | `sha256((base + curr).encode())` |
| TeamProof Merkle root | `keccak256(abi.encodePacked(left, right))` | `sha256(left + right)` |
| TeamProof leaf sort | Insertion sort on `bytes32[]` | Python `sorted()` |

The Python simulations use SHA-256 where Solidity uses keccak256 (testing algorithmic structure, not cryptographic identity). Sorting, tree construction, and rating arithmetic are identical.

---

## 9. Full Test Coverage Summary

| Suite | Tests | Command | Requires |
|-------|-------|---------|----------|
| Hardware Test Suite (v3.0) | 72 | `python dualshock-laptop-testing-suite.py` | Optional: DualSense Edge |
| Anti-Cheat Test Suite | 9 | `python anti_cheat_test_suite.py` | None |
| Contract Tests (Hardhat) | 120 | `cd contracts && npx hardhat test` | Node.js |
| Bridge Tests (pytest) | 98 | `cd bridge && python -m pytest tests/ -v` | Python |
| **Total** | **299** | | |

### Test Breakdown by Type

| Category | Algorithmic | Hardware | Total |
|----------|-------------|----------|-------|
| Phases 1-2 (HW discovery + input) | 3 | 10 | 13 |
| Phase 3 (anti-cheat synthetic) | 8 | 0 | 8 |
| Phase 4 (PoAC generation) | 7 | 1 | 8 |
| Phases 5-6 (contract sims) | 12 | 0 | 12 |
| Phase 7 (feedback + bounty) | 1 | 4 | 5 |
| Phase 8 (E2E pipeline) | 6 | 2 | 8 |
| Phase 9 (live PoAC monitoring) | 0 | 6 | 6 |
| Phase 10 (HW anti-cheat) | 3 | 3 | 6 |
| Phase 11 (bounty + export) | 5 | 1 | 6 |
| **Total** | **45** | **27** | **72** |

---

## 10. Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: pydualsense` | Library not installed | `pip install pydualsense` |
| `ModuleNotFoundError: cryptography` | Library not installed | `pip install cryptography` |
| Controller not detected | Driver issue (Windows) | Install [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases) |
| Controller not detected | Permissions (Linux) | Add udev rule (see 1.3) |
| Controller not detected | Privacy settings (macOS) | Grant Input Monitoring access |
| HW tests show SKIP | No controller connected | Connect via USB-C or Bluetooth |
| Phase 2 interactive tests don't run | Missing flag | Add `--interactive` flag |
| Phase 3 FPR test fails randomly | Random seed edge case | Re-run; should be 0% consistently |
| Phase 7 LED/haptic no response | USB cable doesn't support data | Use a USB-C data cable (not charge-only) |
| Phase 9 too few records | Short duration | 5s should yield ~24 records at 25-frame intervals |
| Phase 10 false positive on 10.1 | Controller on desk untouched | Hold controller naturally with slight movement |
| Phase 11 bounty incomplete | Capture duration too short | 3s at 125Hz = ~375 frames, record every 40 = ~9 records |
| Throughput < 100 rec/s | System load | Close other apps; any modern laptop exceeds this |
| `ImportError: dualshock_emulator` | Wrong directory | `cd controller/` before running |
| `UnicodeEncodeError` | Windows cp1252 terminal | Suite uses ASCII only; report if seen |

---

## 11. Known Limitations (Laptop vs. Firmware)

| Aspect | Laptop Emulator | ESP32-S3 Firmware |
|--------|----------------|-------------------|
| Poll rate | ~125 Hz (Python timer) | 1 kHz (FreeRTOS hardware timer) |
| TinyML | Heuristic only | TFLite Micro with ESP-NN |
| ECDSA signing | ~1 ms (x86 OpenSSL) | ~8 ms (mbedTLS software) |
| SHA-256 | ~0.01 ms (x86) | ~0.5 ms (ESP32 HW accelerator) |
| IMU sampling | Via HID (~133 Hz) | Direct SPI (1 kHz) |
| PoAC record format | **IDENTICAL** | **IDENTICAL** |
| Chain integrity | **IDENTICAL** | **IDENTICAL** |
| Anti-cheat thresholds | **IDENTICAL** | **IDENTICAL** |
| On-chain verification | **IDENTICAL** | **IDENTICAL** |

The laptop emulator is a **functionally equivalent test harness**. A PoAC chain generated by the emulator is verifiable by the same `PoACVerifier` Solidity contract.

---

## 12. Hardware Integration Architecture

```
DualSense Edge (CFI-ZCP1)
    |
    | USB-C or Bluetooth HID
    v
pydualsense library (Python)
    |
    | poll() -> InputSnapshot (50 bytes)
    v
TestClassifier / AntiCheatClassifier
    |
    | classify() -> (inference_code, confidence)
    v
PoACEngine
    |
    | generate() -> PoACRecord (228 bytes)
    | ECDSA-P256 signing (cryptography library)
    v
Chain Head (SHA-256 linked)
    |
    +-- SkillOracleSim (rating update)
    +-- ProgressAttestationSim (improvement tracking)
    +-- TeamProofSim (Merkle aggregation)
    +-- JSON/Binary Export (bridge-compatible)
```

**Data flow per frame:**
1. `controller.poll()` reads HID state -> `InputSnapshot`
2. `classifier.feed(snap)` extracts 30 features, updates sliding window
3. Every N frames: `classifier.classify()` -> inference code + confidence
4. `engine.generate()` creates 228B PoAC record with ECDSA-P256 signature
5. Record appended to hash chain; skill/progress contracts updated

---

**Document End -- VAPI DualSense Edge Hardware Testing Guide v3.0**
