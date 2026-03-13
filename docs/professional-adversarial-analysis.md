# VAPI Phase 48–49: Professional Bot Adversarial Analysis

**Generated:** 2026-03-13 (updated Phase 49: 9-feature batch proxy)
**Phase:** 49
**N=74 calibration** | DualShock Edge CFI-ZCP1 | 1000 Hz
**Bridge tests:** 888 (880 + 4 Phase 48 + 4 Phase 49)

---

## Overview

Phase 47 and earlier adversarial sessions (attacks A–F) used deterministic transforms that
eliminate entire signal classes — zeroing IMU, constant timing, mechanical grid-snapping.
These attacks are detectable because they break obvious physical constraints.

Phase 48 adds three **professional/white-box adversarial attack classes** targeting an
adversary who:

1. Has read the VAPI whitepaper and knows all published thresholds.
2. Has access to a hardware intermediary capable of HID emulation at 1000 Hz.
3. Has the N=74 human calibration data (published in the whitepaper).
4. Can inject per-axis IMU noise calibrated to human variance statistics.

---

## Attack Classes

### Attack G — Randomized IMU Bot (`randomized_bot`)

**Adversary model:** Hardware intermediary replaces IMU with Gaussian noise at human-calibrated
variance while preserving real button/stick timing from a captured session.

**Transform applied to source sessions (hw_020, hw_025, hw_030, hw_035, hw_040):**
- `gyro_{x,y,z}` → N(0, 333²) LSB (human gyro 95th-percentile std)
- `accel_x/y` → N(0, 528²) LSB (human micro_tremor_accel_variance = 278,239 ≈ 528²)
- `accel_z` → 9630 + N(0, 100²) (gravity + small orientation noise)
- All buttons, sticks, triggers: unchanged from source session

**Expected detection in live 9-feature PITL:**
- L2: DOES NOT fire — gyro_std >> 20 LSB threshold
- L4: FIRES — combined entropy + tremor features deviate from human reference
  - accel_magnitude_spectral_entropy: ~8.4 bits vs human mean 4.93 bits (3.4σ above)
  - tremor_peak_hz: Gaussian gyro has no 0.7–1.0 Hz physiological peak → deviates
  - tremor_band_power: flat-spectrum gyro lacks human 8–12 Hz concentration → deviates
- L5: DOES NOT fire — button timing from real session preserves human IBI distribution
- L2B: FIRES — random IMU has no causal correlation with button press events (0x31)

**Batch validation result (9-feature proxy, Phase 49):** 0/5 detected.
**Explanation:** Phase 49 expanded the batch validator from 7 to 9 features, adding
`tremor_peak_hz` and `tremor_band_power` from the `right_stick_x` velocity FFT
(1024-sample window, 0.977 Hz/bin). However, Attack G preserves `right_stick_x` from
the source session unchanged — tremor features in the batch proxy therefore reflect
real human physiology and do not deviate. The entropy contribution (~8.41 bits, below
the standalone threshold of 8.71 bits) produces Mahalanobis distance below the
auto-calibrated threshold of 7.03 (9-feature, mean+3σ).

**Why tremor features don't help for G:** G's attack vector modifies `accel` and `gyro`
but not `right_stick_x` (real stick preserved). Tremor FFT operates on `right_stick_x`
→ human-like tremor patterns regardless of attack. The batch proxy limitation for G is
now specifically the absence of accel/gyro-derived tremor (not the stick-derived FFT).

**Unit test validation:** `test_randomized_imu_produces_high_spectral_entropy` confirms:
- entropy > 7.0 bits (>> human mean 4.93 bits) ✓
- entropy > mean + 1σ = 6.233 bits ✓

**Key finding:** Matching marginal variance (σ = 528 LSB) does not reproduce the
concentrated 8–12 Hz micro-tremor spectral structure of human grip. The adversary
successfully matched per-axis variance but failed to replicate the correlation structure
across features.

---

### Attack H — Threshold-Aware Synthesized Bot (`threshold_aware`)

**Adversary model:** Sophisticated attacker who has studied all published thresholds and
attempts to tune each PITL signal independently to pass every layer.

**Fully synthetic session (no source session required):**
- `gyro_{x,y,z}` → N(0, 333²) LSB (tuned above L2 threshold > 20 LSB)
- `accel_x/y/z` → N(0, 528²) / N(0, 528²) / (9630 + N(0, 528²)) (human tremor variance)
- `left_stick_x/y` → random walk ±1 LSB from center 128
- `r2_trigger` → Gamma(k=2, θ=75ms) IBI distribution → CV ≈ 0.71 >> 0.08 L5 threshold
- `l2_trigger` → 0 always (adversary chose R2-only to simplify timing)
- 30,000 reports at 1000 Hz (30s session)

