# VAPI DualShock — Firmware Porting Guide

**From:** Pebble Tracker (nRF9160 / Zephyr RTOS / CryptoCell-310)
**To:** DualShock Controller Mod (ESP32-S3 / ESP-IDF / FreeRTOS / mbedTLS)

---

## 1. Porting Overview

The VAPI DualShock firmware is **not a rewrite** — it is a direct port of the Pebble Tracker
VAPI agent stack onto gaming-optimized hardware. Every core module maps 1:1:

| Pebble Module | DualShock Port | Changes Required |
|---------------|---------------|------------------|
| `poac.h/c` | `poac.h/c` (unchanged API) | Replace PSA Crypto → mbedTLS; NVS Zephyr → NVS ESP-IDF |
| `agent.h/c` | `dualshock_agent.h/c` | Gaming state machine, 1 kHz input poll, anti-cheat pipeline |
| `tinyml.h/c` | `tinyml_anticheat.h/c` | Gaming-specific: 8-class cheat detection, button/stick features |
| `economic.h/c` | `economic.h/c` (unchanged API) | Energy profile constants updated for ESP32-S3 + BLE |
| `perception.h/c` | `ds_input.h/c` | Replace env sensors → DualShock inputs (buttons, sticks, IMU, touch) |

### 1.1 What Does NOT Change

These are protocol-level invariants shared with the Pebble ecosystem:

- **PoAC record format**: 228 bytes (164-byte body + 64-byte ECDSA-P256 signature)
- **Hash algorithm**: SHA-256 for all commitments
- **Signing algorithm**: ECDSA over secp256r1 (P-256)
- **Chain integrity**: `prev_poac_hash`, monotonic counter, timestamp
- **On-chain contracts**: Same `PoACVerifier`, `DeviceRegistry`, `BountyMarket`
- **Bridge protocol**: Same MQTT/CoAP/HTTP relay (companion app acts as local bridge)
- **Economic model**: Same utility function, same knapsack optimizer

### 1.2 What Changes

Gaming-specific adaptations:

| Aspect | Pebble | DualShock |
|--------|--------|-----------|
| Sensor suite | BME680, ICM-42605, TSL2572, GPS | Buttons (18), sticks (4-axis), triggers (2), IMU (6-axis), touchpad (2-point), battery |
| Sensor commitment | ~96 B env data → SHA-256 | 50 B gaming snapshot → SHA-256 |
| TinyML input | 100 samples × 3 axes (accel) | 100 frames × 30 features (inputs+IMU) |
| TinyML classes | 5: stationary/walking/vehicle/fall/anomaly | 8: nominal/skilled/6 cheat types |
| L1 period | 30 s (environmental) | 1 ms input poll, PoAC at 2–10 Hz |
| L2 period | 5 min | 5 s (anti-cheat trend analysis) |
| L3 period | 1 hr (cellular) | 60 s (BLE to companion app) |
| Transport | NB-IoT cellular | BLE 5.0 (to companion app, which relays to bridge) |
| World model | Environmental baselines (temp, VOC) | Player skill profile (reaction, precision, consistency) |
| Crypto backend | CryptoCell-310 (PSA Crypto API) | mbedTLS software (ESP32-S3 has SHA HW accel) |
| RTOS | Zephyr | FreeRTOS (ESP-IDF native) |
| Flash persistence | Zephyr NVS | ESP-IDF NVS |

---

## 2. Hardware Platform: ESP32-S3

### 2.1 Why ESP32-S3

| Requirement | ESP32-S3 Capability |
|-------------|---------------------|
| Dual-core for parallel input poll + agent | Xtensa LX7 dual-core @ 240 MHz |
| TinyML inference <5 ms | Yes — ESP-NN optimized kernels |
| ECDSA-P256 signing <15 ms | ~8 ms via mbedTLS with hardware SHA |
| BLE 5.0 for companion app | On-chip, NimBLE stack |
| SPI master for stock MCU bus | 4× SPI controllers |
| Fits inside controller shell | WROOM-1 module: 18×25.5×3.1 mm |
| Sufficient RAM | 512 KB SRAM + 8 MB PSRAM |
| Sufficient flash | 16 MB QSPI |
| Low cost | ~$3 in volume |

### 2.2 Pin Mapping

