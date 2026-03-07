# Button Fingerprint Analysis — DualShock Edge (N=40 USB Sessions)

**Game:** NCAA College Football 26 | **Device:** DualShock Edge CFI-ZCP1 | **Polling rate:** ~1000 Hz (USB) | **Sessions:** 40

> **Note:** This is pure data mining from existing hardware sessions. No PITL layer code was modified.

## 1. Per-Button Summary

| Button | Total Presses | Sessions w/ Data | Mean Interval (ms) | Mean CV | Mean Entropy (bits) | Mean Hold (ms) | Viability |
|--------|--------------|-----------------|-------------------|---------|--------------------|--------------:|-----------|
| Cross (X) | 3,347 | 39/40 | 1883.6 | 1.373 | 4.945 | 127.0 | **HIGH** *** |
| R2 (digital) | 1,180 | 35/40 | 4487.8 | 1.176 | 4.496 | 949.5 | **MEDIUM** ** |
| D-pad Down | 590 | 15/40 | 6214.5 | 2.035 | 3.807 | 89.1 | **LOW** * |
| L2 (digital) | 553 | 27/40 | 8586.7 | 1.333 | 3.462 | 242.9 | **MEDIUM** ** |
| Square | 471 | 20/40 | 9971.3 | 1.620 | 3.371 | 171.4 | **LOW** * |
| Circle (O) | 464 | 20/40 | 11023.1 | 1.441 | 3.666 | 132.1 | **LOW** * |
| Triangle | 453 | 21/40 | 10632.7 | 1.137 | 3.573 | 276.5 | **MEDIUM** ** |
| L1 | 341 | 17/40 | 14333.6 | 0.811 | 3.512 | 523.6 | **LOW** * |
| R1 | 239 | 9/40 | 15969.2 | 2.117 | 2.849 | 132.0 | **LOW** * |
| D-pad Right | 202 | 8/40 | 7458.1 | 1.748 | 3.547 | 90.0 | **LOW** * |
| D-pad Left | 88 | 1/40 | 19883.5 | 0.955 | 3.170 | 90.2 | **MEDIUM** ** |
| L3 (L-stick click) | 42 | 0/40 | 35921.1 | N/A | N/A | 309.4 | **LOW** * |
| R3 (R-stick click) | 30 | 0/40 | 20317.1 | N/A | N/A | 93.5 | **LOW** * |
| D-pad Up | 3 | 0/40 | 6749.5 | N/A | N/A | 0.0 | **LOW** * |
| Options | 3 | 0/40 | 0.0 | N/A | N/A | 0.0 | **LOW** * |
| Create (Share) | 1 | 0/40 | 0.0 | N/A | N/A | 0.0 | **LOW** * |

## 2. Top 5 Most Stable Button Timing Features

Ranked by inter-session CV stability (low CV std / CV mean = more consistent across sessions).
Stable buttons are reliable biometric anchors — a bot's artificially low CV would stand out.

| Rank | Button | Mean CV | CV Std (across sessions) | Relative Stability | Sessions |
|------|--------|---------|--------------------------|-------------------|----------|
| 1 | Cross (X) | 1.3726 | 0.2962 | 0.2158 | 39/40 |
| 2 | Triangle | 1.1375 | 0.2877 | 0.2529 | 21/40 |
| 3 | Circle (O) | 1.4409 | 0.4388 | 0.3046 | 20/40 |
| 4 | L1 | 0.8113 | 0.2619 | 0.3228 | 17/40 |
| 5 | Square | 1.6204 | 0.5781 | 0.3568 | 20/40 |

## 3. Top 5 Most Discriminative Button Transitions

Cross-button transitions within 100ms. Low CV = tight timing = hardest to fake.
A macro bot would produce near-constant transition times (CV -> 0).

| Rank | Transition | Count | Mean (ms) | Std (ms) | CV | Macro-detectable? |
|------|-----------|-------|-----------|----------|-----|-------------------|
| 1 | D-pad Down -> Cross (X) | 20 | 77.3 | 18.0 | 0.233 | YES (tight) |
| 2 | Circle (O) -> Square | 43 | 67.6 | 20.5 | 0.304 | moderate |
| 3 | R1 -> Square | 29 | 54.2 | 16.8 | 0.310 | moderate |
| 4 | R2 (digital) -> Square | 22 | 60.7 | 22.3 | 0.367 | moderate |
| 5 | R2 (digital) -> Cross (X) | 119 | 67.6 | 25.0 | 0.370 | moderate |

## 4. Biometric Viability Scores