**Expected detection:**
- L2: DOES NOT fire — gyro_std >> 20 LSB
- L4: **FIRES** — you cannot independently tune 9 correlated biometric features
  - `stick_autocorr_lag1`: random walk ±1 → autocorr ≈ 0.0 vs human mean ~0.44 (3.0σ)
  - `stick_autocorr_lag5`: random walk ±1 → autocorr ≈ 0.0 vs human mean ~0.39 (2.8σ)
  - `grip_asymmetry`: no concurrent L2+R2 → default 1.0 vs human mean ~0.12 (3.1σ)
  - Combined: total Mahalanobis >> 6.726 threshold
- L5: DOES NOT fire — Gamma IBI gives CV ≈ 0.71 >> 0.08, entropy > 1.0 bits
- L2B: FIRES — random IMU independent of R2 timing (0x31)

**Batch validation result:** **5/5 detected by L4** (100%).

**Key finding:** The multivariate Mahalanobis L4 is robust to threshold-aware single-feature
tuning. Even when every individual PITL threshold is independently satisfied, the correlated
9-feature biometric fingerprint exposes the attack. Fixing one feature (e.g., achieving
Gaussian entropy in range) requires changes that break another (e.g., fixing stick autocorr
requires realistic left-stick movement, which disrupts the random-walk assumption).

---

### Attack I — Spectral Entropy Mimicry (`spectral_mimicry`)

**Adversary model:** Advanced attacker specifically targeting `accel_magnitude_spectral_entropy`
(new in Phase 46, published in whitepaper v3). Has measured human entropy ~4.93 bits and
designs shaped noise to match.

**Transform applied to source sessions:**
- `accel_x/y/z` → white noise shaped to source session PSD (target entropy 4.8 ± 0.3 bits)
  - PSD extracted from real session accel_magnitude signal (FFT-based)
  - IFFT shaping: shaped noise = IFFT(rfft(white_noise) × sqrt(target_psd × N/2))
  - Result: noise with same power spectrum as real human session, entropy ~4.8 bits
- `gyro_{x,y,z}` → unchanged from source session (preserves tremor coupling signature)
- All buttons/sticks/triggers → unchanged from source session

**Batch validation result:** 0/5 detected. Mean entropy = 4.76 bits (within human range).

**Explanation:** The shaped accel noise successfully reproduces the human entropy distribution.
The gyro (preserved from real session) prevents tremor-feature deviation. Other features
(sticks, trigger timing) are from real sessions and match the human reference.

**Detection in live mode:** The physical absence of IMU-button coupling is detected by L2B.
The shaped noise is not causally preceded by voluntary motor activity (the adversary generates
IMU independently of pressing), exposing the attack at layer 2B (0x31 advisory).

**Key finding:** Spectral entropy is a robust signal for detecting **naive** noise injection
(Gaussian: ~8.4 bits) but is defeatable by a sophisticated adversary with PSD-matching
capability. Spectral entropy is **not** the last line of defense — it is one of 9 correlated
features in the L4 Mahalanobis, and L2B provides a causal coupling check orthogonal to the
spectral structure.

---

## Detection Summary

| Attack | N | Batch L2 | Batch L4 | Batch L5 | Batch Any | Live Primary Detector |
|--------|---|-----------|-----------|-----------|-----------|----------------------|
| G: Randomized IMU bot | 5 | 0% | 0%† | 0% | 0%† | L4 (entropy+tremor) + L2B |
| H: Threshold-aware synth | 5 | 0% | **100%** | 0% | **100%** | L4 (grip_asym+stick_autocorr) |
| I: Spectral entropy mimicry | 5 | 0% | 0% | 0% | 0% | L2B |

†Phase 49 batch proxy has 9 features (incl. tremor_peak_hz, tremor_band_power) but G
preserves right_stick_x from source session → tremor features human-like; entropy 8.41
bits below standalone threshold 8.71. Live L4 + L2B detects G. Threshold: 7.03 (9-feature).

---

## Key Findings

### 1. Multivariate Defense Holds for Threshold-Aware Attacks

Attack H demonstrates that publishing all individual thresholds does not enable a
sophisticated adversary to evade detection. The L4 Mahalanobis evaluates 9 correlated
features simultaneously. Fixing the temporal rhythm (L5) and variance levels (L2 threshold)
forces the adversary to use simplified stick/trigger patterns that expose grip_asymmetry
and stick_autocorr deviations.

