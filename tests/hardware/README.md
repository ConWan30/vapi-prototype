# Hardware Tests — DualShock Edge CFI-ZCP1

Hardware tests validate the VAPI PITL transport layer against a physical controller.
They are gated behind the `@pytest.mark.hardware` marker and skipped in CI.

## Prerequisites

| Component | Requirement |
|-----------|-------------|
| Controller | DualShock Edge Sony CFI-ZCP1 |
| Cable | USB-C data cable (NOT charge-only) |
| Python package | `pip install hidapi pydualsense` |
| Linux | udev rules: run `scripts/hardware_setup.sh` |
| Windows | WinUSB/LibUSB-Win32 via Zadig tool |
| macOS | No additional setup needed |

## Running Tests

```bash
# All hardware tests (requires connected controller)
pytest tests/hardware/ -v -m hardware

# Specific test files
pytest tests/hardware/test_dualshock_live.py -v -m hardware
pytest tests/hardware/test_pitl_live.py -v -m hardware

# Skip hardware tests (default in CI)
pytest -m "not hardware"
```

## Test Files

### `test_dualshock_live.py` — USB HID validation
| Test | What it checks | Pass criteria |
|------|----------------|---------------|
| `test_hid_connection` | Controller enumerated with correct VID/PID | VID=0x054C, PID=0x0DF2 found |
| `test_raw_report_format` | HID report structure | report_id ∈ {0x01, 0x31}, length ≥ 64 |
| `test_adaptive_trigger_readback` | Trigger byte range | L2/R2 bytes ∈ [0, 255] |
| `test_stick_axes_range` | Stick axis byte range | All 4 axes ∈ [0, 255] |
| `test_imu_noise_floor` | Gyro/accel noise when stationary | gyro std < 50 LSB, accel std < 200 LSB |
| `test_sensor_commitment_consistency` | SHA-256 determinism | Same input → same commitment |

### `test_pitl_live.py` — PITL transport smoke tests
| Test | What it checks | Pass criteria |
|------|----------------|---------------|
| `test_nominal_human_play` | HID report volume during active play | ≥200/250 reports; ≥1 non-zero |
| `test_stationary_controller` | Stick/trigger noise when untouched | stick std < 5 LSB; trigger < 10 |
| `test_poac_chain_generation` | Hash chain construction | 10 records; chain integrity; unique hashes |
| `test_feature_extraction_live` | 6-feature extraction validity | All features ∈ [0, 255]; no NaN/Inf |
| `test_biometric_fingerprint_consistency` | Same-device session similarity | L2 distance < 50 between sessions |

## Expected Behavior

### test_nominal_human_play
- Prompt: "Please interact with the controller for 5 seconds"
- **PASS**: ≥200 reports received, at least 1 with non-zero stick bytes
- **FAIL / investigate**: Fewer reports → check USB cable data capability; check `dmesg`
- **FAIL / investigate**: All-zero payload → charge-only cable, controller asleep, driver issue

### test_stationary_controller
- Prompt: "Leave controller completely untouched for 5 seconds"
- Controller must be on a flat, stable surface with no one touching it
- **PASS**: Stick axis std < 5 LSB, trigger values < 10
- **NEEDS CALIBRATION**: If stick std is 5–15 LSB → update `_STICK_STATIONARY_STD` after running `threshold_calibrator.py`
- **FAIL**: If std > 20 LSB → controller may have stick drift defect; try replacement cable

### test_imu_noise_floor
- **PASS**: gyro std < 50 LSB (= ~1.5 deg/s at typical DualSense sensitivity)
- **INFORMATIONAL**: IMU values are printed for calibration documentation
- **NOTE**: Byte offsets 16–27 (gyro/accel) are from community reverse-engineering; may vary by firmware

### test_biometric_fingerprint_consistency
- **PASS**: Two 30-report windows from the same device in the same state → L2 < 50
- **PRE-CALIBRATION**: L2 < 50 is conservative; run `threshold_calibrator.py` for empirical value
- **FAIL**: If controller state changed significantly between the two windows (stick moved, trigger pressed)

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `No DualShock Edge detected` | Wrong cable or Bluetooth mode | Use data-capable USB-C cable; ensure PS button is lit white (USB mode) |
| `Permission denied` (Linux) | Missing udev rules | Run `scripts/hardware_setup.sh` or `sudo chmod a+rw /dev/hidraw*` |
| `Cannot open HID device` (Windows) | Missing WinUSB driver | Install LibUSB-Win32 via Zadig; select WinUSB driver for DualSense interface |
| Report length < 64 | Bluetooth mode | USB only — Bluetooth mode uses different report IDs and format |
| IMU test skipped | Wrong byte offsets | Offsets 16–21 (gyro) may differ; check with `hexdump` of raw report |
| Fingerprint distance > 50 | Controller state changed | Re-run with controller in stable state across both 30-report windows |

## Known Limitations

- Adaptive trigger effect write/readback (pydualsense output report) not yet implemented
- IMU byte offsets validated on CFI-ZCP1 fw4.xx; earlier firmware may differ
- All numeric thresholds are pre-hardware-calibration estimates
- Real PITL L4/L5 classification requires full bridge pipeline (`main.py`)