```
ESP32-S3 Pin    Function             DualShock Connection
──────────────  ───────────────────  ─────────────────────
GPIO10          SPI2_MISO            Stock MCU data out (input state)
GPIO11          SPI2_MOSI            Stock MCU data in (haptic commands)
GPIO12          SPI2_SCLK            Stock MCU SPI clock
GPIO13          SPI2_CS              Stock MCU chip select
GPIO14          IRQ_INPUT            Stock MCU interrupt (new data ready)
GPIO15          LED_DATA             Light bar control (WS2812 protocol)
GPIO16          HAPTIC_L             Left motor PWM
GPIO17          HAPTIC_R             Right motor PWM
GPIO18          BATTERY_ADC          Battery voltage divider (ADC1_CH7)
GPIO0           BOOT_BUTTON          VAPI mode toggle (repurpose share btn)
Built-in        BLE antenna          On-chip, routed to module antenna
```

### 2.3 Memory Budget

```
Component                    SRAM        PSRAM       Flash
─────────────────────────    ────────    ────────    ────────
FreeRTOS + ESP-IDF HAL       60 KB       —           200 KB
NimBLE stack                 48 KB       —           180 KB
VAPI Agent (3 threads)       24 KB       —           80 KB
  L1 stack (8 KB)
  L2 stack (6 KB)
  L3 stack (6 KB)
  Shared state (4 KB)
PoAC Engine                  8 KB        —           40 KB
  Record workspace
  NVS cache (counter+head)
TinyML Anti-Cheat            22 KB       —           55 KB
  Model weights (in flash)
  Inference arena (22 KB)
Input Ring Buffer            50 KB       —           —
  256 frames × ~200 B each
PoAC Record Queue            8 KB        —           —
  32 records × 228 B
Gaming World Model           12 KB       —           —
  64 entries × ~160 B
Crypto Workspace             4 KB        —           —
  mbedTLS ECDSA context
Economic Evaluator           4 KB        —           4 KB
BLE TX/RX Buffers            16 KB       —           —
─────────────────────────    ────────    ────────    ────────
TOTAL                        ~256 KB     0           ~559 KB
Available                    512 KB      8 MB        16 MB
Headroom                     ~256 KB     8 MB        ~15.4 MB
```

The ESP32-S3 provides comfortable headroom. PSRAM is available for debug logging, replay
storage, and future model expansion but is not required for core operation.

---

## 3. Module-by-Module Porting Guide

### 3.1 PoAC Engine (`poac.h/c`)

**API unchanged.** The header file is identical. Implementation changes:

| Pebble (Zephyr/nRF) | DualShock (ESP-IDF) | Notes |
|---------------------|---------------------|-------|
| `psa_crypto_init()` | `mbedtls_entropy_init()` + `mbedtls_ctr_drbg_seed()` | One-time at boot |
| `psa_generate_key()` | `mbedtls_ecdsa_genkey()` with `MBEDTLS_ECP_DP_SECP256R1` | Persistent in NVS |
| `psa_sign_hash()` | `mbedtls_ecdsa_sign()` → convert DER to raw r‖s | ~8 ms on ESP32-S3 |
| `psa_verify_hash()` | `mbedtls_ecdsa_verify()` | Self-test only |
| `psa_export_public_key()` | `mbedtls_ecp_point_write_binary(UNCOMPRESSED)` | 65 bytes SEC1 |
| `psa_hash_compute(SHA256)` | `esp_sha256()` (hardware accelerated) | <1 ms for 164 B |
| Zephyr NVS `nvs_write/read` | ESP-IDF NVS `nvs_set_blob/get_blob` | Same semantics |
| Zephyr `k_mutex` | FreeRTOS `xSemaphoreCreateMutex` | Same pattern |

**Key storage**: ESP-IDF NVS with encryption enabled (`nvs_flash_init_partition("encrypted")`).
The ECDSA private key is stored as an NVS blob under namespace `"poac"`, key `"ecdsa_priv"`.
On first boot, generate key and store; subsequent boots load from NVS.

### 3.2 Agent (`agent.h/c` → `dualshock_agent.h/c`)

**Major rearchitecture** for gaming. See skeleton code in `controller/firmware/src/dualshock_agent.c`.

Key differences:

| Pebble Agent | DualShock Agent |
|-------------|-----------------|
| `k_thread_create()` | `xTaskCreatePinnedToCore()` |
| Thread priorities: 5/8/11 | FreeRTOS priorities: 20/15/10 (higher = more priority in FreeRTOS) |
| `k_sleep(K_MSEC(30000))` | `vTaskDelay(pdMS_TO_TICKS(1))` for L1 (1 ms poll) |
| `perception_capture()` | `ds_input_poll()` (SPI read from stock MCU) |
| `tinyml_infer()` | `tinyml_anticheat_infer()` (gaming model) |
| Environmental states: IDLE/ALERT/PSM | Gaming states: IDLE/SESSION/TOURNAMENT/CHEAT_ALERT/CALIBRATION |
| Anomaly: env threshold | Cheat: 8-class classifier output |
| World model: env baselines | World model: player skill profile |