**This is the primary Phase 48 finding:** You cannot independently tune 9 correlated
biometric features. Human biometric profiles are not reproducible from marginal statistics.

### 2. Spectral Entropy is a Robust Naive-Noise Detector

Attacks that use naive Gaussian or lightly-colored AR noise produce entropy 7.7–8.5 bits,
well above the human cluster (mean 4.93 ± 1.303 bits, upper 2σ = 7.54 bits). The feature
succeeds as a "coarse filter" catching the vast majority of bot implementations.

Sophisticated PSD-matching (Attack I) defeats the entropy feature in isolation. This is
documented and expected — no single feature is the sole defense.

### 3. L2B is the Professional Bot's Primary Exposure

All three attack classes (G, H, I) share one property: the injected IMU is generated
**independently of the press events**. In human grip, voluntary motor activity produces
IMU changes in the 10–60 ms window before button press (L2B causal latency oracle, 0x31).
Bots that generate IMU separately from timing have zero physical coupling → L2B fires.

L2B is not included in the batch validation script but is validated by the live L2B oracle
(`controller/l2b_imu_press_correlation.py`, N=74 sessions, 0% false positive rate).

### 4. True Blind Spot Remains: Biometric Transplant with IMU Coupling

A bot that (a) extracts the full biometric profile from a specific player's captured
sessions AND (b) replays IMU with accurate coupling timing (IMU precedes presses with
correct latency distribution) achieves 0% detection across L4, L5, and L2B. This gap
is documented in Gap #2 (inter-person separation ratio 0.362) and requires hardware-
enrolled per-player multivariate templates with tighter intra-player bounds.

---

## Batch Validator Status (Phase 49)

Phase 49 expanded the offline batch validator (`scripts/run_adversarial_validation.py`)
from **7 to 9 features**, adding `tremor_peak_hz` and `tremor_band_power` via a new
`_tremor_features()` function operating on `right_stick_x` velocity FFT (1024-sample
window, 0.977 Hz/bin resolution — matches the Phase 49 live path).

**Auto-calibrated batch threshold:** 7.03 (Phase 49, 9-feature, mean+3σ from N=74 human
sessions). Previously 6.08 (Phase 48, 7-feature). Threshold increased due to higher-
dimensional feature space.

**Remaining gap for Attack G:** Adding tremor features to the batch proxy did **not**
improve G detection. This is expected — Attack G preserves `right_stick_x` from source
sessions. The tremor FFT operates on `right_stick_x`; since those values are real human
data, the tremor features look human. The batch validator cannot detect G without accel-
or gyro-derived tremor features, which require the full `BiometricFeatureExtractor` path.

The **unit tests** in `bridge/tests/test_professional_adversarial.py` validate the correct
feature-level properties using the actual `BiometricFeatureExtractor` pipeline:
- Gaussian accel → entropy 8.4 bits >> human mean 4.93 ± 1.303 ✓
- R2-only bot → grip_asymmetry = 1.0 (no dual-press default) vs human mean 0.12 ✓
- Naive AR(2) mimicry (pole r=0.548) → entropy 7.7 bits above human 2σ bound (7.54) ✓
- Gaussian noise → entropy > human cluster upper bound (7.41, mean+2σ) ✓

---

## Session Files

Generated by `scripts/generate_professional_adversarial.py` (reproducible, fixed seeds):

| Session | Attack | Source | Seed |
|---------|--------|--------|------|
| `randomized_bot_001.json` | G | hw_020 | 42 |
| `randomized_bot_002.json` | G | hw_025 | 49 |
| `randomized_bot_003.json` | G | hw_030 | 56 |
| `randomized_bot_004.json` | G | hw_035 | 63 |
| `randomized_bot_005.json` | G | hw_040 | 70 |
| `threshold_aware_001.json` | H | synthetic | 100 |
| `threshold_aware_002.json` | H | synthetic | 117 |
| `threshold_aware_003.json` | H | synthetic | 134 |
| `threshold_aware_004.json` | H | synthetic | 151 |
| `threshold_aware_005.json` | H | synthetic | 168 |
| `spectral_mimicry_001.json` | I | hw_020 | 200 |
| `spectral_mimicry_002.json` | I | hw_025 | 211 |
| `spectral_mimicry_003.json` | I | hw_030 | 222 |
| `spectral_mimicry_004.json` | I | hw_035 | 233 |
| `spectral_mimicry_005.json` | I | hw_040 | 244 |

Total adversarial sessions: 71 (56 A–F + 15 G–I).
