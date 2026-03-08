# VAPI Inter-Person Biometric Separation Analysis

**Date:** 2026-03-08  
**Sessions:** N=69 captured, 64 included, 5 excluded (polling-rate filter)  
**Players:** 3 (Player 1: hw_005–hw_044, Player 2: hw_045–hw_058, Player 3: hw_059–hw_073)  
**Feature space:** 11-dimensional L4 biometric fingerprint  
**Window size:** 50 frames  
**Distance metric:** Full Mahalanobis (Tikhonov-regularized covariance)

## Executive Summary

| Metric | Value |
|--------|-------|
| Mean intra-player distance | 1.210 |
| Mean inter-player distance | 0.611 |
| **Separation ratio (inter/intra)** | **0.505** |
| Leave-one-out classification accuracy | 51.6% (33/64) |

**Conclusion:** NO SEPARATION — fingerprint does not distinguish between players

The 11-feature L4 fingerprint shows **weak or no inter-player separation** (ratio 0.50). This may reflect insufficient session diversity, feature space limitations (e.g., touchpad features all zero in current dataset), or genuine similarity of play styles across players. Intra-player consistency detection remains valid despite low inter-player separation.

## Per-Player Statistics

| Player   | Sessions | Intra Mean | Intra Std | Intra Min | Intra Max | Intra Median |
| -------- | -------- | ---------- | --------- | --------- | --------- | ------------ |
| Player 1 | 38       | 1.154      | 1.057     | 0.423     | 7.060     | 0.893        |
| Player 2 | 14       | 1.006      | 0.531     | 0.200     | 1.804     | 0.853        |
| Player 3 | 12       | 1.471      | 1.537     | 0.427     | 6.430     | 0.993        |

## Inter-Player Distance Matrix (Mahalanobis)

Distance between each pair of player mean feature vectors using the shared global covariance.

|          | Player 1 | Player 2 | Player 3 |
| -------- | -------- | -------- | -------- |
| Player 1 | —        | 0.363    | 0.871    |
| Player 2 | 0.363    | —        | 0.598    |
| Player 3 | 0.871    | 0.598    | —        |

## Intra-Player Distance Distribution

Mahalanobis distance from each session's mean feature vector to its player's centroid, using the global covariance.

**Player 1** (N=38 sessions, mean=1.154):
  7.060, 0.580, 0.851, 0.696, 0.513, 0.596, 1.866, 0.725, 0.835, 0.570, 0.759, 1.076, 0.752, 1.096, 0.926, 0.491, 1.623, 0.439, 1.181, 0.644, 0.620, 0.657, 1.255, 1.397, 1.720, 1.457, 1.650, 0.625, 0.958, 1.441, 1.589, 1.000, 1.399, 0.723, 0.423, 1.866, 0.869, 0.918

**Player 2** (N=14 sessions, mean=1.006):
  0.200, 1.804, 1.753, 1.786, 1.707, 0.631, 1.011, 0.712, 0.616, 0.734, 0.972, 0.473, 0.514, 1.175

**Player 3** (N=12 sessions, mean=1.471):
  6.430, 0.427, 1.281, 0.999, 0.769, 1.788, 0.988, 0.894, 1.232, 0.781, 1.437, 0.626

## Feature Means by Player

Per-feature mean values for each player's session set. Features with high inter-player variation are the strongest biometric discriminators.

| Feature                        | Player 1           | Player 2           | Player 3           | Inter-Range |
| ------------------------------ | ------------------ | ------------------ | ------------------ | ----------- |
| tremor_peak_hz                 | 0.4131 (+/-0.5300) | 0.4811 (+/-0.4332) | 0.8613 (+/-1.4717) | 0.4483      |
| grip_asymmetry                 | 1.0124 (+/-0.0325) | 1.0047 (+/-0.0042) | 1.0026 (+/-0.0020) | 0.0098      |
| stick_autocorr_lag1            | 0.0279 (+/-0.0135) | 0.0262 (+/-0.0147) | 0.0227 (+/-0.0134) | 0.0053      |
| stick_autocorr_lag5            | 0.0124 (+/-0.0061) | 0.0119 (+/-0.0076) | 0.0092 (+/-0.0056) | 0.0032      |
| trigger_onset_velocity_r2      | 0.0017 (+/-0.0012) | 0.0012 (+/-0.0008) | 0.0011 (+/-0.0006) | 0.0006      |
| trigger_onset_velocity_l2      | 0.0004 (+/-0.0004) | 0.0002 (+/-0.0002) | 0.0002 (+/-0.0002) | 0.0002      |
| trigger_resistance_change_rate | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000      |
| micro_tremor_accel_variance    | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000      |
| tremor_band_power              | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000      |
| touchpad_active_fraction       | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000      |
| touch_position_variance        | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000 (+/-0.0000) | 0.0000      |

## Leave-One-Out Classification Results

Each session was classified to the nearest player centroid (Mahalanobis) using the global covariance. Player mean vectors were computed from ALL sessions (no held-out centroid recomputation — this is a bias-aware first-pass estimate).

