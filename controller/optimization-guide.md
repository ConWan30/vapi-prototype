# VAPI DualShock — Development Programming Optimization Guide

**Target:** ESP32-S3 inside DualSense Edge/5 controller shell
**Constraints:** 512 KB SRAM, 1000 mAh battery (shared with stock controller), BLE 5.0 link
**Goal:** Maximize anti-cheat detection accuracy while minimizing latency, memory, and energy

---

## 1. CPU Optimization — Dual-Core Task Partitioning

The ESP32-S3 has two Xtensa LX7 cores at 240 MHz. Correct core pinning is critical
for deterministic anti-cheat timing:

### 1.1 Core Assignment

```
Core 0 (Protocol Core):
  - NimBLE stack (~48 KB SRAM)
  - L2 Anti-Cheat Deliberative thread (priority 15)
  - L3 Economic Strategic thread (priority 10)
  - WiFi (if enabled for dev mode)

Core 1 (Real-Time Core):
  - L1 Gaming Reflexive thread (priority 20) — EXCLUSIVE
  - SPI input polling (1 kHz)
  - TinyML inference (10 Hz)
  - PoAC generation (2-10 Hz)
  - Feature extraction (continuous)
```

**Why this matters:** BLE event processing on core 0 can cause up to 3 ms jitter.
By pinning L1 to core 1, input polling maintains <50 µs jitter — essential for
accurate reaction time measurement and macro detection.

### 1.2 FreeRTOS Configuration

```c
// sdkconfig critical settings:
CONFIG_FREERTOS_HZ=1000          // 1 ms tick resolution
CONFIG_FREERTOS_UNICORE=n        // Dual-core mode
CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y
CONFIG_COMPILER_OPTIMIZATION_PERF=y  // -O2 optimization
CONFIG_ESP_SYSTEM_MEMPROT_FEATURE=n  // Disable for PSRAM access speed
```

### 1.3 Avoiding Priority Inversion

The L1 thread (priority 20) shares the world model mutex with L2 (priority 15).
FreeRTOS priority inheritance prevents unbounded inversion, but minimize critical
section duration:

```c
// BAD: Long critical section
xSemaphoreTake(s_wm_mutex, portMAX_DELAY);
wm_compute_hash(wm_hash);  // ~2 ms SHA-256!
xSemaphoreGive(s_wm_mutex);

// GOOD: Copy-then-compute
xSemaphoreTake(s_wm_mutex, portMAX_DELAY);
memcpy(&wm_copy, &s_world_model, sizeof(wm_copy));  // <10 µs
xSemaphoreGive(s_wm_mutex);
wm_compute_hash_from_copy(&wm_copy, wm_hash);  // Outside lock
```

---

## 2. Memory Optimization

### 2.1 SRAM Budget Breakdown

Total available: 512 KB. Target usage: <470 KB (42 KB headroom for stack overflow guard).

| Component | Baseline | Optimized | Savings | Technique |
|-----------|----------|-----------|---------|-----------|
| Input ring buffer | 51.2 KB (256×200B) | 25.6 KB (128×200B) | 25.6 KB | Reduce depth to 128 frames (still 128 ms history) |
| TinyML arena | 24 KB | 22 KB | 2 KB | Tune arena via `MicroInterpreter::arena_used_bytes()` |
| World model | 12.5 KB (64×196B) | 6.4 KB (32×200B) | 6.1 KB | Reduce history to 32 (sufficient for skill profiling) |
| PoAC queue | 7.3 KB (32×228B) | 3.6 KB (16×228B) | 3.7 KB | Reduce depth; L3 drains every 60s at 2 Hz = ~120 records |
| Feature window | 12 KB (100×120B) | 12 KB | 0 | Cannot reduce — model input size is fixed |
| BLE buffers | 16 KB | 8 KB | 8 KB | Reduce ATT MTU to 244, single connection |
| Thread stacks | 26 KB | 20 KB | 6 KB | Profile with `uxTaskGetStackHighWaterMark()` |
| **Total saved** | | | **~51 KB** | |

### 2.2 PSRAM Usage (8 MB available)

