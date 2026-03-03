# VAPI Adversarial Validation Results

**Generated:** 2026-03-02  
**Calibration:** N=50 DualShock Edge sessions, high confidence  
**Human sessions:** 50 (real hw_* + synthetic baselines)  
**Adversarial sessions:** 57 (6 attack types, real-data transforms)  
**Detection (excl. replay):** 39/52 (75.0%)  
**False positive rate:** 1/50 (2.0%)

## Method

Each adversarial session is a deterministic, reproducible transformation of a real 1000 Hz DualShock Edge capture. A reviewer can inspect exactly what was done to each session. Transforms target the specific physical properties each PITL layer relies on.

```
========================================================================
VAPI PITL Adversarial Validation Report
N=50 hardware calibration  |  DualShock Edge CFI-ZCP1  |  1000 Hz
========================================================================

ADVERSARIAL SESSIONS  (detection rate — higher is better)
------------------------------------------------------------------------
Attack Type                    N   L2 Det%  L4 Det%  L5 Det%    Any%  Notes
------------------------------------------------------------------------
Replay (chain-level)           5      0.0%    20.0%     0.0%   20.0%  chain-level attack; 0% PITL is expected/correct
IMU-stripped injection        10     80.0%     0.0%     0.0%   80.0%  
Perfect-timing macro          10     50.0%    30.0%   100.0%  100.0%  
Biometric transplant           5      0.0%     0.0%     0.0%    0.0%  L4 fired 0/5 (single-person dataset limits sensitivity)
Gradual warmup (E)            10     20.0%     0.0%    60.0%   60.0%  sessions 1-3 bot_score mean=0.914
Quant-masked bot              15     66.7%     0.0%   100.0%  100.0%  mean quant_score=0.898
unknown                        1      0.0%     0.0%     0.0%    0.0%  (unlabelled)
stationary                     1      0.0%     0.0%     0.0%    0.0%  (unlabelled)

HUMAN SESSIONS  (false positive rate — lower is better)
------------------------------------------------------------------------
Session Type                   N    L2 FP%   L4 FP%   L5 FP%  Any FP%
------------------------------------------------------------------------
Real hardware (hw_*)          40      0.0%     2.5%     0.0%     2.5%
Synthetic baseline            10      0.0%     0.0%     0.0%     0.0%
All human sessions            50      0.0%     2.0%     0.0%     2.0%

WARMUP SEQUENCE  (bot->human progression, Attack E)
------------------------------------------------------------------------
Session       Alpha Label             L2    L4    L5  BotScore      CV  Entropy  GyroStd
------------------------------------------------------------------------
warmup_001    0.00 bot_like        FIRE     -  FIRE    0.9390  0.0054   0.2192     6.08
warmup_002    0.11 bot_like        FIRE     -  FIRE    0.9152  0.0054   0.2192    14.88
warmup_003    0.22 bot_like           -     -  FIRE    0.8865  0.0054   0.2192    28.38
warmup_004    0.33 transitional       -     -  FIRE    0.8582  0.0054   0.2192    42.39
warmup_005    0.44 transitional       -     -  FIRE    0.5466  1.3713   0.0313    45.60
warmup_006    0.56 transitional       -     -  FIRE    0.5115  1.3645   0.0605    57.08
warmup_007    0.67 transitional       -     -     -    0.4978  0.0000   0.0000    68.54
warmup_008    0.78 near_human         -     -     -    0.4697  0.0000   0.0000    80.03
warmup_009    0.89 near_human         -     -     -    0.4418  0.0000   0.0000    91.52
warmup_010    1.00 near_human         -     -     -    0.4138  0.0000   0.0000   103.31

L5 HUMAN BASELINE STATISTICS
------------------------------------------------------------------------
  L5 CV       — mean: 1.1662  min: 0.6649  max: 2.5122  (threshold < 0.08)
  L5 Entropy  — mean: 4.5616  min: 3.0332  max: 5.3141  (threshold < 1.0 bits)
  L5 Quant    — mean: 0.5902  min: 0.4231  max: 0.7500  (threshold > 0.55)

CALIBRATED THRESHOLDS  (N=50, high confidence, 2026-03-02)
------------------------------------------------------------------------
  L2 injection gyro std < 20.0 LSB with active input
  L4 Mahalanobis distance > 5.869 (mean + 3-sigma, N=50)
  L5 CV < 0.08 | entropy < 1.0 bits | quant > 0.55  (need >=2/3)

```

## Attack Descriptions

| Attack | Type | Detection Layer | Transform |
|--------|------|-----------------|----------|
| Replay | A | Chain (not PITL) | Timestamps +3600 s; identical biometrics |
| IMU-stripped injection | B | L2+L4 | All gyro/accel fields zeroed |
| Perfect-timing macro | C | L5 | R2 presses at constant 50 ms intervals |
| Biometric transplant | D | L4 | Stick+IMU from session X; trigger timing from Y |
| Gradual warmup | E | L2+L4+L5 | Linear bot→human interpolation across 10 sessions |
| Quant-masked bot | F | L5 | 60 Hz-locked presses + 2 ms Gaussian jitter |

## Threshold Reference

| Layer | Threshold | N=50 Human Baseline |
|-------|-----------|--------------------|
| L2 injection | gyro_std < 20.0 LSB | ~320 LSB (16x margin) |
| L4 Mahalanobis | distance > 5.869 | mean+3sigma |
| L5 CV | < 0.08 | human ~0.34 (4.3x margin) |
| L5 Entropy | < 1.0 bits | human ~1.38 bits |
| L5 Quant | > 0.55 | human ~0.35 |
