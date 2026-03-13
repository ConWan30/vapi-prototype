# Phase 45 — accel_phase_coherence Calibration Report

**Generated from N=68 sessions** (after Phase 43 exclusions).
Excluded: hw_043, hw_044, hw_059, hw_067, hw_069, hw_073.
Window size: 1024 frames per vector.

---

## 1. Coherence Distribution

accel_phase_coherence range: [-1, 1].  Expected: human rigid grip ~0.6–0.9; noise injection ~0.0 ± 0.1.

| Player | N | min | p10 | median | mean | p90 | max | std |
|--------|---|-----|-----|--------|------|-----|-----|-----|
| P1 | 38 | -0.863 | -0.435 | -0.068 | -0.069 | 0.192 | 0.998 | 0.298 |
| P2 | 14 | -0.492 | -0.403 | -0.157 | -0.128 | 0.176 | 0.560 | 0.284 |
| P3 | 11 | -0.321 | -0.185 | 0.008 | 0.040 | 0.263 | 0.486 | 0.212 |
| **ALL** | **68** | -0.863 | -0.412 | **-0.058** | -0.011 | 0.411 | 1.000 | 0.383 |

---

## 2. L4 Mahalanobis Thresholds

| | Old (Phase 43) | New (Phase 45) | Delta |
|-|----------------|----------------|-------|
| Anomaly threshold (mean+3s) | 7.019 | 4.434 | -2.585 |
| Continuity threshold (mean+2s) | 5.369 | 3.483 | -1.886 |
| Population Mahal mean | — | 1.583 | — |
| Population Mahal std | — | 0.950 | — |
| Active feature count | 8 (3 zero-var slots) | 6 | — |

**Note:** accel_phase_coherence is at index 9. If it is now non-zero-variance,
the active count increases from 8 → 6. The threshold change
reflects the updated population distribution with this slot active.

---

## 3. Inter-Person Separation Ratio

| | Old (Phase 43) | New (Phase 45) |
|-|----------------|----------------|
| Separation ratio | 0.362 | 0.305 |
| Target for reliable identification | >2.0 | >2.0 |

**Unchanged or regressed**: ratio moved from 0.362 → 0.305.
accel_phase_coherence activates a previously dead feature slot but separation improvement is bounded by the fundamental P1/P2 similarity — they share the same tremor oscillator topology. Primary fix path remains post-Phase-17 touchpad recapture.

---

## 4. Dashboard RADAR_DATA Score

Recommended score for Phase Coherence radar entry: **-6**
(= population median -0.058 × 100, rounded).

---

## 5. Action Items

- [ ] Review threshold delta — if within ±5% of 7.019/5.369, keep current constants
- [ ] If separation ratio improved beyond 1.0, update whitepaper §8.6 note
- [ ] Update RADAR_DATA score to -6 in VAPIDashboard.jsx
- [ ] Confirm: after approval, update ANOMALY_THRESHOLD and CONTINUITY_THRESHOLD in source