### 3.3 TinyML (`tinyml.h/c` → `tinyml_anticheat.h/c`)

**New model architecture** for anti-cheat. See skeleton code in `controller/firmware/src/tinyml_anticheat.c`.

| Pebble TinyML | DualShock TinyML |
|--------------|-----------------|
| Input: 100×3 accel window (300 floats) | Input: 100×30 feature window (3000 values, INT8) |
| Model: activity recognition CNN | Model: anti-cheat CNN (Conv1D stack) |
| Classes: 5 (stationary/walking/vehicle/fall/anomaly) | Classes: 8 (nominal/skilled/6 cheat types) |
| Flash: <80 KB | Flash: <60 KB (optimized for gaming) |
| RAM arena: <32 KB | RAM arena: <24 KB |
| Inference: ~10 ms on Cortex-M33 @ 64 MHz | Inference: ~4 ms on Xtensa LX7 @ 240 MHz |
| Framework: Edge Impulse C++ SDK | Framework: TFLite Micro (ESP-NN optimized) |

### 3.4 Perception (`perception.h/c` → `ds_input.h/c`)

Complete replacement. The DualShock "sensor suite" is controller inputs:

```c
/* Gaming input snapshot — replaces perception_t */
typedef struct __attribute__((packed)) {
    /* Buttons: 18 inputs packed into 3 bytes */
    uint8_t  buttons[3];

    /* Analog sticks: [-32768, 32767] */
    int16_t  left_stick_x, left_stick_y;
    int16_t  right_stick_x, right_stick_y;

    /* Triggers: [0, 255] */
    uint8_t  l2_trigger, r2_trigger;

    /* IMU (6-axis) */
    float    gyro_x, gyro_y, gyro_z;     /* rad/s */
    float    accel_x, accel_y, accel_z;   /* g */

    /* Touchpad (2-point) */
    uint16_t touch0_x, touch0_y;
    uint16_t touch1_x, touch1_y;
    uint8_t  touch_active;                /* bit0: t0, bit1: t1 */

    /* Metadata */
    uint16_t battery_mv;
    uint32_t frame_counter;               /* monotonic */
    uint32_t inter_frame_us;              /* µs since last frame */
} ds_input_snapshot_t;
/* sizeof = 50 bytes — committed into sensor_commitment via SHA-256 */
```

### 3.5 Economic (`economic.h/c`)

**API unchanged.** Only the energy profile constants change:

```c
static const energy_profile_t ds_energy_profile = {
    .mah_per_sensor_read   = 0.002f,   /* SPI read, negligible */
    .mah_per_cellular_tx   = 0.003f,   /* BLE notify (vs 0.08 mAh NB-IoT) */
    .mah_per_gps_fix       = 0.0f,     /* No GPS on controller */
    .mah_per_crypto_op     = 0.002f,   /* mbedTLS ECDSA (vs 0.001 CryptoCell) */
    .battery_capacity_mah  = 1000.0f,  /* DS4: 1000, DualSense: 1560 */
    .mah_per_pct           = 10.0f,    /* 1000 / 100 */
};
```

---

## 4. Build System

### 4.1 ESP-IDF Project Structure

```
controller/firmware/
├── CMakeLists.txt              # Top-level ESP-IDF project
├── sdkconfig.defaults          # Default config (BLE, NVS encryption, etc.)
├── partitions.csv              # Custom partition table (NVS encrypted + OTA)
├── main/
│   ├── CMakeLists.txt
│   ├── main.c                  # app_main(): init all subsystems, start agent
│   ├── ds_input.c              # SPI polling of stock controller MCU
│   ├── ds_input.h
│   ├── ble_service.c           # NimBLE GATT server (VAPI service)
│   ├── ble_service.h
│   └── haptic.c/h              # Motor control for feedback
├── components/
│   ├── vapi_poac/              # PoAC engine (ported from Pebble, same API)
│   │   ├── CMakeLists.txt
│   │   ├── include/poac.h      # IDENTICAL to Pebble version
│   │   └── poac.c              # mbedTLS backend instead of PSA
│   ├── vapi_agent/             # Gaming agent (new implementation, same architecture)
│   │   ├── CMakeLists.txt
│   │   ├── include/dualshock_agent.h
│   │   └── dualshock_agent.c
│   ├── vapi_tinyml/            # Anti-cheat TinyML (new model, same interface pattern)
│   │   ├── CMakeLists.txt
│   │   ├── include/tinyml_anticheat.h
│   │   ├── tinyml_anticheat.c
│   │   └── models/
│   │       └── anticheat_v1.tflite   # INT8 quantized, <60 KB
│   └── vapi_economic/          # Economic evaluator (ported, same API)
│       ├── CMakeLists.txt
│       ├── include/economic.h  # IDENTICAL to Pebble version
│       └── economic.c          # Same implementation, different energy constants
└── test/                       # Unity test framework
    ├── test_poac.c
    ├── test_agent.c
    └── test_anticheat.c
```

