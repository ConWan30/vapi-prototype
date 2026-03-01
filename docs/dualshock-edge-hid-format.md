# DualShock Edge HID Report Format

**Device:** Sony DualSense Edge (CFI-ZCP1)
**USB Vendor ID:** `0x054C` (Sony)
**USB Product ID:** `0x0DF2`
**Interface:** USB HID (not Bluetooth — VAPI requires USB for deterministic polling)
**Report rate:** 1000 Hz (USB full-speed, 1 ms interval)
**Input report ID:** `0x01`
**Input report length:** 64 bytes

---

## Full input report layout (Report ID 0x01, 64 bytes)

| Byte | Field | Type | Range | Notes |
|------|-------|------|-------|-------|
| 0 | Report ID | uint8 | `0x01` | Always `0x01` for standard input |
| 1 | Left stick X | uint8 | 0–255 | 128 = center |
| 2 | Left stick Y | uint8 | 0–255 | 128 = center; Y increases downward |
| 3 | Right stick X | uint8 | 0–255 | 128 = center |
| 4 | Right stick Y | uint8 | 0–255 | 128 = center; Y increases downward |
| 5 | L2 trigger | uint8 | 0–255 | 0 = released, 255 = fully pressed |
| 6 | R2 trigger | uint8 | 0–255 | 0 = released, 255 = fully pressed |
| 7 | Counter | uint8 | 0–255 | Wraps; increments per report |
| 8 | Button byte 0 | uint8 | bitmask | See Table 1 |
| 9 | Button byte 1 | uint8 | bitmask | See Table 2 |
| 10 | Button byte 2 | uint8 | bitmask | See Table 3 |
| 11 | Reserved | uint8 | — | |
| 12–14 | Timestamp | uint24 LE | μs | Low 24 bits of USB SoF microsecond timer |
| 15 | Battery / connection | uint8 | bitmask | Bits 0–3: battery 0–10 (×10%), bit 4: charging |
| 16–21 | Gyroscope X,Y,Z | int16 LE ×3 | ±2000 °/s | 1 LSB ≈ 0.061 °/s |
| 22–27 | Accelerometer X,Y,Z | int16 LE ×3 | ±8 g | 1 LSB ≈ 0.000244 g |
| 28–31 | Reserved | uint32 | — | |
| 32 | Touchpad finger 0 active | uint8 | 0/1 | Bit 7: active |
| 33–35 | Touchpad finger 0 XY | packed | | Bits 0–11: X (0–1919), bits 12–23: Y (0–1079) |
| 36 | Touchpad finger 1 active | uint8 | 0/1 | |
| 37–39 | Touchpad finger 1 XY | packed | | |
| 40–42 | Reserved | — | — | |
| 43 | Trigger effect byte L2 | uint8 | bitmask | Adaptive trigger effect mode (see §Adaptive Triggers) |
| 44 | Trigger effect byte R2 | uint8 | bitmask | Adaptive trigger effect mode |
| 45–63 | Reserved / vendor | — | — | |

---

## Button byte 0 (byte 8) — Table 1

| Bit | Button | Notes |
|-----|--------|-------|
| 0–3 | D-pad | 0=up, 1=up-right, 2=right, 3=down-right, 4=down, 5=down-left, 6=left, 7=up-left, 8=neutral |
| 4 | Square | |
| 5 | Cross | |
| 6 | Circle | |
| 7 | Triangle | |

## Button byte 1 (byte 9) — Table 2

| Bit | Button |
|-----|--------|
| 0 | L1 |
| 1 | R1 |
| 2 | L2 (digital) |
| 3 | R2 (digital) |
| 4 | Create / Share |
| 5 | Options / Menu |
| 6 | L3 (left stick click) |
| 7 | R3 (right stick click) |

## Button byte 2 (byte 10) — Table 3

| Bit | Button |
|-----|--------|
| 0 | PS button |
| 1 | Touchpad click |
| 2 | Mute button |
| 3 | Edge Fn (back paddle left) |
| 4 | Edge Fn (back paddle right) |
| 5–7 | Reserved |