PSRAM has ~10× higher access latency than internal SRAM (80 ns vs 8 ns). Use it
only for non-latency-critical data:

```c
// Allocate debug/replay buffer in PSRAM
ds_input_snapshot_t *replay_buf = heap_caps_malloc(
    sizeof(ds_input_snapshot_t) * 10000,  // 500 KB replay buffer
    MALLOC_CAP_SPIRAM
);

// Keep TinyML arena in internal SRAM for inference speed
uint8_t s_arena[AC_MODEL_MAX_ARENA] __attribute__((section(".iram1")));
```

**PSRAM candidates:** Replay storage, debug logs, OTA staging buffer, extended
world model history, training data collection buffer.

### 2.3 Flash Optimization

The TinyML model is the largest flash consumer (~55 KB). Optimize model size:

| Technique | Size Reduction | Accuracy Impact |
|-----------|---------------|-----------------|
| INT8 quantization (baseline) | — | <1% accuracy loss |
| Pruning (50% sparsity) | ~30% | 1-3% accuracy loss |
| Knowledge distillation | ~40% | 2-5% accuracy loss |
| Reduce Conv1D filters (32→16) | ~50% | 3-7% accuracy loss |
| Remove Dropout layers (inference only) | ~5% | None |

**Recommended:** INT8 quantization + 30% pruning → ~38 KB model, <2% accuracy impact.

---

## 3. Battery Optimization

### 3.1 Power Budget

Stock DualSense Edge draws ~50 mA average. VAPI adds ~37 mA. Combined: ~87 mA.
With 1000 mAh battery: **~11.5 hours** (vs ~20 hours stock).

| Component | Current | Duty Cycle | Effective mA |
|-----------|---------|------------|-------------|
| ESP32-S3 active (240 MHz) | 68 mA | 100% | 68.0 |
| ESP32-S3 light sleep | 0.3 mA | 0% (during gameplay) | 0.0 |
| SPI polling (1 kHz) | +2 mA | 100% | 2.0 |
| SHA-256 hardware | +5 mA | 0.1% (2 Hz × 0.5 ms) | 0.005 |
| ECDSA-P256 (mbedTLS) | +15 mA | 0.06% (2 Hz × 3 ms) | 0.009 |
| TinyML inference | +20 mA | 0.5% (10 Hz × 5 ms) | 0.1 |
| BLE advertising | +8 mA | 1% | 0.08 |
| BLE connected (2 Hz notify) | +10 mA | 0.4% | 0.04 |
| **VAPI total overhead** | | | **~37 mA** |

### 3.2 Dynamic Power Modes

```
Mode            L1 Rate     TinyML Rate   PoAC Rate   Added mA   Battery Life
──────────────  ──────────  ────────────  ──────────  ─────────  ────────────
IDLE            Off         Off           Off         5 mA       ~18 hr
SESSION         1 kHz       10 Hz         2 Hz        37 mA      ~11.5 hr
TOURNAMENT      1 kHz       20 Hz         10 Hz       65 mA      ~8.7 hr
LOW_BATTERY     500 Hz      5 Hz          1 Hz        22 mA      ~13.5 hr
CALIBRATION     1 kHz       Off           Off         15 mA      ~15 hr
```

### 3.3 ESP32-S3 Sleep During Idle

When no game session is active, the ESP32-S3 enters light sleep between BLE
connection events. This drops consumption from 68 mA to <1 mA:

```c
// In IDLE state: enable automatic light sleep
esp_pm_config_esp32s3_t pm_config = {
    .max_freq_mhz = 240,
    .min_freq_mhz = 80,      // Reduce CPU when idle
    .light_sleep_enable = true,
};
esp_pm_configure(&pm_config);

// BLE wakes the CPU for connection events automatically
// Input polling is disabled in IDLE (no game session)
```

**Result:** In IDLE mode (paired, waiting for session), VAPI adds only ~5 mA to the
stock controller's ~15 mA standby, yielding ~50 hours standby.

### 3.4 PoAC Rate Adaptation