### 4.2 Build Commands

```bash
# Prerequisites: ESP-IDF v5.2+
# Install: https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/get-started/

# Configure
cd controller/firmware
idf.py set-target esp32s3

# Build
idf.py build

# Flash via USB (controller connected via USB-C/Micro-B)
idf.py -p /dev/ttyUSB0 flash

# Monitor serial output
idf.py -p /dev/ttyUSB0 monitor

# Flash via companion app (OTA over BLE)
# Uses the companion app's firmware flasher (esptool wrapper)
python ../app/vapi-dualshock-companion.py flash --ble-addr XX:XX:XX:XX:XX:XX \
    --firmware build/vapi-dualshock.bin
```

### 4.3 Key sdkconfig Options

```
# BLE
CONFIG_BT_ENABLED=y
CONFIG_BT_NIMBLE_ENABLED=y
CONFIG_BT_NIMBLE_MAX_CONNECTIONS=1
CONFIG_BT_NIMBLE_EXT_ADV=y

# Crypto
CONFIG_MBEDTLS_HARDWARE_SHA=y
CONFIG_MBEDTLS_ECDSA_C=y
CONFIG_MBEDTLS_ECP_DP_SECP256R1_ENABLED=y

# NVS Encryption
CONFIG_NVS_ENCRYPTION=y

# FreeRTOS
CONFIG_FREERTOS_HZ=1000
CONFIG_FREERTOS_UNICORE=n

# Performance
CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y
CONFIG_COMPILER_OPTIMIZATION_PERF=y
CONFIG_ESP_SYSTEM_MEMPROT_FEATURE=n
```

---

## 5. Testing Strategy

### 5.1 Unit Tests (on host)

Run on x86 with mocked hardware:

```bash
cd controller/firmware
idf.py -T test build
# Or use Unity test framework directly:
# gcc -o test_runner test/test_poac.c components/vapi_poac/poac.c -lmbedtls -lm
```

Key tests:
- PoAC record generation and verification (mbedTLS backend)
- Chain integrity (hash linkage, counter monotonicity)
- Feature extraction correctness
- TinyML inference output range
- Economic utility computation
- World model hash determinism

### 5.2 Integration Tests (on device)

Flash to ESP32-S3 devkit (no controller required):
- Generate synthetic input patterns (macro, aimbot, normal)
- Verify PoAC records over BLE (companion app in test mode)
- Verify on-chain submission via testnet
- Battery drain measurement

### 5.3 Hardware-in-Loop Tests (with controller)

Wire ESP32-S3 devkit to a DualSense Edge via SPI:
- Verify input polling at 1 kHz
- Verify haptic feedback commands
- Verify BLE + USB simultaneous operation
- Real gameplay sessions with PoAC chain verification

---

## 6. Migration Checklist

```
[ ] Port poac.c: Replace PSA Crypto → mbedTLS (keep API identical)
[ ] Port NVS: Replace Zephyr NVS → ESP-IDF NVS (same key/value semantics)
[ ] Port threading: Replace k_thread → xTaskCreatePinnedToCore
[ ] Port mutexes: Replace k_mutex → xSemaphoreCreateMutex
[ ] Port timers: Replace k_sleep → vTaskDelay
[ ] Implement ds_input.c: SPI polling of stock controller MCU
[ ] Implement dualshock_agent.c: Gaming-adapted 3-layer agent
[ ] Implement tinyml_anticheat.c: 8-class cheat detection
[ ] Implement ble_service.c: NimBLE GATT server with VAPI characteristics
[ ] Implement haptic.c: PWM motor control for feedback
[ ] Update economic.c: ESP32-S3 energy profile constants
[ ] Create TFLite model: Train anti-cheat CNN, export INT8, <60 KB
[ ] Test PoAC compatibility: Same device key → same record format → same on-chain verification
[ ] Test bridge compatibility: Companion app relays to existing VAPI bridge
[ ] Test contract compatibility: Same PoACVerifier accepts DualShock records
```

---

**Document End — VAPI DualShock Firmware Porting Guide v1.0.0**