---

## Adaptive trigger effect byte

The `trigger_effect` byte at offsets 43 (L2) and 44 (R2) encodes the current haptic
resistance profile loaded by the game or firmware:

| Value | Mode | Description |
|-------|------|-------------|
| `0x00` | `NO_RESISTANCE` | Trigger moves freely |
| `0x01` | `CONTINUOUS_RESISTANCE` | Uniform force along full travel |
| `0x02` | `SECTION_RESISTANCE` | Force applied only in a specific zone |
| `0x03` | `EFFECT_EX` | Extended effect (game-defined waveform) |
| `0x04` | `CALIBRATION` | Calibration mode |
| `0x05` | `FEEDBACK` | Force-feedback pulse |
| `0x06` | `WEAPON` | Weapon-specific resistance ramp |
| `0x07` | `BOW` | Bow-draw tension ramp |

> **Detection surface.** This byte is **not injectable via standard HID driver stack**:
> it reflects the physical actuator state read back from the motorized trigger
> mechanism, not the output command sent to the trigger. A software injection attack
> using `SendInput()` / `WriteFile()` to the HID device driver cannot produce
> non-zero trigger effect bytes because those bytes originate from the controller's
> internal ADC, not from host-side writes.
>
> VAPI commits this field into `sensor_commitment_v2` (see §Sensor Commitment below).
> Any mismatch between the committed resistance profile and subsequent reports is a
> detectable inconsistency signal.

---

## VAPI sensor commitment v2 (DualShock Edge)

The 32-byte `sensor_commitment` field in each PoAC record is:

```
SHA-256(
    left_stick_x  (int16 BE) ||
    left_stick_y  (int16 BE) ||
    right_stick_x (int16 BE) ||
    right_stick_y (int16 BE) ||
    l2_trigger    (uint8)    ||
    r2_trigger    (uint8)    ||
    l2_effect     (uint8)    ||   ← adaptive trigger effect (unforgeable)
    r2_effect     (uint8)    ||   ← adaptive trigger effect (unforgeable)
    gyro_x        (int16 BE) ||
    gyro_y        (int16 BE) ||
    gyro_z        (int16 BE) ||
    accel_x       (int16 BE) ||
    accel_y       (int16 BE) ||
    accel_z       (int16 BE) ||
    timestamp_ms  (int64 BE)
)
```

Total pre-image: 2+2+2+2+1+1+1+1+2+2+2+2+2+2+8 = **32 bytes** → SHA-256 → 32 bytes output.

This is **sensor commitment schema v2** (kinematic/haptic). The Pebble Tracker uses
schema v1 (environmental). Both schemas produce a 32-byte commitment stored in
`raw_data[32:64]` in the 228-byte PoAC wire format.

---

## L4 biometric feature vector (7 dimensions)

The `BiometricFusionClassifier` extracts these 7 features per 50-report window
(50 ms at 1 kHz) for Mahalanobis anomaly detection:

| Index | Feature | Computation | Physical meaning |
|-------|---------|------------|-----------------|
| 0 | `trigger_onset_velocity` | ΔL2/Δt at first non-zero report | How fast the player presses the trigger |
| 1 | `micro_tremor_variance` | var(gyro_magnitude, window=50) | Hand micro-tremor (only present with physical controller) |
| 2 | `grip_asymmetry` | mean(abs(left_stick_x - right_stick_x)) | Natural left/right grip force difference |
| 3 | `stick_autocorrelation` | r(left_stick_x, lag=5) | Temporal smoothness of stick input |
| 4 | `accel_magnitude_mean` | mean(sqrt(ax²+ay²+az²)) | Mean acceleration (gravity + hand motion) |
| 5 | `trigger_release_decel` | ΔL2/Δt at last non-zero report (absolute) | How fast the player releases the trigger |
| 6 | `imu_stick_correlation` | corr(gyro_magnitude, stick_magnitude) | IMU-stick coupling (absent in software injection) |