The biggest energy lever is PoAC generation rate. Each PoAC record costs:

```
SHA-256 × 3 (sensor + world model + chain): 3 × 0.5 ms × 5 mA = 0.002 mAh
ECDSA sign: 3 ms × 15 mA = 0.0125 mAh
BLE notify: 2 ms × 10 mA = 0.006 mAh
Total per record: ~0.02 mAh
```

At 2 Hz: 0.04 mAh/s = 144 mAh/hr
At 10 Hz (tournament): 0.2 mAh/s = 720 mAh/hr

**Optimization:** In SESSION mode with clean play (no cheat flags for >60 s),
reduce PoAC to 1 Hz. Increase to 5 Hz on any suspicious inference. This
provides adaptive coverage while conserving energy.

```c
static uint32_t adaptive_poac_interval_ms(void) {
    if (s_state == DS_STATE_TOURNAMENT)     return 100;   // 10 Hz
    if (s_state == DS_STATE_CHEAT_ALERT)    return 200;   // 5 Hz
    if (s_consecutive_clean > 120)          return 1000;  // 1 Hz (clean streak)
    return 500;  // 2 Hz default
}
```

---

## 4. Anti-Cheat Performance Tuning

### 4.1 Feature Extraction Optimization

Feature extraction runs at 1 kHz (every input poll). Each extraction must
complete in <200 µs to avoid blocking the next poll:

| Feature | Naive Cost | Optimized Cost | Technique |
|---------|-----------|---------------|-----------|
| Stick velocity | 4 div + 2 sqrt | 2 div + 1 sqrt | Cache magnitude, reuse |
| Stick jerk | 6 div + 2 sqrt | 2 sub | Use acceleration delta (no sqrt) |
| Press variance | O(n) sum | O(1) running | Welford's online algorithm |
| IMU correlation | O(n) dot product | O(1) running | Incremental accumulator with decay |
| Touch entropy | O(n log n) histogram | O(1) running | Incremental histogram update |
| **Total** | **~80 µs** | **~25 µs** | |

**Welford's algorithm for press variance** (O(1) per update):

```c
// Running mean and variance without storing all values
static float s_press_mean = 0;
static float s_press_m2 = 0;
static uint32_t s_press_n = 0;

static void update_press_stats(float new_interval) {
    s_press_n++;
    float delta = new_interval - s_press_mean;
    s_press_mean += delta / s_press_n;
    float delta2 = new_interval - s_press_mean;
    s_press_m2 += delta * delta2;
}

static float get_press_variance(void) {
    return (s_press_n > 1) ? (s_press_m2 / (s_press_n - 1)) : 999.0f;
}
```

### 4.2 TinyML Inference Optimization

Target: <5 ms per inference on ESP32-S3 at 240 MHz.

| Optimization | Speedup | Applied |
|-------------|---------|---------|
| ESP-NN acceleration (SIMD) | 2-3× | Use `esp-nn` component in ESP-IDF |
| INT8 quantization | 2-4× vs FP32 | Already required (model constraint) |
| Operator fusion (Conv+BN+ReLU) | 1.3× | TFLite Micro does this automatically |
| Reduce window size (100→50 frames) | ~1.8× | Acceptable if detection rate sufficient |
| Reduce features (30→20 per frame) | ~1.5× | Drop low-importance features |

**ESP-NN integration** (critical for ESP32-S3):

```cmake
# In components/vapi_tinyml/CMakeLists.txt
idf_component_register(
    SRCS "tinyml_anticheat.c"
    INCLUDE_DIRS "include"
    REQUIRES esp-nn esp-tflite-micro  # ESP-IDF managed components
)
```

ESP-NN provides SIMD-optimized kernels for Conv2D, DepthwiseConv2D, and
fully-connected layers on the Xtensa LX7. This alone provides ~2.5× speedup
for the Conv1D anti-cheat model.

### 4.3 Threshold Tuning Methodology

Anti-cheat thresholds directly impact false positive and false negative rates.
Use the companion app's dev tools for iterative tuning:

