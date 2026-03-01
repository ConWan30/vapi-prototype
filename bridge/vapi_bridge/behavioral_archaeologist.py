"""
Phase 26 — BehavioralArchaeologist.

Longitudinal analysis of PITL session history to detect:
  - Warm-up attacks: bot gradually improves humanity_prob while drift rises
  - Burst farming: bursty PHG checkpoint accumulation via inter-checkpoint CV

Pure numpy. Read-only — never modifies records or scores.
"""

import math
import time
from dataclasses import dataclass, field

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

FEATURE_KEYS = [
    "trigger_resistance_change_rate",
    "trigger_onset_velocity_l2",
    "trigger_onset_velocity_r2",
    "micro_tremor_accel_variance",
    "grip_asymmetry",
    "stick_autocorr_lag1",
    "stick_autocorr_lag5",
]


@dataclass
class BehavioralReport:
    device_id: str
    drift_trend_slope: float        # positive = drift increasing (suspicious)
    humanity_trend_slope: float     # positive = humanity improving
    warmup_attack_score: float      # [0, 1]; HIGH = suspicious warm-up pattern
    burst_farming_score: float      # [0, 1]; HIGH = bursty PHG accumulation
    biometric_stability_cert: bool  # avg drift_velocity < 0.5 over last 20
    l4_consistency_cert: bool       # L4 distance std/mean < 0.3 over last 20
    session_count: int
    report_timestamp: float
    warning: str = ""


def _slope(arr) -> float:
    """Pure numpy centred linear regression slope: Σ(x_c·y_c) / Σ(x_c²).

    Uses record index as x-axis (invariant to adversarial timestamp manipulation).
    Returns 0.0 for arrays of length < 2.
    """
    if not HAS_NUMPY:
        return 0.0
    n = len(arr)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=np.float64)
    x_c = x - x.mean()
    y_c = arr - arr.mean()
    denom = np.dot(x_c, x_c)
    if denom == 0.0:
        return 0.0
    return float(np.dot(x_c, y_c) / denom)


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