Features are scaled to `[0, 1000]` for Poseidon hashing in the PITL ZK circuit
(`FEATURE_SCALE = 1000`).

---

## Injection detection — zero IMU criterion (L2)

Software injection vectors (SendInput, XInput emulation, vJoy, DS4Windows spoofing)
cannot produce physical IMU readings. The L2 detector fires `0x28 DRIVER_INJECT` when:

```
imu_noise = std(gyro_x, gyro_y, gyro_z over 50 reports)
if imu_noise < 0.001 rad/s AND abs(stick_magnitude) > 0.15:
    fire(0x28, confidence=210)
```

The `0.001 rad/s` threshold is below any real controller's IMU noise floor
(typically 0.01–0.05 rad/s at rest). A physical controller held by a human always
exceeds this floor due to hand micro-tremors and breathing.

---

## Reading HID reports (Python, pydualsense / hidapi)

```python
import hid

VID = 0x054C
PID = 0x0DF2

dev = hid.device()
dev.open(VID, PID)
dev.set_nonblocking(False)

while True:
    report = dev.read(64)
    if not report or report[0] != 0x01:
        continue

    left_x   = report[1]
    left_y   = report[2]
    right_x  = report[3]
    right_y  = report[4]
    l2       = report[5]
    r2       = report[6]
    gyro_x   = int.from_bytes(report[16:18], 'little', signed=True)
    gyro_y   = int.from_bytes(report[18:20], 'little', signed=True)
    gyro_z   = int.from_bytes(report[20:22], 'little', signed=True)
    accel_x  = int.from_bytes(report[22:24], 'little', signed=True)
    accel_y  = int.from_bytes(report[24:26], 'little', signed=True)
    accel_z  = int.from_bytes(report[26:28], 'little', signed=True)
    l2_eff   = report[43]   # adaptive trigger effect mode
    r2_eff   = report[44]
    batt     = report[15] & 0x0F  # 0–10 (×10%)
    charging = bool(report[15] & 0x10)
```

---

## Known quirks and edge cases

| Quirk | Details |
|-------|---------|
| Stick deadzone | Hardware deadzone of ±8 LSB around center (128 ± 8). Values in [120, 136] may be noise, not input. |
| Gyro scaling | Raw value × (2000/32768) = degrees/second. At rest, expect ±5 LSB drift. |
| Accel at rest | Z-axis reads ~4000 LSB (≈1g) when flat. Total magnitude: sqrt(ax²+ay²+az²) ≈ 8192 at 1g. |
| USB polling jitter | Windows USBHID driver adds ±1 ms jitter even at 1 kHz. Use report counter (byte 7) to detect gaps. |
| Trigger digital bit | Byte 9 bits 2–3 are digital L2/R2 (threshold ~50 of 255). Analog values in bytes 5–6 are always available regardless. |
| Edge paddles | Edge function buttons (byte 10 bits 3–4) are the back paddles. They only appear when the Edge function layer is active. |
| Bluetooth mode | In Bluetooth mode, report ID is `0x31` with a different layout. VAPI explicitly requires USB (`0x01`) for deterministic 1 kHz polling. |
| Windows HID filter | Windows may require installing `ViGEm Bus` or `DS4Windows` driver package if the controller is detected as a game controller rather than a raw HID device. VAPI uses raw HID via `hid` library (hidapi). |

---

## Troubleshooting connection issues

```
Error: Unable to open HID device 054C:0DF2
```

**Linux:** Add udev rule (run `scripts/hardware_setup.sh` which installs this automatically):
```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="0df2", MODE="0666"
```
Then: `sudo udevadm control --reload-rules && sudo udevadm trigger`

**Windows:** If the device appears as a game controller in Device Manager (not as a HID
device), install [ViGEmBus](https://github.com/ViGEm/ViGEmBus) and use the hidapi
`\\.\\hid#vid_054c&pid_0df2` path override.

**macOS:** No extra configuration needed. `hid.open(VID, PID)` should work directly.
IOKit grants HID access to user-space processes by default for non-keyboard/mouse
devices.