```
1. COLLECT labeled gameplay data via companion app
   → "dev mode" streams raw inputs at 10 Hz
   → Player labels sessions: "clean", "using_macro", "using_aimbot"
   → Produces labeled .csv: timestamp, features[30], label

2. ANALYZE feature distributions per label
   → Plot histograms of press_variance for clean vs macro
   → Plot IMU_correlation for clean vs XIM adapter
   → Plot reaction_time distribution for clean vs aimbot

3. SET thresholds at operating points
   → Macro detection: σ(inter-press) < 1.0 ms → 99.5% TPR, 0.1% FPR
   → IMU mismatch: correlation < 0.15 → 97% TPR, 0.5% FPR
   → Reaction time: sustained < 150 ms → 98% TPR, 0.2% FPR

4. VALIDATE on holdout data
   → Run heuristic classifier on unlabeled test set
   → Verify FPR < 0.5% (critical: false cheat accusations ruin UX)

5. TRAIN TinyML model on collected data
   → Use Edge Impulse or TF Lite Model Maker
   → Target 8-class CNN architecture from architecture.md
   → Export INT8 quantized, verify <60 KB
   → Upload to controller via companion app BLE OTA
```

### 4.4 Key Detection Thresholds (Defaults)

| Metric | Threshold | Cheat Type | Notes |
|--------|-----------|------------|-------|
| `press_variance` | < 1.0 ms² | MACRO | Humans: σ > 5 ms; macros: σ < 0.1 ms |
| `imu_noise_floor` | < 0.001 rad/s | INJECTION | Hand tremor: 0.01-0.05; desk: <0.001 |
| `imu_stick_corr` | < 0.15 | IMU_MISMATCH | Normal play: 0.3-0.8; adapter: <0.1 |
| `reaction_proxy` | < 150 ms (sustained 10+) | REACTION | Human min: ~150 ms; average: ~250 ms |
| `stick_jerk_r` | > 2.0 | AIMBOT | Normal aim: <0.5; snap-aim: >3.0 |
| `confidence` threshold | 180/255 (70%) | All | Below this = uncertain, don't flag |
| `cheat_resolve_count` | 10 windows | Recovery | 10 × 5s = 50s clean to clear alert |

---

## 5. BLE Optimization

### 5.1 Connection Parameters

```c
// Optimized BLE connection for gaming (low latency + reasonable power)
struct ble_gap_conn_params conn_params = {
    .itvl_min = 6,       // 7.5 ms (6 × 1.25 ms) — fast for gaming
    .itvl_max = 12,      // 15 ms — upper bound
    .latency = 0,        // No slave latency (real-time notifications)
    .supervision_timeout = 200,  // 2 seconds disconnect timeout
};
```

### 5.2 MTU Negotiation

```c
// Request 244-byte MTU for single-packet PoAC records
// 228-byte record + 3-byte ATT header + 4-byte L2CAP = 235 bytes
ble_att_set_preferred_mtu(247);  // Server preferred MTU
```

With MTU ≥ 244, each 228-byte PoAC record fits in a single BLE notification
packet. This eliminates fragmentation overhead and reduces latency.

### 5.3 Throughput Calculation

```
Normal mode (2 Hz PoAC):
  228 B × 2 = 456 B/s
  BLE 5.0 LE 2M PHY: ~1.4 Mbps effective → 175,000 B/s
  Utilization: 0.26%

Tournament mode (10 Hz PoAC):
  228 B × 10 = 2,280 B/s
  Utilization: 1.3%

Dev mode (10 Hz PoAC + 10 Hz raw input):
  (228 + 50) × 10 = 2,780 B/s
  Utilization: 1.6%
```

BLE throughput is never a bottleneck. The constraint is energy, not bandwidth.

### 5.4 Notification Batching

For tournament mode (10 Hz), batch 5 PoAC records into a single BLE indication
(5 × 228 = 1,140 bytes, sent as fragmented indication with ACK):

