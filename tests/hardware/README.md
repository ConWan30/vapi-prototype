# Hardware Tests — DualShock Edge CFI-ZCP1

Hardware tests validate the VAPI PITL transport layer and biometric pipeline against
a physical DualShock Edge (Sony CFI-ZCP1) connected via USB. All tests are gated
behind the `@pytest.mark.hardware` marker and skipped in CI.

---

## Prerequisites

| Component | Requirement |
|-----------|-------------|
| Controller | DualShock Edge Sony CFI-ZCP1 (model CFI-ZCP1) |
| Cable | USB-C **data** cable (NOT charge-only — use the cable from the controller box) |
| LED indicator | White PS button LED = USB mode ✓ · Blue = Bluetooth ✗ |
| Python packages | `pip install hidapi pydualsense` |
| Linux | udev rules: run `scripts/hardware_setup.sh` |
| Windows | WinUSB/LibUSB-Win32 via [Zadig](https://zadig.akeo.ie/) tool |
| macOS | No additional setup needed |
| Battery | > 20% (haptic/trigger motor tests need power) |

---

## Quick Start

```bash
# All hardware tests (requires connected controller)
pytest tests/hardware/ -v -m hardware -s

# Individual test files
pytest tests/hardware/test_dualshock_live.py            -v -m hardware -s
pytest tests/hardware/test_pitl_live.py                 -v -m hardware -s
pytest tests/hardware/test_dualshock_biometric.py       -v -m hardware -s
pytest tests/hardware/test_dualshock_report_timing.py   -v -m hardware -s
pytest tests/hardware/test_dualshock_adaptive_triggers.py -v -m hardware -s

# Skip hardware tests (default in CI)
pytest -m "not hardware"
```

> **-s flag**: Required to see step-by-step prompts and calibration data printed during tests.

---

## Complete Test Suite Reference

### File 1: `test_dualshock_live.py` — USB HID baseline validation

| Test | What it checks | Action required | Duration | Pass criteria |
|------|---------------|-----------------|----------|---------------|
| `test_hid_connection` | VID=0x054C, PID=0x0DF2 present | None | <5 s | Device found |
| `test_raw_report_format` | report_id ∈ {0x01}, length ≥ 64 | None | <5 s | All 10 reports valid |
| `test_adaptive_trigger_readback` | L2/R2 bytes in [0, 255] | None | <5 s | No out-of-range values |
| `test_stick_axes_range` | 4 stick axes in [0, 255] | None | <5 s | 100 reports clean |
| `test_imu_noise_floor` | gyro std < 50 LSB, accel std < 200 LSB | Place controller on flat surface | ~10 s | Within noise floor |
| `test_sensor_commitment_consistency` | SHA-256 determinism | None | <5 s | Same input → same output |

---

### File 2: `test_pitl_live.py` — PITL transport smoke tests

| Test | What it checks | Action required | Duration | Pass criteria |
|------|---------------|-----------------|----------|---------------|
| `test_nominal_human_play` | HID report volume during active play | Interact with controller for 5 s | ~10 s | ≥200/250 reports; ≥1 non-zero |
| `test_stationary_controller` | Stick/trigger noise when untouched | Leave controller on flat surface for 5 s | ~10 s | stick std < 5 LSB; trigger < 10 |
| `test_poac_chain_generation` | Hash chain construction | None | <5 s | 10 records; chain valid; unique hashes |
| `test_feature_extraction_live` | 6-feature extraction in [0, 255] | Interact with controller | ~10 s | No NaN/Inf; values in range |
| `test_biometric_fingerprint_consistency` | Same-device session L2 distance | Hold controller consistently | ~30 s | L2 distance < 50 |

---

### File 3: `test_dualshock_biometric.py` — L4 Biometric Fusion (live)

Tests the BiometricFusionClassifier two-track EMA with real HID data.

| Test | What it checks | Action required | Duration | Pass criteria |
|------|---------------|-----------------|----------|---------------|
| `test_1_biometric_feature_extraction_live` | 7 PITL features from live data | Move sticks and press triggers | ~30 s | All features finite; print for calibration |
| `test_2_stable_track_initialisation` | Stable track init after N_WARMUP sessions | Play normally for 5×5s sessions | ~90 s | `_stable_initialized` = True; drift < 0.5 |
| `test_3_drift_velocity_after_play` | Drift velocity increases on style change | Phase A: gentle hold; Phase B: active play | ~60 s | drift_after > drift_baseline |
| `test_4_candidate_track_diverges_on_bot_input` | Stable track quarantine (live) | Phase A: play; Phase B: put down controller | ~30 s | `_stable_mean` unchanged; quarantine holds |
| `test_5_imu_stick_coupling_nonzero` | IMU–stick coupling (injection detection surface) | Hold controller; move sticks actively | ~10 s | IMU noise std > 0 |
| `test_6_trigger_onset_velocity_characterisation` | L2/R2 onset velocity measurement | Press triggers quickly on cue | ~30 s | Onset detected; print for calibration |
| `test_7_micro_tremor_accel_variance_present` | Accelerometer micro-tremor variance | Hold controller naturally (no deliberate movement) | ~10 s | accel_variance > 0; gyro_std > 0 |

---

### File 4: `test_dualshock_report_timing.py` — 1 kHz polling and counter validation

All tests are fully passive (no controller interaction needed — just leave connected via USB).

| Test | What it checks | Action required | Duration | Pass criteria |
|------|---------------|-----------------|----------|---------------|
| `test_1_polling_rate_1khz` | Effective polling rate 850–1150 Hz | None | ~5 s | 1000 reports in ~1 s |
| `test_2_report_counter_monotonic` | Byte-7 counter increments by 1 per report | None | ~2 s | Zero violations |
| `test_3_gap_detection` | Inter-report intervals (expect ≈ 1 ms) | None | ~2 s | Median < 10 ms |
| `test_4_timestamp_field_advances` | Internal timestamp at bytes 12–14 | None | ~2 s | > 80% advancing deltas |
| `test_5_report_counter_wrap` | Counter wraps 255 → 0 correctly | None | ~1 s | Wrap validated if observed |

---

### File 5: `test_dualshock_adaptive_triggers.py` — Motorised trigger characterisation

| Test | What it checks | Action required | Duration | Pass criteria |
|------|---------------|-----------------|----------|---------------|
| `test_1_trigger_full_range` | L2/R2 ADC range [0, 255] | Press L2 fully, release; repeat R2 | ~30 s | min < 10, max > 200 |
| `test_2_trigger_effect_byte_readback` | Effect bytes at offsets 43/44 in [0, 7] | None | ~15 s | Valid mode codes; print for docs |
| `test_3_trigger_release_return` | Trigger returns to near-zero on release | Press fully, hold, then fully release | ~30 s | Final max < 30 |
| `test_4_trigger_differential` | L2 and R2 channels are independent | Press L2 only; then R2 only | ~30 s | No cross-talk |
| `test_5_sensor_commitment_v2_preimage` | SHA-256 pre-image format from live data | None | ~10 s | Deterministic; correct length |

---

## Step-by-Step Full Test Session

The complete recommended sequence for first-time hardware validation.
**Total time: ~40 minutes.**

### Phase 0 — Setup (5 min)

1. Connect DualShock Edge via USB-C data cable.
2. Press the PS button — LED should be **white** (USB mode).
   If blue: go to Settings > Accessories and switch to USB.
3. Install dependencies: `pip install hidapi pydualsense`
4. On Windows: run Zadig, select the DualSense USB HID interface, install WinUSB driver.
5. On Linux: run `bash scripts/hardware_setup.sh` (installs udev rules).
6. Verify device enumeration: `python -c "import hid; print(hid.enumerate(0x054C, 0x0DF2))"`
   Should print a non-empty list with VID=0x054C, PID=0x0DF2.

### Phase 1 — Baseline HID validation (5 min)

```bash
pytest tests/hardware/test_dualshock_live.py -v -m hardware -s
```

Place the controller on a flat table. No interaction needed except for `test_imu_noise_floor`
(keep the controller on the table for that test — printed prompts will guide you).

**Save the output** — IMU noise values from this phase set the thresholds for L4 calibration.

### Phase 2 — Polling rate and timing (2 min)

```bash
pytest tests/hardware/test_dualshock_report_timing.py -v -m hardware -s
```

No action needed. Leave controller connected and still.

> **Windows note**: If test_1_polling_rate_1khz shows < 500 Hz, your OS is throttling USB
> polling. Run `devmgmt.msc` → Human Interface Devices → DualSense → Power Management →
> uncheck "Allow the computer to turn off this device to save power". Then re-run.

### Phase 3 — Trigger characterisation (8 min)

```bash
pytest tests/hardware/test_dualshock_adaptive_triggers.py -v -m hardware -s
```

Follow the printed prompts for each test. Key actions:
- **Test 1**: Press L2 fully to mechanical stop. Hold 2 s. Release fully. Repeat R2.
- **Test 3**: Press fully, hold 2 s, release completely and wait 2 s for settle.
- **Test 4**: Phase A = L2 only (R2 free). Phase B = R2 only (L2 free).

**Save the calibration data** printed for trigger range — use with `scripts/threshold_calibrator.py`.

### Phase 4 — PITL transport smoke tests (5 min)

```bash
pytest tests/hardware/test_pitl_live.py -v -m hardware -s
```

Follow printed prompts. Key actions:
- `test_nominal_human_play`: Move sticks, press buttons for 5 seconds when prompted.
- `test_stationary_controller`: Put controller on flat surface and don't touch it.
- `test_biometric_fingerprint_consistency`: Hold controller in the same grip for both 30-report windows.

### Phase 5 — Biometric fusion live tests (15 min)

```bash
pytest tests/hardware/test_dualshock_biometric.py -v -m hardware -s
```

Most time-intensive phase. Key actions:
- **Test 2** (Stable track init): You will be prompted 5 times. Each session: hold/play controller normally for ~3 seconds.
- **Test 3** (Drift velocity): Phase A = keep controller still (on table, connected). Phase B = active play with sticks and triggers.
- **Test 4** (Quarantine): Phase A = play normally for 5 sessions. Phase B = put controller on table and don't touch it.
- **Test 6** (Trigger onset): Fully release triggers, then when prompted, press L2 quickly in one smooth fast motion.
- **Test 7** (Micro-tremor): Hold controller in gaming grip; just breathe normally — no deliberate movement.

### Phase 6 — Record calibration values

After all 5 phases pass, collect the printed calibration values and update `scripts/threshold_calibrator.py`:

```python
# From test_7_micro_tremor_accel_variance_present:
ACCEL_VARIANCE_AT_REST = <printed value>
GYRO_STD_AT_REST = <printed value>

# From test_1_biometric_feature_extraction_live:
L4_FEATURE_BASELINE = {
    "trigger_resistance_change_rate": <printed>,
    "trigger_onset_velocity_l2":      <printed>,
    "micro_tremor_accel_variance":    <printed>,
    "grip_asymmetry":                 <printed>,
    "stick_autocorr_lag1":            <printed>,
    "stick_autocorr_lag5":            <printed>,
}

# From test_1_trigger_full_range:
TRIGGER_MAX_ADC = <printed l2_max value>
TRIGGER_REST_ADC = <printed l2_min value>
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `No DualShock Edge detected` | Wrong cable or Bluetooth mode | Use data-capable USB-C cable; PS button LED must be **white** |
| `Permission denied` (Linux) | Missing udev rules | Run `scripts/hardware_setup.sh` or `sudo chmod a+rw /dev/hidraw*` |
| `Cannot open HID device` (Windows) | Missing WinUSB driver | Zadig → select DualSense interface → install WinUSB |
| Polling rate << 1000 Hz (Windows) | OS USB power management | Device Manager → HID → Power Management → uncheck "allow turn off" |
| `test_2_report_counter_monotonic` fails | USB packet loss | Try different USB port or cable; avoid USB hubs |
| IMU all-zero or test skipped | Wrong byte offsets for firmware | Run `python scripts/hid_hexdump.py` to inspect raw bytes |
| Trigger max < 200 | Not pressed fully to mechanical stop | Press harder to the physical stop (motorised resistance ≠ max depth) |
| Biometric test drift unexpectedly high | Controller moved between sessions | Keep controller in exact same position/grip between A/B windows |
| `hidapi not installed` | Missing Python package | `pip install hidapi` (not `hid` on PyPI — use `hidapi`) |

---

## Known Limitations

- Adaptive trigger effect **write** tests (pydualsense output report) not yet implemented
  — tests only read back the current effect mode, not set it.
- IMU byte offsets (16–27 for gyro/accel) validated on CFI-ZCP1 firmware 4.xx.
  Earlier firmware versions may use different offsets.
- All numeric thresholds are pre-hardware-calibration estimates. Run `scripts/threshold_calibrator.py`
  after completing Phase 1–5 to derive empirical values for your specific unit.
- Full PITL L4/L5 classification (classify() returning inference codes) requires
  the complete bridge pipeline (`main.py`) running — not tested in isolation here.
- `test_1_polling_rate_1khz` may fail on Windows without USB polling rate tuning.
  This is a Windows USBHID scheduler limitation, not a hardware defect.