**Accuracy: 51.6% (33/64 sessions correctly assigned)**

Misclassified sessions:

| Session | True Player | Predicted | Best Dist |
| ------- | ----------- | --------- | --------- |
| hw_007  | Player 1    | Player 3  | 0.717     |
| hw_009  | Player 1    | Player 2  | 0.453     |
| hw_011  | Player 1    | Player 2  | 1.786     |
| hw_013  | Player 1    | Player 2  | 0.725     |
| hw_019  | Player 1    | Player 2  | 0.786     |
| hw_020  | Player 1    | Player 2  | 0.352     |
| hw_021  | Player 1    | Player 2  | 1.540     |
| hw_023  | Player 1    | Player 2  | 1.091     |
| hw_024  | Player 1    | Player 2  | 0.636     |
| hw_030  | Player 1    | Player 2  | 1.389     |
| hw_031  | Player 1    | Player 2  | 1.639     |
| hw_032  | Player 1    | Player 2  | 0.528     |
| hw_033  | Player 1    | Player 2  | 0.941     |
| hw_034  | Player 1    | Player 3  | 1.216     |
| hw_035  | Player 1    | Player 3  | 0.814     |
| hw_040  | Player 1    | Player 2  | 1.786     |
| hw_041  | Player 1    | Player 2  | 0.822     |
| hw_046  | Player 2    | Player 3  | 1.709     |
| hw_051  | Player 2    | Player 1  | 1.010     |
| hw_052  | Player 2    | Player 1  | 0.618     |
| hw_053  | Player 2    | Player 1  | 0.610     |
| hw_054  | Player 2    | Player 1  | 0.695     |
| hw_055  | Player 2    | Player 3  | 0.954     |
| hw_058  | Player 2    | Player 3  | 1.029     |
| hw_060  | Player 3    | Player 2  | 0.324     |
| hw_061  | Player 3    | Player 1  | 0.755     |
| hw_062  | Player 3    | Player 1  | 0.389     |
| hw_063  | Player 3    | Player 2  | 0.348     |
| hw_064  | Player 3    | Player 2  | 1.714     |
| hw_070  | Player 3    | Player 2  | 0.197     |
| hw_071  | Player 3    | Player 1  | 0.883     |

## Excluded Sessions

| Session | Reason                                       | Polling Rate Hz |
| ------- | -------------------------------------------- | --------------- |
| hw_043  | polling_rate_hz=203.6 outside [800.0,1100.0] | 203.55          |
| hw_044  | polling_rate_hz=492.7 outside [800.0,1100.0] | 492.67          |
| hw_067  | polling_rate_hz=72.2 outside [800.0,1100.0]  | 72.16           |
| hw_069  | polling_rate_hz=307.1 outside [800.0,1100.0] | 307.14          |
| hw_073  | polling_rate_hz=49.7 outside [800.0,1100.0]  | 49.7            |

## Recommendations for L4 Multi-Person Calibration

### Implications for VAPI Protocol

1. **Player-specific fingerprinting needs more features.** The current separation ratio of 0.50 suggests feature augmentation or longer session windows before per-player identification is reliable.

2. **Touchpad biometrics.** All 69 sessions show zero touchpad activity (touch_active=False throughout). Adding the `touch_active`/`touch0_x` fields from capture_session.py Phase 17 will add player-specific thumb-resting patterns as a discriminator. This is expected to improve separation significantly.

3. **Micro-tremor variance.** The gyro-based still-frame filter (gyro_mag < 0.01) applies to raw LSB gyro values (range ~-350 to +350). With raw IMU values in the hundreds, most frames fail this threshold — the effective still-frame count is low. Consider calibrating the threshold to `gyro_mag < IMU_NOISE_FLOOR` (empirical: 332.99 LSB, 95th pct) to capture more tremor frames.

4. **Multi-session calibration window.** The live L4 oracle uses EMA over sessions. For inter-player separation in tournament contexts, accumulate ≥10 sessions per player before computing player centroid. The current N=21 sessions/player average is adequate.

5. **Full covariance vs. diagonal.** This analysis uses a full Tikhonov-regularized covariance matrix (off-diagonal terms included). The live L4 oracle currently uses a diagonal approximation. Upgrading to full covariance (TODO in the source) would better capture feature correlations and improve both intra-player consistency detection and inter-player separation.

6. **Tremor FFT window length.** The 50-frame window used here (vs 120-frame in live oracle) at 1000 Hz gives a frequency resolution of 20 Hz/bin, which is too coarse to resolve the 8-12 Hz physiological tremor band. The live oracle uses 120-frame windows (8.3 Hz/bin). For reliable tremor band power, a 1024-frame window at 1000 Hz would give 0.98 Hz/bin resolution (noted in CLAUDE.md as a known gap).

---
*Generated by `scripts/analyze_interperson_separation.py` — VAPI Phase 17, 2026-03-08*