| Button | Viability | Freq Score | Stability Score | Recommended Use |
|--------|-----------|------------|----------------|----------------|
| Cross (X) | **HIGH** | HIGH | HIGH | Add to L5 + L4 biometric |
| R2 (digital) | **MEDIUM** | HIGH | MEDIUM | Consider for L5 |
| D-pad Down | **LOW** | MEDIUM | MEDIUM | Skip (insufficient data) |
| L2 (digital) | **MEDIUM** | HIGH | MEDIUM | Consider for L5 |
| Square | **LOW** | MEDIUM | MEDIUM | Skip (insufficient data) |
| Circle (O) | **LOW** | MEDIUM | MEDIUM | Skip (insufficient data) |
| Triangle | **MEDIUM** | MEDIUM | HIGH | Consider for L5 |
| L1 | **LOW** | MEDIUM | MEDIUM | Skip (insufficient data) |
| R1 | **LOW** | LOW | MEDIUM | Skip (insufficient data) |
| D-pad Right | **LOW** | LOW | MEDIUM | Skip (insufficient data) |
| D-pad Left | **MEDIUM** | LOW | HIGH | Consider for L5 |
| L3 (L-stick click) | **LOW** | LOW | LOW | Skip (insufficient data) |
| R3 (R-stick click) | **LOW** | LOW | LOW | Skip (insufficient data) |
| D-pad Up | **LOW** | LOW | LOW | Skip (insufficient data) |
| Options | **LOW** | LOW | LOW | Skip (insufficient data) |
| Create (Share) | **LOW** | LOW | LOW | Skip (insufficient data) |

## 5. Per-Session Button Fingerprint (Mahalanobis Analysis)

Top 4 buttons by press count: **Cross (X), R2 (digital), D-pad Down, L2 (digital)**

Feature vector: [CV, Entropy] x 4 buttons = 8-dimensional
Valid sessions (all top buttons have >=10 presses): **8** / 40

| Metric | Value |
|--------|-------|
| Mean Mahalanobis distance (session->centroid) | 2.475 |
| Std Mahalanobis distance | 0.000 |
| Max Mahalanobis distance | 2.475 |

**Interpretation:** Low mean Mahalanobis distance (< 2.0) indicates stable fingerprint across sessions — strong biometric signal. This player's button timing fingerprint has mean distance = **2.475** (STABLE).

**Per-session distances (top 10 outliers):**
  - hw_006: 2.475
  - hw_015: 2.475
  - hw_020: 2.475
  - hw_024: 2.475
  - hw_026: 2.475
  - hw_028: 2.475
  - hw_029: 2.475
  - hw_035: 2.475

## 6. R2-Only vs Multi-Button L5 Signal Comparison

Current L5 uses **R2 (digital) only** for timing analysis. Multi-button expands to: **r2_dig, cross, l1, r1**.

| Metric | R2-Only | Multi-Button (avg) | Improvement? |
|--------|---------|--------------------|--------------|
| Sessions usable | 35 | 36 | More data |
| Mean CV | 1.1760 | 1.2780 | Higher = more human-like margin |
| CV Std (consistency) | 0.4583 | 0.3754 | — |
| False-positive rate (CV < 0.08) | 0.0% | 0.0% | Fewer FPs |

## 7. Recommendations

### Add to L5 Temporal Rhythm Analysis

- **Cross (X)**: mean CV=1.373, entropy=4.945 bits, 39/40 sessions, viability=HIGH

### Add to L4 Biometric Fingerprint

Hold-duration vectors (mean hold time per button) make excellent L4 features — they are stable, amplitude-based, and not sensitive to polling rate.
- **Cross (X)**: mean hold = 127.0 ms
- **R2 (digital)**: mean hold = 949.5 ms
- **L2 (digital)**: mean hold = 242.9 ms
- **Triangle**: mean hold = 276.5 ms
- **D-pad Left**: mean hold = 90.2 ms

### Button Transitions for Anti-Macro Detection

These transitions have tight enough timing distributions to detect macros (constant-interval replay would produce CV -> 0):
- **D-pad Down -> Cross (X)**: mean 77.3ms ± 18.0ms (CV=0.233)
- **Circle (O) -> Square**: mean 67.6ms ± 20.5ms (CV=0.304)
- **R1 -> Square**: mean 54.2ms ± 16.8ms (CV=0.310)

## 8. Raw Per-Button Detail

### Cross (X)
- Total presses: 3,347 across 40 sessions
- Sessions with >=10 presses: 39/40
- Press count per session: mean=83.7, std=39.2, min=0, max=150
- Inter-press interval: mean=1883.6ms, std=2904.9ms
- CV (across sessions): mean=1.3726, std=0.2962 -> stability=HIGH
- Shannon entropy: mean=4.945 bits (39 sessions)
- Hold duration: mean=127.0ms ± 26.8ms
- **Biometric viability: HIGH** (freq=HIGH, stab=HIGH)