class BehavioralArchaeologist:
    """Longitudinal PITL analysis for anomaly detection."""

    def __init__(self, store, window_records: int = 50):
        self._store = store
        self._window = window_records

    def analyze_device(self, device_id: str) -> BehavioralReport:
        """Produce a BehavioralReport for a single device."""
        history = self._store.get_pitl_history(device_id, limit=self._window)
        session_count = len(history)

        # Burst farming score depends on checkpoints, not PITL history — always compute
        checkpoints = self._store.get_phg_checkpoints(device_id, limit=20)
        burst_farming_score = self._compute_burst_score(checkpoints)

        if not HAS_NUMPY or session_count == 0:
            return BehavioralReport(
                device_id=device_id,
                drift_trend_slope=0.0,
                humanity_trend_slope=0.0,
                warmup_attack_score=_sigmoid(-3.0),   # neutral baseline
                burst_farming_score=burst_farming_score,
                biometric_stability_cert=False,
                l4_consistency_cert=False,
                session_count=session_count,
                report_timestamp=time.time(),
            )

        # Extract series (skip None) — history is DESC, reverse to chronological
        hist_asc = list(reversed(history))

        drift_vals = np.array(
            [r["pitl_l4_drift_velocity"] for r in hist_asc
             if r["pitl_l4_drift_velocity"] is not None],
            dtype=np.float64,
        )
        humanity_vals = np.array(
            [r["pitl_humanity_prob"] for r in hist_asc
             if r["pitl_humanity_prob"] is not None],
            dtype=np.float64,
        )
        l4_dist_vals = np.array(
            [r["pitl_l4_distance"] for r in hist_asc
             if r["pitl_l4_distance"] is not None],
            dtype=np.float64,
        )

        drift_slope = _slope(drift_vals) if len(drift_vals) >= 2 else 0.0
        humanity_slope = _slope(humanity_vals) if len(humanity_vals) >= 2 else 0.0

        # Warmup attack score — both drift AND humanity trending up = suspicious
        # Scale: slope=0.01 for each → warmup_raw=2.0 → sigmoid(1.0)≈0.73 (>0.7 threshold)
        if humanity_slope > 0.002 and drift_slope > 0.002:
            # 20000: scaling factor for the warmup sigmoid formula.
            # Each slope is typically a tiny value in the range [0.001, 0.01] per session.
            # The product of two such slopes is therefore in the range [1e-6, 1e-4].
            # Multiplying by 20000 scales this into a range where sigmoid(raw - 1.0)
            # produces meaningful [0, 1] separation between suspicious and benign devices:
            #   - Slopes (0.01, 0.01): raw = 0.01 * 0.01 * 20000 = 2.0
            #                          sigmoid(2.0 - 1.0) = sigma(1.0) ~= 0.73 (above 0.7 threshold)
            #   - Slopes (0.001, 0.001): raw = 0.001 * 0.001 * 20000 = 0.002
            #                            sigmoid(0.002 - 1.0) = sigma(-0.998) ~= 0.27 (below threshold)
            # This value is derived from synthetic test patterns and has NOT been empirically
            # calibrated against real controller session data.
            # TODO: Calibrate 20000 against real-hardware session data (target: F1 > 0.85 at
            # warmup attack detection on 500+ labeled sessions). Consider replacing the product
            # formula with a geometric mean or log-domain computation for better numerical
            # stability across a wider range of slope magnitudes.
            warmup_raw = drift_slope * humanity_slope * 20000
        else:
            warmup_raw = 0.0
        warmup_attack_score = _sigmoid(warmup_raw - 1.0)

        # Biometric stability certificate
        biometric_stability_cert = (
            len(drift_vals) >= 5 and float(np.mean(drift_vals[:20])) < 0.5
        )

        # L4 consistency certificate
        if len(l4_dist_vals) >= 5:
            mean_l4 = float(np.mean(l4_dist_vals[:20]))
            std_l4 = float(np.std(l4_dist_vals[:20]))
            l4_consistency_cert = (std_l4 / (mean_l4 + 1e-6)) < 0.3
        else:
            l4_consistency_cert = False

        warning = ""
        if warmup_attack_score > 0.7:
            warning = "WARMUP_ATTACK_SUSPECTED"
        elif burst_farming_score > 0.7:
            warning = "BURST_FARMING_SUSPECTED"

        return BehavioralReport(
            device_id=device_id,
            drift_trend_slope=round(drift_slope, 6),
            humanity_trend_slope=round(humanity_slope, 6),
            warmup_attack_score=round(warmup_attack_score, 4),
            burst_farming_score=round(burst_farming_score, 4),
            biometric_stability_cert=biometric_stability_cert,
            l4_consistency_cert=l4_consistency_cert,
            session_count=session_count,
            report_timestamp=time.time(),
            warning=warning,
        )

    def _compute_burst_score(self, checkpoints: list) -> float:
        """Inter-checkpoint time CV → burst farming score ∈ [0, 1]."""
        if len(checkpoints) < 2:
            return 0.0
        if not HAS_NUMPY:
            return 0.0
        times = sorted(c["committed_at"] for c in checkpoints)
        gaps = np.diff(np.array(times, dtype=np.float64))
        mean_gap = float(np.mean(gaps))
        std_gap = float(np.std(gaps))
        if mean_gap < 1e-6:
            return 0.0
        cv = std_gap / mean_gap
        # cv / 2.0: maps the coefficient of variation (CV) of inter-checkpoint time gaps
        # to a burst farming suspicion score in [0, 1].
        #
        # CV = std(gaps) / mean(gaps) measures relative variability of checkpoint timing:
        #   - Pure macro/script: gaps are highly regular → CV ~= 0 → score ~= 0.0
        #   - Burst farming: gaps cluster (many rapid checkpoints, then long pauses)
        #                    → high variance relative to mean → CV ~= 1-2 → score = 0.5-1.0
        # Dividing by 2.0 maps CV=1.0 (meaningfully bursty) to score=0.5 and CV=2.0 to
        # the capped maximum of 1.0. The divisor 2.0 is empirically arbitrary — it was
        # chosen to give a mid-range score for moderately bursty patterns without
        # immediately saturating at 1.0.
        # TODO: Calibrate the divisor (currently 2.0) against real-hardware checkpoint
        # data from human players vs. known burst-farming bots. The CV distribution for
        # human players on real hardware is unknown. A lower divisor (e.g., 1.5) would
        # make the detector more sensitive; a higher divisor (e.g., 3.0) more conservative.
        return min(1.0, cv / 2.0)

    def get_population_report(self) -> list[BehavioralReport]:
        """Analyze all fingerprinted devices."""
        devices = self._store.get_all_fingerprinted_devices()
        return [self.analyze_device(d) for d in devices]

    def get_high_risk_devices(self, threshold: float = 0.7) -> list[str]:
        """Return device IDs with warmup_attack_score OR burst_farming_score above threshold."""
        return [
            r.device_id for r in self.get_population_report()
            if r.warmup_attack_score > threshold or r.burst_farming_score > threshold
        ]