```c
// Batch 5 records, send every 500 ms instead of 1 record every 100 ms
// Reduces BLE radio-on events by 5×, saving ~40% BLE energy
#define BLE_BATCH_SIZE   5
#define BLE_BATCH_TIMEOUT_MS  500

static poac_record_t ble_batch[BLE_BATCH_SIZE];
static uint8_t ble_batch_count = 0;

void ble_enqueue_poac(const poac_record_t *record) {
    ble_batch[ble_batch_count++] = *record;
    if (ble_batch_count >= BLE_BATCH_SIZE) {
        ble_send_batch(ble_batch, ble_batch_count);
        ble_batch_count = 0;
    }
}
```

---

## 6. PoAC Compression

### 6.1 Delta Encoding for Chain Transmission

Consecutive PoAC records share significant redundancy (same model hash, similar
timestamps, same bounty ID). Delta encoding reduces BLE payload:

| Field | Full Size | Delta Size | Savings |
|-------|-----------|------------|---------|
| `prev_poac_hash` | 32 B | 0 B (reconstructible) | 32 B |
| `sensor_commitment` | 32 B | 32 B (unique per record) | 0 |
| `model_manifest_hash` | 32 B | 1 B (flag: "same as prev") | 31 B |
| `world_model_hash` | 32 B | 32 B (unique per record) | 0 |
| `inference_result` | 1 B | 1 B | 0 |
| `action_code` | 1 B | 1 B | 0 |
| `confidence` | 1 B | 1 B | 0 |
| `battery_pct` | 1 B | 1 B | 0 |
| `monotonic_ctr` | 4 B | 0 B (prev + 1) | 4 B |
| `timestamp_ms` | 8 B | 2 B (delta from prev, ms) | 6 B |
| `latitude` | 8 B | 0 B (no GPS on controller) | 8 B |
| `longitude` | 8 B | 0 B (no GPS on controller) | 8 B |
| `bounty_id` | 4 B | 1 B (flag: "same as prev") | 3 B |
| `signature` | 64 B | 64 B (unique per record) | 0 |
| **Total** | **228 B** | **136 B** | **92 B (40%)** |

**Important:** Delta encoding is a *transport optimization only*. The canonical
228-byte record is always used for signing and on-chain verification. The companion
app reconstructs full records before relaying to the bridge.

### 6.2 Implementation

```c
typedef struct __attribute__((packed)) {
    uint8_t  flags;              // Bit 0: same model, Bit 1: same bounty
    uint8_t  sensor_commitment[32];
    uint8_t  world_model_hash[32];
    uint8_t  inference_result;
    uint8_t  action_code;
    uint8_t  confidence;
    uint8_t  battery_pct;
    uint16_t timestamp_delta_ms;  // Delta from previous (max 65 s gap)
    uint8_t  signature[64];
} poac_delta_record_t;
// sizeof = 136 bytes (40% reduction)
```

---

## 7. Testing and Profiling

### 7.1 Performance Profiling

```c
// Use esp_timer for microsecond-precision profiling
int64_t start = esp_timer_get_time();
ac_classify(&result);
int64_t elapsed = esp_timer_get_time() - start;
ESP_LOGI("PERF", "TinyML inference: %lld µs", elapsed);

// Profile memory usage
ESP_LOGI("MEM", "Free heap: %d, min free: %d, PSRAM: %d",
    esp_get_free_heap_size(),
    esp_get_minimum_free_heap_size(),
    heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

// Profile stack usage per thread
UBaseType_t hwm = uxTaskGetStackHighWaterMark(s_l1_task);
ESP_LOGI("STACK", "L1 high water mark: %d bytes remaining", hwm * 4);
```

### 7.2 Performance Targets

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Input poll jitter | < 50 µs | GPIO toggle + oscilloscope |
| Feature extraction | < 25 µs per frame | `esp_timer_get_time()` |
| TinyML inference | < 5 ms | `ac_result_t.latency_us` |
| SHA-256 (50 B sensor) | < 0.3 ms | `esp_timer_get_time()` |
| SHA-256 (1.6 KB world model) | < 1.5 ms | `esp_timer_get_time()` |
| ECDSA-P256 sign | < 8 ms | `esp_timer_get_time()` |
| Full PoAC generation | < 10 ms | `esp_timer_get_time()` |
| BLE notify latency | < 3 ms | BLE sniffer |
| Total L1 cycle (1 ms target) | < 0.2 ms (avg) | `esp_timer_get_time()` |
| Free heap (runtime) | > 40 KB | `esp_get_free_heap_size()` |

