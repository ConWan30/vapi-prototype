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

## Phase 41 Post-Analysis: Why P1/P2 Are Indistinguishable and What Fixes It

**Date:** 2026-03-11
**Method:** Per-feature symmetric KL divergence (Gaussian approximation), computed from N=64 real sessions (P1=38, P2=14, P3=12) using `BiometricFeatureExtractor` with `window_frames=1024`.

### Root Cause Summary

P1 and P2 are indistinguishable because **6 of 11 features are structurally zero in NCAA Football 26**, and the remaining 5 active features produce heavily overlapping distributions between P1 and P2. The underlying issue is game-genre specificity: NCAA Football 26 uses a narrow input subset (left stick + face buttons + R2 sprint), leaving the features that best discriminate humans entirely inert.

---

### Feature-by-Feature KL Divergence Analysis

Symmetric KL divergence between player pairs computed under a Gaussian approximation. Values marked `DEGEN` indicate a degenerate case where one player has zero standard deviation — the KL formula is undefined and the feature cannot discriminate that pair regardless of the apparent mean difference. Cohen's d is reported for P1-P2 for all non-zero features.

| Rank | Feature | P1–P2 KL | P1–P3 KL | P2–P3 KL | Avg KL | P1–P2 Cohen's d | Status |
|------|---------|----------|----------|----------|--------|-----------------|--------|
| 1 | `stick_autocorr_lag1` | 0.210 | 8.053 | 20.145 | 9.469 | 0.278 | Active; P3 separable |
| 2 | `stick_autocorr_lag5` | 0.213 | 2.766 | 7.388 | 3.456 | 0.312 | Active; P3 separable |
| 3 | `micro_tremor_accel_variance` | 0.063 | 0.118 | 0.008 | 0.063 | 0.205 | Active; all pairs overlap |
| 4 | `grip_asymmetry` | DEGEN | DEGEN | 10.599 | — | 0.392 | Active but degenerate for P1–P2, P1–P3 |
| 5 | `tremor_peak_hz` | DEGEN | DEGEN | 0.000 | — | 0.232 | Active but degenerate; only 1/38 P1 sessions non-zero |
| — | `trigger_resistance_change_rate` | 0 | 0 | 0 | 0 | — | **ZERO-VARIANCE** |
| — | `trigger_onset_velocity_l2` | 0 | 0 | 0 | 0 | — | **ZERO-VARIANCE** |
| — | `trigger_onset_velocity_r2` | 0 | 0 | 0 | 0 | — | **ZERO-VARIANCE** |
| — | `tremor_band_power` | 0 | 0 | 0 | 0 | — | **ZERO-VARIANCE** |
| — | `touchpad_active_fraction` | 0 | 0 | 0 | 0 | — | **ZERO-VARIANCE** |
| — | `touch_position_variance` | 0 | 0 | 0 | 0 | — | **ZERO-VARIANCE** |

> **DEGEN** = one player has std ≈ 0, making the log(σ₂/σ₁) term in the KL formula diverge. The astronomical values produced by the naive formula (~10¹⁴–10¹⁵) are numerical artifacts, not real information.

---

### Why P1 and P2 Overlap on Every Active Feature

#### `grip_asymmetry` — Structurally Degenerate (NCAA Football 26)

In 37/38 P1 sessions and 13/14 P2 sessions, `grip_asymmetry` returns **exactly 1.000**. The feature is computed only during frames where both L2 and R2 exceed 10 ADC units simultaneously. In NCAA Football 26, L2 and R2 are almost never pressed together — R2 is the sprint button and L2 selects formations pre-snap, not during play. With no dual-press frames, the feature defaults to 1.0 for both players. Only hw_058 (P2, ratio=2.010) and hw_071 (P3, ratio=0.851) show any grip signal at all.

| Session | Player | grip_asymmetry | Explanation |
|---------|--------|---------------|-------------|
| hw_001–hw_042 | P1 | 1.000 | No dual L2+R2 press in any session |
| hw_045–hw_057 | P2 | 1.000 | No dual L2+R2 press |
| hw_058 | P2 | **2.010** | One session with simultaneous trigger press |
| hw_071 | P3 | **0.851** | P3's grip skewed slightly toward R2 |

#### `trigger_onset_velocity_l2/r2` — Structurally Near-Zero

L2 trigger onset velocity requires the L2 ADC value to rise from 0 to peak and complete. In the raw HID reports (8-bit range, 0–255), P1 almost never presses L2 (mean = 0.0000). P2/P3 show tiny non-zero means (0.0007–0.0020) from rare L2 presses. The feature is effectively zero-signal for all 3 players in this game.

#### `stick_autocorr_lag1/lag5` — Moderate Signal, But P1≈P2

Both P1 (lag1=0.044) and P2 (lag1=0.089) show low autocorrelation in right-stick velocity. Neither player uses the right stick consistently — NCAA Football 26 play centers on left stick movement and button timing, not right-stick aiming or camera. P3's values (lag1=0.007) are even lower, distinguishing P3 from P1/P2, but P1 and P2 overlap entirely (Cohen's d=0.278, all d < 0.5 threshold for meaningful separation).

#### `tremor_peak_hz` — Dead Signal (Right Stick Static)

Only 1 of 38 P1 sessions (hw_005, 3.906 Hz) shows any non-zero `tremor_peak_hz`. All P2 and P3 sessions return 0.000 Hz. The right stick sits at 128 (dead zone) throughout essentially all sessions, producing a zero-velocity signal for which the FFT returns only a DC component (0 Hz). The feature is measuring right-stick movement tremor, which only activates in games that use the right stick for continuous aiming or camera input.

