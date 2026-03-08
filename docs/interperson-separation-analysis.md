# VAPI Inter-Person Biometric Separation Analysis

**Date:** 2026-03-08  
**Sessions:** N=69 captured, 64 included, 5 excluded (polling-rate filter)  
**Players:** 3 (Player 1: hw_005–hw_044, Player 2: hw_045–hw_058, Player 3: hw_059–hw_073)  
**Feature space:** 11-dimensional L4 biometric fingerprint (8 active after zero-variance exclusion)  
**Window size:** 1024 frames  
**Distance metric:** Full Mahalanobis on active features (Tikhonov-regularized covariance)

> **Auto-excluded features (zero variance across all sessions):** `trigger_resistance_change_rate`, `touchpad_active_fraction`, `touch_position_variance`  
> These features are structurally zero in the current N=69 corpus (game-specific or hardware field added after capture). They are reported below but excluded from Mahalanobis computation.

## Executive Summary

| Metric | Value |
|--------|-------|
| Mean intra-player distance | 0.635 |
| Mean inter-player distance | 0.230 |
| **Separation ratio (inter/intra)** | **0.362** |
| Leave-one-out classification accuracy | 42.2% (27/64) |

**Conclusion:** NO SEPARATION — fingerprint does not distinguish between players

The 11-feature L4 fingerprint shows **weak or no inter-player separation** (ratio 0.36). This may reflect insufficient session diversity, feature space limitations (e.g., touchpad features all zero in current dataset), or genuine similarity of play styles across players. Intra-player consistency detection remains valid despite low inter-player separation.

## Per-Player Statistics

| Player   | Sessions | Intra Mean | Intra Std | Intra Min | Intra Max | Intra Median |
| -------- | -------- | ---------- | --------- | --------- | --------- | ------------ |
| Player 1 | 38       | 0.716      | 0.830     | 0.006     | 4.290     | 0.412        |
| Player 2 | 14       | 0.661      | 0.658     | 0.094     | 2.636     | 0.478        |
| Player 3 | 12       | 0.527      | 0.522     | 0.018     | 2.048     | 0.362        |

## Inter-Player Distance Matrix (Mahalanobis)

Distance between each pair of player mean feature vectors using the shared global covariance.

|          | Player 1 | Player 2 | Player 3 |
| -------- | -------- | -------- | -------- |
| Player 1 | —        | 0.051    | 0.343    |
| Player 2 | 0.051    | —        | 0.295    |
| Player 3 | 0.343    | 0.295    | —        |

## Intra-Player Distance Distribution

Mahalanobis distance from each session's mean feature vector to its player's centroid, using the global covariance.

**Player 1** (N=38 sessions, mean=0.716):
  1.834, 0.464, 0.193, 4.290, 0.051, 0.073, 1.644, 0.406, 0.809, 0.163, 0.006, 0.604, 0.057, 0.276, 0.267, 0.479, 1.732, 1.989, 0.662, 0.019, 0.605, 0.255, 0.328, 0.418, 0.886, 0.153, 1.026, 0.127, 1.746, 0.032, 0.311, 1.286, 0.141, 1.865, 0.379, 0.620, 0.615, 0.403

**Player 2** (N=14 sessions, mean=0.661):
  0.847, 0.442, 0.814, 1.471, 0.094, 0.546, 0.117, 2.636, 0.515, 0.143, 0.157, 0.769, 0.280, 0.420

**Player 3** (N=12 sessions, mean=0.527):
  2.048, 0.178, 0.744, 0.275, 0.835, 0.166, 0.018, 0.329, 0.442, 0.161, 0.394, 0.734

## Feature Means by Player

Per-feature mean values for each player's session set. Features with high inter-player variation are the strongest biometric discriminators.

| Feature                        | Player 1                 | Player 2                 | Player 3                 | Inter-Range |
| ------------------------------ | ------------------------ | ------------------------ | ------------------------ | ----------- |
| micro_tremor_accel_variance    | 8766.9396 (+/-5160.8163) | 8526.8386 (+/-4386.2235) | 7276.6082 (+/-2846.5756) | 1490.3314   |
| tremor_peak_hz                 | 0.7075 (+/-1.1032)       | 1.0174 (+/-1.1592)       | 7.7951 (+/-21.7266)      | 7.0876      |
| stick_autocorr_lag5            | 0.1316 (+/-0.0583)       | 0.1144 (+/-0.0626)       | 0.0983 (+/-0.0559)       | 0.0332      |
| stick_autocorr_lag1            | 0.1464 (+/-0.0646)       | 0.1303 (+/-0.0721)       | 0.1145 (+/-0.0650)       | 0.0320      |
| grip_asymmetry                 | 1.0407 (+/-0.0613)       | 1.0330 (+/-0.0289)       | 1.0249 (+/-0.0293)       | 0.0157      |
| trigger_onset_velocity_r2      | 0.0041 (+/-0.0046)       | 0.0028 (+/-0.0021)       | 0.0027 (+/-0.0021)       | 0.0014      |
| tremor_band_power              | 0.0037 (+/-0.0032)       | 0.0045 (+/-0.0030)       | 0.0040 (+/-0.0024)       | 0.0008      |
| trigger_onset_velocity_l2      | 0.0010 (+/-0.0010)       | 0.0009 (+/-0.0008)       | 0.0009 (+/-0.0006)       | 0.0002      |
| trigger_resistance_change_rate | 0.0000 (+/-0.0000)       | 0.0000 (+/-0.0000)       | 0.0000 (+/-0.0000)       | 0.0000      |
| touchpad_active_fraction       | 0.0000 (+/-0.0000)       | 0.0000 (+/-0.0000)       | 0.0000 (+/-0.0000)       | 0.0000      |
| touch_position_variance        | 0.0000 (+/-0.0000)       | 0.0000 (+/-0.0000)       | 0.0000 (+/-0.0000)       | 0.0000      |

