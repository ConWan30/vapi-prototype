# VAPI Hardware Testing Guide — DualShock Edge CFI-ZCP1

## Overview

This guide covers everything needed to run real-hardware PITL validation against
a physical DualShock Edge (Sony CFI-ZCP1) connected via USB. Hardware testing is
the #1 priority gap before any production deployment claim can be made.

**Why hardware testing matters**: All current PITL detection figures (100% detection,
0% false positives) are derived from synthetic test data. These figures are
meaningless without real-hardware validation. This guide provides the path from
"codebase complete" to "empirically validated."

---

## Prerequisites

| Item | Details |
|------|---------|
| Controller | DualShock Edge (Sony CFI-ZCP1) |
| Cable | USB-C data cable — NOT a charge-only cable |
| OS | Linux (Ubuntu 22.04+), macOS, or Windows 10+ |
| Python | 3.10+ |
| Packages | `pip install hidapi pydualsense pytest` |

**Verify your USB cable**: Charge-only cables have only 2 wires (power + ground).
Data cables have 4 wires. If `lsusb` (Linux) or Device Manager (Windows) doesn't
show the controller, try a different cable.

---

## Step-by-Step Setup

### Linux

```bash
# 1. Run the setup script (installs udev rules, Python packages, runs connection test)
bash scripts/hardware_setup.sh

# 2. Reconnect the controller after udev rules are installed
# (or: sudo udevadm trigger)

# 3. Verify connection
python3 -c "import hid; print(hid.enumerate(0x054C, 0x0DF2))"
# Expected: [{'vendor_id': 1356, 'product_id': 3570, 'product_string': 'DualSense Edge', ...}]
```

### Windows

1. Download [Zadig](https://zadig.akeo.ie/)
2. Plug in DualShock Edge via USB
3. In Zadig: Options → List All Devices → select "DualSense Edge" → install WinUSB
4. `pip install hidapi pydualsense`
5. Verify: `python -c "import hid; print(hid.enumerate(0x054C, 0x0DF2))"`

### macOS

```bash
pip install hidapi pydualsense
python3 -c "import hid; print(hid.enumerate(0x054C, 0x0DF2))"
# macOS grants HID access automatically
```

---

## Running Tests

```bash
# All hardware tests (requires connected controller)
pytest tests/hardware/ -v -m hardware

# Individual test files
pytest tests/hardware/test_dualshock_live.py -v -m hardware
pytest tests/hardware/test_pitl_live.py -v -m hardware

# CI-safe (no hardware required)
pytest -m "not hardware"
```

---

## First Session Protocol

The first hardware test session establishes the biometric baseline used for
threshold calibration. Run it before any other hardware testing:

```bash
python scripts/first_session_protocol.py
```

This walks you through:
1. **Free play** (30s) — move sticks, press triggers naturally
2. **Structured motions** (30s) — full stick rotations, trigger ramps, IMU tilts
3. **Stationary** (10s) — controller flat on desk, not touched
4. **Chain generation** — 50 synthetic PoAC-like records
5. **Chain verification** — hash linkage integrity check
6. Saves everything to `sessions/first_session/`

---

## Capturing Calibration Sessions

After the first session protocol, capture additional sessions for threshold calibration.
**Minimum N=10 sessions; N=50 recommended for production thresholds.**

```bash
# Capture a 60-second session
python scripts/capture_session.py --duration 60 --notes "free play gold tier"

# Capture a longer session
python scripts/capture_session.py --duration 300 --notes "competitive match"

# Sessions are saved to sessions/ with timestamped filenames
ls sessions/
```

---

## Threshold Calibration

After collecting sessions, run the calibration tool:

```bash
# Calibrate from all captured sessions
python scripts/threshold_calibrator.py sessions/*.json

# Output: calibration_profile.json with recommended thresholds
cat calibration_profile.json
```

Compare the recommended values to the current magic numbers:

| Threshold | Current Magic Number | File | Location |
|-----------|---------------------|------|----------|
| L4 anomaly | 3.0 | `controller/tinyml_biometric_fusion.py` | `ANOMALY_THRESHOLD` |
| L4 continuity | 2.0 | `controller/tinyml_biometric_fusion.py` | `CONTINUITY_THRESHOLD` |
| L5 CV | 0.08 | `bridge/vapi_bridge/dualshock_integration.py` | `_CV_THRESHOLD` |
| L5 entropy | 1.5 | `bridge/vapi_bridge/dualshock_integration.py` | `_ENTROPY_THRESHOLD` |

---

## Interpreting Results

### "PASS" vs "Needs Calibration"

| Test | PASS | Needs Calibration | FAIL |
|------|------|------------------|------|
| Stick axis range | All bytes ∈ [0, 255] | — | Out-of-range bytes |
| IMU noise floor | gyro std < 50 LSB | 50–100 LSB | > 100 LSB |
| Fingerprint consistency | L2 < 50 | 30–50 (update threshold) | > 50 (controller drift?) |
| Timing CV (human) | > 0.15 | 0.08–0.15 (diamond tier risk) | < 0.08 (would trigger L5) |

### False Positive Risk

If any human-play session triggers L4/L5:
1. Document the session metadata (skill tier, fatigue, unusual conditions)
2. Check if the threshold needs adjustment using `threshold_calibrator.py`
3. Do NOT simply raise the threshold without understanding why it triggered

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Controller not detected | Charge-only cable | Try different data cable |
| Permission denied (Linux) | Missing udev rules | `bash scripts/hardware_setup.sh` |
| IMU reads all zeros | Firmware offset mismatch | Check `_parse_imu()` byte offsets |
| `test_biometric_fingerprint_consistency` fails | Controller state changed between windows | Re-run with controller in stable state |
| Stick drift detected | Hardware defect | Test with replacement controller |
| CV < 0.08 for human player | Diamond-tier timing or USB polling | Check USB polling rate (see hardware_setup.sh) |

---

## Known Limitations

- **No production ZK proofs**: PITLSessionRegistry is in mock mode during hardware testing.
  Real ZK proofs require `npx hardhat run scripts/run-ceremony.js` for trusted setup.
- **IMU byte offsets**: Bytes 16–27 (gyro/accel) from community reverse-engineering.
  Validate against your specific firmware version (`hexdump -C` a raw report).
- **Adaptive trigger write**: Full trigger effect write/readback requires pydualsense
  output report protocol (USB 0x31). Currently tested read-only only.
- **All thresholds are pre-calibration**: Numeric values in hardware tests are
  conservative estimates. Run `threshold_calibrator.py` after N≥10 sessions to get
  empirical values.

---

## Session Storage Structure

```
sessions/
  first_session/
    session.json          ← first_session_protocol.py output
  session_20240101T120000Z.json   ← capture_session.py outputs
  session_20240101T130000Z.json
calibration_profile.json          ← threshold_calibrator.py output
```