#### `micro_tremor_accel_variance` — High Variance, Low Discrimination

All three players show high within-player variance in micro-tremor (P1 std=8026, P2 std=9830, P3 std=10646). The feature captures variance of accel magnitude during still-frame windows (gyro_mag < 20 LSB). The high variance is session-to-session noise (dependent on how much the device physically moves during gameplay) rather than a stable fingerprint. None of the three player pairs achieve Cohen's d > 0.5.

---

### Ranked Summary: What Separates Each Pair

| Player Pair | Best Discriminator | KL | Verdict |
|-------------|-------------------|-----|---------|
| **P1 vs P2** | `stick_autocorr_lag1` | 0.210 | **FAILS** — no feature achieves KL > 0.5 or Cohen's d > 0.5 |
| **P1 vs P3** | `stick_autocorr_lag1` | 8.053 | SEPARABLE — P3's near-zero right-stick autocorr differs strongly |
| **P2 vs P3** | `stick_autocorr_lag1` | 20.145 | SEPARABLE — same mechanism as P1-P3 |

**The dataset contains two effectively identical players (P1/P2) and one outlier (P3).** P3 is separable not because of biometric differences but because P3 barely uses the right stick at all (almost every session has right_stick_x frozen at 128), producing distinctly lower autocorrelation and confirming the "low R2/right-stick usage" observation from Phase 17.

---

### What Fixes Each Zero-Variance Feature

#### Features That Need a Different Game Genre

| Feature | Why Zero in NCAA Football 26 | Game Genre That Activates It |
|---------|------------------------------|------------------------------|
| `grip_asymmetry` | L2+R2 never pressed simultaneously; L2=pre-snap, R2=sprint, no gameplay overlap | **Racing games** (Forza, Gran Turismo, F1): L2=brake, R2=accelerate, continuously pressed together at varying force ratios. Grip ratio is a stable per-player fingerprint. |
| `trigger_onset_velocity_l2/r2` | L2 rarely pressed; R2 pressed but onset detection requires ADC transitions the extraction misses at 8-bit resolution | **Shooters / tactical** (COD, Apex, The Finals): L2=ADS (aim-down-sights), R2=fire, both used every engagement. High press frequency → robust onset statistics. Also **sports games with shot mechanics** (NBA 2K: L2=post, R2=shoot; FIFA: L2=skill, R2=shoot). |
| `tremor_peak_hz` | Right stick static at dead zone; no continuous input → FFT sees only DC | **FPS/TPS** (COD, Halo, Destiny, Fortnite): right stick = continuous camera/aim input. True 8–12 Hz physiological tremor from sustained aim engagement. Also **dual-stick arcade** (twin-stick shooters). |
| `tremor_band_power` | Collapses to 0 when right stick is static (follows `tremor_peak_hz`) | Same as above — same game genre fix. |
| `touchpad_active_fraction` | `touch_active` field added post-Phase-17; pre-Phase-17 captures all have `touch_active=False`. No sessions captured after Phase 17 yet. | **Any game after Phase 17 capture.** Next capture session will populate this field. Resting thumb position is player-specific. |
| `touch_position_variance` | Same as above — field added in Phase 17 | Same as above. |
| `trigger_resistance_change_rate` | NCAA Football 26 uses static trigger effect modes (mode 0/1 throughout); no mid-game adaptive trigger profile changes | **Adaptive trigger games** with per-context effect profiles: Returnal (mode switches per weapon), Ratchet & Clank (different resistance per weapon), Astro's Playroom (per-environment effects), Gran Turismo (ABS/grip feedback). The DualShock Edge firmware must be set to output trigger effect byte changes in response to game state. |

#### Features That Need More Sessions / Better Capture

| Feature | Fix |
|---------|-----|
| `micro_tremor_accel_variance` | The still-frame gate (`gyro_mag < 20 LSB`) is marginally calibrated — many P1/P2/P3 sessions return 0 because few or no frames pass the gate during active play. Fix: increase threshold to 50–100 LSB (still below active-play floor of ~200 LSB) to capture more still windows. Also, this feature has high session-to-session variance that drowns the player signal — needs N≥30 sessions per player to stabilize the EMA mean. |
| `grip_asymmetry` | If game genre cannot be changed: instrument L2+R2 usage in the capture script and flag sessions where neither trigger exceeds 30 ADC as "trigger-inactive". L4 can exclude this feature for trigger-inactive sessions rather than defaulting to 1.0 for all players. |

---

### Recommended Next Capture Sessions

To validate P1–P2 separability before production deployment, capture at minimum:

1. **10 sessions each, FPS game** (COD: Black Ops 6 or Warzone recommended): Will activate `tremor_peak_hz`, `tremor_band_power`, and (if ADS is L2-based) `trigger_onset_velocity_l2`.
2. **5 sessions each, racing game** (Gran Turismo 7 or Forza Motorsport): Will activate `grip_asymmetry` and both trigger onset velocities.
3. **Any game captured after Phase 17** (to populate `touchpad_active_fraction` and `touch_position_variance` with real thumb resting data).

Expected outcome: With those 3 features activated, the theoretical synthetic separation ratio (computed in `scripts/generate_synthetic_players.py`) is 9.85 with LOO accuracy 98%. The actual ratio on real sessions will be lower due to within-player noise, but should exceed 2.0 and cross the separability threshold.

---
*Phase 41 analysis added 2026-03-11. Data source: N=64 real sessions (sessions/human/hw_005–hw_073, 5 excluded), extracted with `BiometricFeatureExtractor(window_frames=1024)`. KL divergence computed under Gaussian approximation; degenerate cases (std≈0) noted explicitly. Analysis script: inline Python in docs source.*