### 7.3 Automated Test Suite

```bash
# Unit tests (host)
cd controller/firmware
idf.py -T test build flash monitor

# Key test cases:
# test_poac_generate        — Record generation + signature validity
# test_poac_chain_verify    — Chain linkage integrity
# test_poac_mbedtls_compat  — Verify mbedTLS output matches PSA Crypto (Pebble)
# test_ac_feature_extract   — Feature correctness from known inputs
# test_ac_heuristic_macro   — Macro detection on synthetic macro input
# test_ac_heuristic_nominal — No false positives on clean input
# test_ac_heuristic_aimbot  — Aimbot detection on snap-aim patterns
# test_wm_hash_determinism  — Same world model → same hash (cross-platform)
# test_economic_utility     — Utility function matches Pebble output
# test_poac_format_compat   — 228-byte format byte-identical to Pebble records
```

### 7.4 Cross-Platform Compatibility Test

The most critical test: a PoAC record generated by the DualShock ESP32-S3 must
be verifiable by the same `PoACVerifier` Solidity contract that verifies Pebble
records. This validates:

1. Same 228-byte wire format (field order, endianness, padding)
2. Same SHA-256 output (ESP32 HW SHA == CryptoCell-310 SHA)
3. Same ECDSA-P256 signature format (mbedTLS raw r‖s == PSA raw r‖s)
4. Same `keccak256(pubkey)` device ID computation

```python
# Python cross-platform test (run in bridge environment)
from vapi_bridge.codec import parse_record, verify_signature

# Load a record generated by DualShock ESP32-S3
with open("test_dualshock_record.bin", "rb") as f:
    raw = f.read()  # 228 bytes

record = parse_record(raw)  # Same parser as Pebble records
assert verify_signature(record, dualshock_pubkey)
assert len(raw) == 228

# Verify the same record would pass on-chain verification
# (submit to IoTeX testnet PoACVerifier contract)
```

---

## 8. Common Pitfalls and Solutions

| Pitfall | Symptom | Solution |
|---------|---------|----------|
| BLE stack on core 1 | L1 jitter >3 ms | Pin BLE to core 0, L1 to core 1 |
| TinyML in PSRAM | Inference >15 ms | Keep arena in internal SRAM |
| Float in ISR | Hard fault | Use integer math in SPI ISR handler |
| NVS write in L1 | Periodic 20 ms stall | Move NVS writes to L3 (batch persist) |
| Unbounded PoAC queue | OOM crash | Fixed ring buffer with drop-oldest |
| mbedTLS heap alloc | Fragmentation | Pre-allocate ECDSA context at init |
| BLE MTU < 228 | Fragmented PoAC | Negotiate MTU 247 at connection |
| World model mutex in L1 | Priority inversion | Copy-then-hash pattern (§1.3) |
| No GPS fields | Non-zero lat/lon | Hardcode 0.0 (controller has no GPS) |
| Signature format mismatch | On-chain verify fails | Ensure raw r‖s (64 B), not DER |

---

## 9. Quick Reference — Build and Flash

```bash
# First-time setup
cd controller/firmware
idf.py set-target esp32s3
idf.py menuconfig  # Set BLE, crypto, NVS options per sdkconfig.defaults

# Build
idf.py build

# Flash via USB
idf.py -p /dev/ttyUSB0 flash

# Monitor serial output
idf.py -p /dev/ttyUSB0 monitor

# Run tests
idf.py -T test build flash monitor

# Flash via companion app (BLE OTA)
cd ../app
python vapi-dualshock-companion.py  # Start companion app
# Use /api/dev/flash endpoint or dashboard UI

# Generate size report
idf.py size-components  # Shows per-component flash/RAM usage
```

---

**Document End — VAPI DualShock Optimization Guide v1.0.0**
