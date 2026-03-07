# Bluetooth Transport — Threshold Impact Analysis

**Status:** Pre-BT-calibration. BT thresholds currently mirror USB calibrated values
(N=50 USB sessions, 2026-03-02).  Separate BT calibration required (N≥50 BT sessions).

---

## 1. Polling Rate Comparison

| Transport | Nominal Rate | Min Observed | Max Observed | Report Size |
|-----------|-------------|-------------|-------------|------------|
| USB       | 1000 Hz     | 999.8 Hz    | 1000.0 Hz   | 64 bytes   |
| Bluetooth | 125–250 Hz  | TBD (BT cal)| TBD         | 78 bytes   |

Source: USB values from N=50 hardware calibration sessions (see `calibration_profile.json`).
BT values TBD pending BT calibration sessions.

---

## 2. L4 Biometric Mahalanobis — Polling Rate Impact

**USB**: 1000 Hz × 1.0s window = ~1000 samples per interval.
**BT**: 250 Hz × 1.0s window = ~250 samples per interval → **4× fewer samples**.

### Consequence
- Mahalanobis distance is computed over a fixed 50-sample sliding window.
- At 250 Hz, the window covers 200 ms of gameplay (vs 50 ms at USB).
- Human micro-tremor patterns (8–12 Hz) complete more full cycles per window at BT rates,
  potentially increasing gyro_std variance and raising the Mahalanobis distance.
- **Risk**: legitimate BT players may trigger L4 anomaly threshold more frequently.

### Current threshold: `l4_anomaly_threshold = 5.869` (USB-calibrated, N=50)
```
bt_l4_anomaly_threshold = 5.869  # CONFIG: env BT_L4_ANOMALY_THRESHOLD
```
**Recommendation**: After N≥50 BT sessions, recalibrate:
```bash
python scripts/threshold_calibrator.py sessions/bt/*.json
```

---

## 3. L5 Temporal Rhythm Oracle — Timing Resolution Impact

**USB**: ~1 ms inter-frame intervals → CV, entropy computed at 1 ms resolution.
**BT**: ~4–8 ms inter-frame intervals → CV baseline may differ.

### Key metrics
| Metric | USB Human Baseline (N=50) | BT Expected | Notes |
|--------|--------------------------|-------------|-------|
| R2 press CV | 0.341 | TBD | 4.3× above 0.08 bot threshold |
| Entropy bits | 1.382 | TBD | Below human min = 1.0 bits threshold |

### Current thresholds
```
l5_cv_threshold = 0.08      # bot CV floor (USB-calibrated)
bt_l5_cv_threshold = 0.08   # CONFIG: env BT_L5_CV_THRESHOLD (mirrors USB)
```
**Recommendation**: BT L5 thresholds likely identical (CV reflects human behavior variance,
not frame rate). Verify with N≥20 BT sessions before lowering.

---

## 4. L6 Active Challenge-Response — BT Compatibility

**Status**: L6 trigger output is **already transport-transparent**.

`pydualsense.prepareReport()` handles BT vs USB correctly:
- BT output report: correct byte offsets + CRC32 appended automatically
- `DSTrigger.setMode()` / `DSTrigger.setForce()` — transport-agnostic API

**Consequence**: No L6 changes required for BT. Challenge-response latency may increase
at BT polling rates (output command → haptic → input readback cycle: ~8 ms BT vs ~2 ms USB).

### Impact on onset_ms detection (L6ResponseAnalyzer)
BT adds ~4–8 ms latency to onset detection. `onset_ms < 5` threshold in Attack G detection
may produce false negatives on BT. Adjust if false negatives observed after BT validation.

---

## 5. L2 Gyro/Accel Injection Detection — BT Impact

L2 uses **amplitude-based** signals:
- Signal A: `gyro_std < 20 LSB` during active frames
- Signal B: `mean_accel_mag < 100 LSB`

Both signals are amplitude-based, not rate-based. **Not affected by BT polling rate.**
BT IMU offset bug fix (using `ds.states[16:28]`) is **critical** for correct L2 scoring.

---

## 6. L0 Bluetooth Physical Presence Signals

| Signal | Weight | Source | Notes |
|--------|--------|--------|-------|
| Sequence counter integrity | 0.5 | `ds.states[0]` / raw[1] | Gaps = missing reports |
| Inter-report latency | 0.4 | `snap.inter_frame_us` | Real device: ~4 ms, CV<0.3 |
| RSSI | 0.1 | N/A | Always 0.5 on Windows/hidapi (unavailable) |

**Score interpretation**:
- `> 0.7`: high confidence physical BT device
- `0.3–0.7`: uncertain (noisy environment, USB HID passthrough, etc.)
- `< 0.3` + `is_bluetooth=True`: advisory WARNING (virtual HID suspected)

Advisory only — no effect on `humanity_probability` or PoAC inference codes.

---

## 7. Sensor Commitment — Transport Independence

The sensor commitment (32 bytes in the 228-byte PoAC record) is computed from
`InputSnapshot` canonical fields via `struct.pack`, **not** from raw HID bytes.

```python
# In make_sensor_commitment() — transport-independent:
struct.pack(">8hI", lx, ly, rx, ry, l2, r2, b0, b1, ctr)
```

Once the `DualSenseReader.poll()` BT IMU fix is applied (using `ds.states[16:28]`),
the `InputSnapshot.accel_*` and `gyro_*` fields are correct for both transports.
The commitment is therefore **identical for equivalent physical controller state**
regardless of transport — the key invariant for cross-session replay.

---

## 8. Recommended BT Calibration Procedure

1. Pair DualShock Edge via Bluetooth (disconnect USB cable first)
2. Capture ≥50 sessions:
   ```bash
   python scripts/capture_session.py --duration 60 --output sessions/bt/session_001.json
   # Repeat 50+ times
   ```
3. Recalibrate BT thresholds:
   ```bash
   python scripts/threshold_calibrator.py sessions/bt/*.json
   ```
4. Set BT-specific env vars:
   ```
   BT_L4_ANOMALY_THRESHOLD=<derived>
   BT_L5_CV_THRESHOLD=<derived>
   BT_POLLING_RATE_HZ=<measured from capture metadata>
   ```
5. Run BT hardware tests:
   ```bash
   pytest tests/hardware/test_dualshock_bluetooth.py -v -m bluetooth -s
   ```

---

## 9. Summary of Required Changes (Completed)

| File | Change | Status |
|------|--------|--------|
| `controller/hid_report_parser.py` | Transport-aware offset tables + `parse_report()` | DONE |
| `controller/dualshock_emulator.py` | Fix BT IMU bug — use `ds.states[16:28]` | DONE |
| `scripts/capture_session.py` | Transport detection + BT offset table | DONE |
| `controller/l0_bluetooth_presence.py` | BT physical presence verifier | DONE |
| `bridge/vapi_bridge/dualshock_integration.py` | Wire `BluetoothPresenceVerifier` | DONE |
| `bridge/vapi_bridge/config.py` | Add `bt_l4_anomaly_threshold`, `bt_l5_cv_threshold`, `bt_polling_rate_hz` | DONE |
| `tests/hardware/conftest.py` | `bluetooth` marker + `bt_device` fixture | DONE |
| `pytest.ini` | `bluetooth` in `addopts` exclusion + markers | DONE |
| `tests/hardware/test_dualshock_bluetooth.py` | 8 BT hardware tests | DONE |

**Invariants preserved**: 228-byte PoAC format frozen; USB path unchanged; sensor
commitment transport-independent; BT tests excluded from CI.