## Leave-One-Out Classification Results

Each session was classified to the nearest player centroid (Mahalanobis) using the global covariance. Player mean vectors were computed from ALL sessions (no held-out centroid recomputation — this is a bias-aware first-pass estimate).

**Accuracy: 42.2% (27/64 sessions correctly assigned)**

Misclassified sessions:

| Session | True Player | Predicted | Best Dist |
| ------- | ----------- | --------- | --------- |
| hw_005  | Player 1    | Player 3  | 1.530     |
| hw_006  | Player 1    | Player 3  | 0.171     |
| hw_007  | Player 1    | Player 2  | 0.143     |
| hw_010  | Player 1    | Player 2  | 0.026     |
| hw_011  | Player 1    | Player 3  | 1.344     |
| hw_018  | Player 1    | Player 3  | 0.097     |
| hw_019  | Player 1    | Player 3  | 0.157     |
| hw_021  | Player 1    | Player 3  | 1.432     |
| hw_023  | Player 1    | Player 3  | 0.386     |
| hw_027  | Player 1    | Player 3  | 0.159     |
| hw_028  | Player 1    | Player 3  | 0.189     |
| hw_029  | Player 1    | Player 3  | 0.598     |
| hw_031  | Player 1    | Player 3  | 0.720     |
| hw_034  | Player 1    | Player 2  | 0.026     |
| hw_035  | Player 1    | Player 3  | 0.156     |
| hw_036  | Player 1    | Player 3  | 0.988     |
| hw_037  | Player 1    | Player 2  | 0.091     |
| hw_039  | Player 1    | Player 3  | 0.174     |
| hw_040  | Player 1    | Player 3  | 0.350     |
| hw_041  | Player 1    | Player 3  | 0.319     |
| hw_042  | Player 1    | Player 3  | 0.186     |
| hw_045  | Player 2    | Player 1  | 0.796     |
| hw_046  | Player 2    | Player 1  | 0.393     |
| hw_047  | Player 2    | Player 3  | 0.563     |
| hw_048  | Player 2    | Player 3  | 1.223     |
| hw_050  | Player 2    | Player 3  | 0.327     |
| hw_052  | Player 2    | Player 1  | 2.585     |
| hw_053  | Player 2    | Player 3  | 0.300     |
| hw_056  | Player 2    | Player 3  | 0.534     |
| hw_057  | Player 2    | Player 1  | 0.229     |
| hw_058  | Player 2    | Player 1  | 0.370     |
| hw_060  | Player 3    | Player 2  | 0.163     |
| hw_061  | Player 3    | Player 1  | 0.418     |
| hw_062  | Player 3    | Player 2  | 0.038     |
| hw_063  | Player 3    | Player 1  | 0.511     |
| hw_066  | Player 3    | Player 1  | 0.030     |
| hw_071  | Player 3    | Player 1  | 0.053     |

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

1. **Player-specific fingerprinting needs more features.** The current separation ratio of 0.36 suggests feature augmentation or longer session windows before per-player identification is reliable.

2. **Touchpad biometrics.** All 69 sessions show zero touchpad activity (touch_active=False throughout). Adding the `touch_active`/`touch0_x` fields from capture_session.py Phase 17 will add player-specific thumb-resting patterns as a discriminator. This is expected to improve separation significantly.

3. **Micro-tremor variance.** The gyro-based still-frame filter (gyro_mag < 0.01) applies to raw LSB gyro values (range ~-350 to +350). With raw IMU values in the hundreds, most frames fail this threshold — the effective still-frame count is low. Consider calibrating the threshold to `gyro_mag < IMU_NOISE_FLOOR` (empirical: 332.99 LSB, 95th pct) to capture more tremor frames.

4. **Multi-session calibration window.** The live L4 oracle uses EMA over sessions. For inter-player separation in tournament contexts, accumulate ≥10 sessions per player before computing player centroid. The current N=21 sessions/player average is adequate.

5. **Full covariance vs. diagonal.** This analysis uses a full Tikhonov-regularized covariance matrix (off-diagonal terms included). The live L4 oracle currently uses a diagonal approximation. Upgrading to full covariance (TODO in the source) would better capture feature correlations and improve both intra-player consistency detection and inter-player separation.

6. **Tremor FFT window length.** The 50-frame window used here (vs 120-frame in live oracle) at 1000 Hz gives a frequency resolution of 20 Hz/bin, which is too coarse to resolve the 8-12 Hz physiological tremor band. The live oracle uses 120-frame windows (8.3 Hz/bin). For reliable tremor band power, a 1024-frame window at 1000 Hz would give 0.98 Hz/bin resolution (noted in CLAUDE.md as a known gap).

---
*Generated by `scripts/analyze_interperson_separation.py` — VAPI Phase 17, 2026-03-08*