### R2 (digital)
- Total presses: 1,180 across 40 sessions
- Sessions with >=10 presses: 35/40
- Press count per session: mean=29.5, std=13.4, min=0, max=68
- Inter-press interval: mean=4487.8ms, std=6240.4ms
- CV (across sessions): mean=1.1760, std=0.4583 -> stability=MEDIUM
- Shannon entropy: mean=4.496 bits (35 sessions)
- Hold duration: mean=949.5ms ± 312.1ms
- **Biometric viability: MEDIUM** (freq=HIGH, stab=MEDIUM)

### D-pad Down
- Total presses: 590 across 40 sessions
- Sessions with >=10 presses: 15/40
- Press count per session: mean=14.8, std=18.5, min=0, max=69
- Inter-press interval: mean=6214.5ms, std=15326.6ms
- CV (across sessions): mean=2.0348, std=0.7697 -> stability=MEDIUM
- Shannon entropy: mean=3.807 bits (15 sessions)
- Hold duration: mean=89.1ms ± 8.6ms
- **Biometric viability: LOW** (freq=MEDIUM, stab=MEDIUM)

### L2 (digital)
- Total presses: 553 across 40 sessions
- Sessions with >=10 presses: 27/40
- Press count per session: mean=13.8, std=10.2, min=0, max=52
- Inter-press interval: mean=8586.7ms, std=13226.8ms
- CV (across sessions): mean=1.3329, std=0.7232 -> stability=MEDIUM
- Shannon entropy: mean=3.462 bits (27 sessions)
- Hold duration: mean=242.9ms ± 94.0ms
- **Biometric viability: MEDIUM** (freq=HIGH, stab=MEDIUM)

### Square
- Total presses: 471 across 40 sessions
- Sessions with >=10 presses: 20/40
- Press count per session: mean=11.8, std=9.0, min=0, max=40
- Inter-press interval: mean=9971.3ms, std=16729.7ms
- CV (across sessions): mean=1.6204, std=0.5781 -> stability=MEDIUM
- Shannon entropy: mean=3.371 bits (20 sessions)
- Hold duration: mean=171.4ms ± 67.2ms
- **Biometric viability: LOW** (freq=MEDIUM, stab=MEDIUM)

### Circle (O)
- Total presses: 464 across 40 sessions
- Sessions with >=10 presses: 20/40
- Press count per session: mean=11.6, std=7.3, min=0, max=31
- Inter-press interval: mean=11023.1ms, std=15794.5ms
- CV (across sessions): mean=1.4409, std=0.4388 -> stability=MEDIUM
- Shannon entropy: mean=3.666 bits (20 sessions)
- Hold duration: mean=132.1ms ± 25.9ms
- **Biometric viability: LOW** (freq=MEDIUM, stab=MEDIUM)

### Triangle
- Total presses: 453 across 40 sessions
- Sessions with >=10 presses: 21/40
- Press count per session: mean=11.3, std=8.2, min=0, max=41
- Inter-press interval: mean=10632.7ms, std=14246.6ms
- CV (across sessions): mean=1.1375, std=0.2877 -> stability=HIGH
- Shannon entropy: mean=3.573 bits (21 sessions)
- Hold duration: mean=276.5ms ± 237.4ms
- **Biometric viability: MEDIUM** (freq=MEDIUM, stab=HIGH)

### L1
- Total presses: 341 across 40 sessions
- Sessions with >=10 presses: 17/40
- Press count per session: mean=8.5, std=5.0, min=0, max=21
- Inter-press interval: mean=14333.6ms, std=13614.8ms
- CV (across sessions): mean=0.8113, std=0.2619 -> stability=MEDIUM
- Shannon entropy: mean=3.512 bits (17 sessions)
- Hold duration: mean=523.6ms ± 162.3ms
- **Biometric viability: LOW** (freq=MEDIUM, stab=MEDIUM)

### R1
- Total presses: 239 across 40 sessions
- Sessions with >=10 presses: 9/40
- Press count per session: mean=6.0, std=5.1, min=0, max=19
- Inter-press interval: mean=15969.2ms, std=29212.1ms
- CV (across sessions): mean=2.1172, std=0.8152 -> stability=MEDIUM
- Shannon entropy: mean=2.849 bits (9 sessions)
- Hold duration: mean=132.0ms ± 42.1ms
- **Biometric viability: LOW** (freq=LOW, stab=MEDIUM)

### D-pad Right
- Total presses: 202 across 40 sessions
- Sessions with >=10 presses: 8/40
- Press count per session: mean=5.0, std=7.7, min=0, max=29
- Inter-press interval: mean=7458.1ms, std=16679.5ms
- CV (across sessions): mean=1.7476, std=0.6099 -> stability=MEDIUM
- Shannon entropy: mean=3.547 bits (8 sessions)
- Hold duration: mean=90.0ms ± 8.6ms
- **Biometric viability: LOW** (freq=LOW, stab=MEDIUM)

### D-pad Left
- Total presses: 88 across 40 sessions
- Sessions with >=10 presses: 1/40
- Press count per session: mean=2.2, std=2.7, min=0, max=10
- Inter-press interval: mean=19883.5ms, std=30227.4ms
- CV (across sessions): mean=0.9554, std=0.0000 -> stability=HIGH
- Shannon entropy: mean=3.170 bits (1 sessions)
- Hold duration: mean=90.2ms ± 5.7ms
- **Biometric viability: MEDIUM** (freq=LOW, stab=HIGH)

### L3 (L-stick click)
- Total presses: 42 across 40 sessions
- Sessions with >=10 presses: 0/40
- Press count per session: mean=1.1, std=1.4, min=0, max=6
- Inter-press interval: mean=35921.1ms, std=30697.5ms
- CV (across sessions): mean=N/A, std=0.0000 -> stability=LOW
- Shannon entropy: mean=N/A bits (0 sessions)
- Hold duration: mean=309.4ms ± 84.8ms
- **Biometric viability: LOW** (freq=LOW, stab=LOW)

### R3 (R-stick click)
- Total presses: 30 across 40 sessions
- Sessions with >=10 presses: 0/40
- Press count per session: mean=0.8, std=1.6, min=0, max=6
- Inter-press interval: mean=20317.1ms, std=25342.8ms
- CV (across sessions): mean=N/A, std=0.0000 -> stability=LOW
- Shannon entropy: mean=N/A bits (0 sessions)
- Hold duration: mean=93.5ms ± 9.9ms
- **Biometric viability: LOW** (freq=LOW, stab=LOW)

### D-pad Up
- Total presses: 3 across 40 sessions
- Sessions with >=10 presses: 0/40
- Press count per session: mean=0.1, std=0.5, min=0, max=3
- Inter-press interval: mean=6749.5ms, std=4001.5ms
- CV (across sessions): mean=N/A, std=0.0000 -> stability=LOW
- Shannon entropy: mean=N/A bits (0 sessions)
- Hold duration: mean=0.0ms ± 0.0ms
- **Biometric viability: LOW** (freq=LOW, stab=LOW)

### Options
- Total presses: 3 across 40 sessions
- Sessions with >=10 presses: 0/40
- Press count per session: mean=0.1, std=0.3, min=0, max=1
- Inter-press interval: mean=0.0ms, std=0.0ms
- CV (across sessions): mean=N/A, std=0.0000 -> stability=LOW
- Shannon entropy: mean=N/A bits (0 sessions)
- Hold duration: mean=0.0ms ± 0.0ms
- **Biometric viability: LOW** (freq=LOW, stab=LOW)

### Create (Share)
- Total presses: 1 across 40 sessions
- Sessions with >=10 presses: 0/40
- Press count per session: mean=0.0, std=0.2, min=0, max=1
- Inter-press interval: mean=0.0ms, std=0.0ms
- CV (across sessions): mean=N/A, std=0.0000 -> stability=LOW
- Shannon entropy: mean=N/A bits (0 sessions)
- Hold duration: mean=0.0ms ± 0.0ms
- **Biometric viability: LOW** (freq=LOW, stab=LOW)

## 9. Methodology

- Sessions: 40 hardware sessions (`sessions/human/hw_*.json`) at ~1000 Hz USB polling
- Synthetic baseline sessions excluded (no button data)
- Minimum presses for CV/entropy: 10 per session
- Entropy bin width: 50ms (matches L5 TemporalRhythmOracle)
- Transition window: 100ms (A releases -> B presses within this)
- Mahalanobis: per-session [CV, Entropy] vector for top-4 buttons by press count; pinv covariance for numerical stability
- D-pad: hat-switch encoding (N=0,NE=1,E=2,SE=3,S=4,SW=5,W=6,NW=7,neutral=8)
  - Up: {0, 1, 7}, Down: {3, 4, 5}
  - Left: {5, 6, 7}, Right: {1, 2, 3}

---
*Generated by `scripts/analyze_button_fingerprint.py`*