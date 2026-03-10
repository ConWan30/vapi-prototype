"""
threshold_calibrator.py — Empirical PITL threshold calibration tool.

Takes one or more session files produced by scripts/capture_session.py and
computes recommended threshold values for the PITL detection stack.

WARNING: Minimum N=10 sessions is required for reliable thresholds.
Fewer than 10 sessions will produce a warning and wide confidence intervals.
Production thresholds require N>=50 sessions across multiple sessions and days.

Computed thresholds
-------------------
L4 Mahalanobis anomaly threshold   (currently hardcoded 3.0 in BiometricFusionClassifier)
L4 continuity threshold             (currently hardcoded 2.0)
L5 CV threshold                     (currently hardcoded 0.08)
L5 entropy threshold                (currently hardcoded 1.5 bits)
Stick noise floor                   (currently hardcoded 5 LSB std in hardware tests)

Output: calibration_profile.json

Usage:
    python scripts/threshold_calibrator.py sessions/session_001.json
    python scripts/threshold_calibrator.py sessions/*.json
    python scripts/threshold_calibrator.py sessions/*.json --output calibration_profile.json
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys


# ---------------------------------------------------------------------------
# Stats helpers (no external dependencies)
# ---------------------------------------------------------------------------

def _mean(vals: list) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals: list) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _cv(vals: list) -> float:
    m = _mean(vals)
    return _std(vals) / m if m != 0.0 else 0.0


def _percentile(vals: list, pct: float) -> float:
    """Compute the p-th percentile of sorted vals (linear interpolation)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    k = (n - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, n - 1)
    return s[lo] + (k - lo) * (s[hi] - s[lo])


def _ci95(vals: list) -> tuple:
    """
    Approximate 95% confidence interval for the mean (t-distribution, large N ≈ z).
    For small N (< 30), this is a rough estimate.
    """
    n = len(vals)
    if n < 2:
        return (0.0, 0.0)
    m = _mean(vals)
    s = _std(vals)
    # Use 1.96 for large N; t_0.025 ≈ 2.0 for small N — conservative
    z = 2.0 if n < 30 else 1.96
    margin = z * s / math.sqrt(n)
    return (m - margin, m + margin)


def _entropy_bits(vals: list, bins: int = 10) -> float:
    """
    Compute approximate entropy of a distribution in bits using histogram binning.
    Low entropy = regular/periodic timing (bot-like). High entropy = human-like.
    """
    if not vals:
        return 0.0
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return 0.0
    bucket_width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = min(int((v - lo) / bucket_width), bins - 1)
        counts[idx] += 1
    total = len(vals)
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)
    return entropy


# ---------------------------------------------------------------------------
# Biometric proxy feature extraction (mirrors BiometricFeatureExtractor)
# ---------------------------------------------------------------------------

def _trigger_onset_velocity(trigger_vals: list) -> float:
    """
    Normalized trigger onset velocity: frames from first crossing 5 to peak / peak_value.
    Mirrors controller/tinyml_biometric_fusion.py::_compute_trigger_onset_velocity.
    Returns mean across all detected onsets; 0.0 if no onsets detected.
    """
    onsets = []
    in_onset = False
    onset_start = 0
    for i, v in enumerate(trigger_vals):
        if not in_onset and v > 5:
            in_onset = True
            onset_start = i
        elif in_onset and (v >= 250 or (i > onset_start and trigger_vals[i - 1] > v)):
            peak = trigger_vals[i - 1] if i > onset_start else v
            duration = max(i - onset_start, 1)
            onsets.append(duration / (peak + 1e-6))
            in_onset = False
    return sum(onsets) / len(onsets) if onsets else 0.0


def _autocorr_py(series: list, lag: int) -> float:
    """Pearson autocorrelation at given lag (pure Python). Returns 0 if insufficient data."""
    n = len(series)
    if n <= lag + 2:
        return 0.0
    x = series[:-lag]
    y = series[lag:]
    m_x = sum(x) / len(x)
    m_y = sum(y) / len(y)
    num = sum((xi - m_x) * (yi - m_y) for xi, yi in zip(x, y))
    s_x = math.sqrt(sum((xi - m_x) ** 2 for xi in x))
    s_y = math.sqrt(sum((yi - m_y) ** 2 for yi in y))
    if s_x < 1e-10 or s_y < 1e-10:
        return 0.0
    return num / (s_x * s_y)


def _session_biometric_fingerprint(session: dict) -> list | None:
    """
    Extract 6-proxy biometric feature vector from a captured session.

    Maps to BiometricFusionClassifier's 7-feature space (6 of 7 available from
    capture data; F1 trigger_resistance_change_rate requires OUTPUT report bytes
    not present in capture JSON):

    [0] onset_l2      — trigger onset velocity L2 (F2)
    [1] onset_r2      — trigger onset velocity R2 (F3)
    [2] tremor_var    — accel_mag variance during low-gyro frames (F4)
    [3] grip_asym     — L2/R2 ratio during dual-press (F5)
    [4] autocorr_lag1 — lx-velocity Pearson autocorr at lag 1 (F6)
    [5] autocorr_lag5 — lx-velocity Pearson autocorr at lag 5 (F7)

    Returns None if the session has too few reports for reliable extraction.

    NOTE on gyro threshold for tremor_var: BiometricFeatureExtractor uses
    gyro_mag < 0.01 rad/s (~9 LSB). At 1000 Hz, real hardware noise floor is
    ~200-400 LSB std, so individual frames rarely fall below 9 LSB. We use
    gyro_mag < 500 LSB as the capture-data proxy for "low motion"; for sessions
    where no frames qualify, tremor_var = 0.0 (non-informative but harmless).
    """
    reports = session.get("reports", [])
    if len(reports) < 20:
        return None

    l2_vals, r2_vals = [], []
    gyro_x_vals, gyro_y_vals, gyro_z_vals = [], [], []
    accel_x_vals, accel_y_vals, accel_z_vals = [], [], []
    lx_vals, ly_vals = [], []

    for r in reports:
        feat = r.get("features", {})

        def _fv(k):
            v = feat.get(k)
            return float(v) if v is not None else None

        l2 = _fv("l2_trigger");  r2 = _fv("r2_trigger")
        gx = _fv("gyro_x");      gy = _fv("gyro_y");      gz = _fv("gyro_z")
        ax = _fv("accel_x");     ay = _fv("accel_y");      az = _fv("accel_z")
        lx = _fv("left_stick_x"); ly_ = _fv("left_stick_y")

        if l2 is not None: l2_vals.append(l2)
        if r2 is not None: r2_vals.append(r2)
        if gx is not None: gyro_x_vals.append(gx)
        if gy is not None: gyro_y_vals.append(gy)
        if gz is not None: gyro_z_vals.append(gz)
        if ax is not None: accel_x_vals.append(ax)
        if ay is not None: accel_y_vals.append(ay)
        if az is not None: accel_z_vals.append(az)
        if lx is not None: lx_vals.append(lx)
        if ly_ is not None: ly_vals.append(ly_)

    # F2: trigger onset velocity L2
    onset_l2 = _trigger_onset_velocity([int(v) for v in l2_vals]) if l2_vals else 0.0

    # F3: trigger onset velocity R2
    onset_r2 = _trigger_onset_velocity([int(v) for v in r2_vals]) if r2_vals else 0.0

    # F4: micro-tremor accel variance during low-motion frames
    # gyro_mag < 500 LSB is the capture-data proxy for "physically still" at 1000Hz.
    _GYRO_STILL_THRESH = 500.0
    still_accel_mags = []
    for gx, gy, gz, ax, ay, az in zip(
        gyro_x_vals, gyro_y_vals, gyro_z_vals,
        accel_x_vals, accel_y_vals, accel_z_vals,
    ):
        if math.sqrt(gx*gx + gy*gy + gz*gz) < _GYRO_STILL_THRESH:
            still_accel_mags.append(math.sqrt(ax*ax + ay*ay + az*az))
    tremor_var = 0.0
    if len(still_accel_mags) >= 5:
        m = sum(still_accel_mags) / len(still_accel_mags)
        tremor_var = sum((v - m) ** 2 for v in still_accel_mags) / len(still_accel_mags)

    # F5: grip asymmetry — L2/R2 ratio during dual-press (both > 10/255)
    grip_ratios = []
    for l2v, r2v in zip(l2_vals, r2_vals):
        if l2v > 10.0 and r2v > 10.0:
            grip_ratios.append(l2v / (r2v + 1e-6))
    grip_asym = sum(grip_ratios) / len(grip_ratios) if grip_ratios else 1.0

    # F6/F7: stick velocity magnitude autocorrelation at lag 1 and lag 5.
    # Velocity = Euclidean distance between consecutive (lx, ly) positions.
    stick_vels: list[float] = []
    for i in range(1, min(len(lx_vals), len(ly_vals))):
        dx = lx_vals[i] - lx_vals[i - 1]
        dy = ly_vals[i] - ly_vals[i - 1]
        stick_vels.append(math.sqrt(dx * dx + dy * dy))

    autocorr1 = _autocorr_py(stick_vels, lag=1)
    autocorr5 = _autocorr_py(stick_vels, lag=5)

    return [onset_l2, onset_r2, tremor_var, grip_asym, autocorr1, autocorr5]


# ---------------------------------------------------------------------------
# L5 press interval extraction (mirrors validate_detection.py)
# ---------------------------------------------------------------------------

_R2_PRESS_THRESH   = 64   # Analog: "pressed" when crossing from below
_R2_RELEASE_THRESH = 30   # Analog: "released" when dropping below
_R2_DIGITAL_BIT    = 3    # buttons_1 bit 3 = R2 digital (DualSense USB)
_L2_DIGITAL_BIT    = 2    # buttons_1 bit 2 = L2 digital (symmetric to R2)
_L5_MIN_PRESSES    = 20   # Minimum presses for reliable L5 calibration
_L5_POOL_MIN       = 5    # Minimum per-button presses to contribute to pooled mode
_CROSS_BIT         = 0x20 # buttons_0 bit 5 = Cross (X) in raw USB HID report
_TRIANGLE_BIT_RAW  = 0x80 # buttons_0 bit 7 = Triangle in raw USB HID report


def _extract_cross_intervals(session: dict) -> list:
    """
    Extract inter-press intervals (ms) from Cross (X) button events.
    Uses rising-edge detection on buttons_0 bit 5 (raw USB HID report byte 8).
    Mirrors _extract_press_intervals() logic; called when R2 has insufficient presses.
    """
    reports = session.get("reports", [])
    intervals: list = []
    prev_ts = None
    above = False
    for r in reports:
        feat = r.get("features", {})
        b0 = feat.get("buttons_0")
        if b0 is None:
            continue
        pressed = bool(b0 & _CROSS_BIT)
        if not above and pressed:
            above = True
            if prev_ts is not None:
                dt = r["timestamp_ms"] - prev_ts
                if dt > 0:
                    intervals.append(float(dt))
            prev_ts = float(r["timestamp_ms"])
        elif above and not pressed:
            above = False
    return intervals


def _extract_l2_intervals(session: dict) -> list:
    """
    Extract inter-press intervals (ms) from L2 trigger events.
    Mirrors _extract_press_intervals() exactly but for L2:
      1. Digital: buttons_1 bit 2 when available (DualSense USB: L2 = bit 2).
      2. Analog fallback: hysteresis on l2_trigger (press>=64, release<30).
    """
    reports = session.get("reports", [])
    intervals: list = []
    prev_ts = None
    above_thresh = False

    use_digital = any(
        r.get("features", {}).get("buttons_1") is not None
        for r in reports[:10]
    )

    for r in reports:
        feat = r.get("features", {})
        if use_digital:
            b1 = feat.get("buttons_1")
            if b1 is None:
                continue
            pressed = bool((b1 >> _L2_DIGITAL_BIT) & 1)
            if not above_thresh and pressed:
                above_thresh = True
                if prev_ts is not None:
                    dt = r["timestamp_ms"] - prev_ts
                    if dt > 0:
                        intervals.append(float(dt))
                prev_ts = float(r["timestamp_ms"])
            elif above_thresh and not pressed:
                above_thresh = False
        else:
            l2 = feat.get("l2_trigger", 0) or 0
            if not above_thresh and l2 >= _R2_PRESS_THRESH:
                above_thresh = True
                if prev_ts is not None:
                    dt = r["timestamp_ms"] - prev_ts
                    if dt > 0:
                        intervals.append(float(dt))
                prev_ts = float(r["timestamp_ms"])
            elif above_thresh and l2 < _R2_RELEASE_THRESH:
                above_thresh = False

    return intervals


def _extract_triangle_intervals(session: dict) -> list:
    """
    Extract inter-press intervals (ms) from Triangle button events.
    Uses rising-edge detection on buttons_0 bit 7 (raw USB HID).
    Mirrors _extract_cross_intervals() logic with _TRIANGLE_BIT_RAW.
    """
    reports = session.get("reports", [])
    intervals: list = []
    prev_ts = None
    above = False
    for r in reports:
        feat = r.get("features", {})
        b0 = feat.get("buttons_0")
        if b0 is None:
            continue
        pressed = bool(b0 & _TRIANGLE_BIT_RAW)
        if not above and pressed:
            above = True
            if prev_ts is not None:
                dt = r["timestamp_ms"] - prev_ts
                if dt > 0:
                    intervals.append(float(dt))
            prev_ts = float(r["timestamp_ms"])
        elif above and not pressed:
            above = False
    return intervals


def _extract_press_intervals(session: dict) -> list:
    """
    Extract inter-press intervals (ms) from R2 trigger events.
    Mirrors validate_detection.py::_l5_extract_intervals exactly:
      1. Digital: buttons_1 bit 3 when available (exact, firmware-driven).
      2. Analog fallback: hysteresis on r2_trigger (press>=64, release<30).
    Returns list of inter-press intervals in ms.
    """
    reports = session.get("reports", [])
    intervals: list = []
    prev_ts = None
    above_thresh = False

    use_digital = any(
        r.get("features", {}).get("buttons_1") is not None
        for r in reports[:10]
    )

    for r in reports:
        feat = r.get("features", {})
        if use_digital:
            b1 = feat.get("buttons_1")
            if b1 is None:
                continue
            pressed = bool((b1 >> _R2_DIGITAL_BIT) & 1)
            if not above_thresh and pressed:
                above_thresh = True
                if prev_ts is not None:
                    dt = r["timestamp_ms"] - prev_ts
                    if dt > 0:
                        intervals.append(float(dt))
                prev_ts = float(r["timestamp_ms"])
            elif above_thresh and not pressed:
                above_thresh = False
        else:
            r2 = feat.get("r2_trigger", 0) or 0
            if not above_thresh and r2 >= _R2_PRESS_THRESH:
                above_thresh = True
                if prev_ts is not None:
                    dt = r["timestamp_ms"] - prev_ts
                    if dt > 0:
                        intervals.append(float(dt))
                prev_ts = float(r["timestamp_ms"])
            elif above_thresh and r2 < _R2_RELEASE_THRESH:
                above_thresh = False

    return intervals


# ---------------------------------------------------------------------------
# Session loading
# ---------------------------------------------------------------------------

def _load_session(path: str) -> dict:
    """Load a session JSON file. Returns None on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "reports" not in data or "metadata" not in data:
            print(f"  WARNING: {path} missing 'reports' or 'metadata' key — skipped.")
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Cannot load {path}: {e} — skipped.")
        return None


# ---------------------------------------------------------------------------
# Feature extraction per session
# ---------------------------------------------------------------------------

def _session_features(session: dict) -> dict:
    """
    Extract per-session statistical features from a captured session file.
    Returns a dict of lists (one entry per report).
    """
    reports = session.get("reports", [])
    inter_event_ms = []
    lx_vals, ly_vals, rx_vals, ry_vals = [], [], [], []
    l2_vals, r2_vals = [], []
    gyro_x_vals, gyro_y_vals, gyro_z_vals = [], [], []

    prev_ts = None
    for r in reports:
        ts = r.get("timestamp_ms", 0)
        if prev_ts is not None:
            dt = ts - prev_ts
            if dt > 0:
                inter_event_ms.append(float(dt))
        prev_ts = ts

        feat = r.get("features", {})
        if feat.get("left_stick_x") is not None:
            lx_vals.append(feat["left_stick_x"])
        if feat.get("left_stick_y") is not None:
            ly_vals.append(feat["left_stick_y"])
        if feat.get("right_stick_x") is not None:
            rx_vals.append(feat["right_stick_x"])
        if feat.get("right_stick_y") is not None:
            ry_vals.append(feat["right_stick_y"])
        if feat.get("l2_trigger") is not None:
            l2_vals.append(feat["l2_trigger"])
        if feat.get("r2_trigger") is not None:
            r2_vals.append(feat["r2_trigger"])
        if feat.get("gyro_x") is not None:
            gyro_x_vals.append(feat["gyro_x"])
        if feat.get("gyro_y") is not None:
            gyro_y_vals.append(feat["gyro_y"])
        if feat.get("gyro_z") is not None:
            gyro_z_vals.append(feat["gyro_z"])

    return {
        "inter_event_ms": inter_event_ms,
        "lx": lx_vals, "ly": ly_vals,
        "rx": rx_vals, "ry": ry_vals,
        "l2": l2_vals, "r2": r2_vals,
        "gyro_x": gyro_x_vals, "gyro_y": gyro_y_vals, "gyro_z": gyro_z_vals,
        "report_count": len(reports),
        "polling_rate_hz": session["metadata"].get("polling_rate_hz", 0),
    }


# ---------------------------------------------------------------------------
# Threshold computation
# ---------------------------------------------------------------------------

def compute_thresholds(sessions: list) -> dict:
    """
    Compute recommended PITL thresholds from a list of loaded session dicts.

    Returns a calibration profile dict suitable for saving as JSON.
    """
    all_features = [_session_features(s) for s in sessions]
    n_sessions = len(all_features)

    # --- L5: timing CV and entropy per session (inter-press intervals) ---
    # Button preference mirrors live TemporalRhythmOracle.extract_features() priority:
    #   Cross (CV=1.373) > L2_dig (1.333) > R2 (1.176) > Triangle (1.138)
    # Pooled fallback: merge all buttons with >=_L5_POOL_MIN presses when no single
    # button reaches _L5_MIN_PRESSES — closes coverage gap for mixed play styles.
    session_cvs: list = []
    session_entropies: list = []
    l5_excluded = 0

    # Per-button coverage tracking for session_stats output
    _btn_cross_counts: list = []
    _btn_l2_counts: list = []
    _btn_r2_counts: list = []
    _btn_tri_counts: list = []
    _btn_cross_cvs: list = []
    _btn_l2_cvs: list = []
    _btn_r2_cvs: list = []
    _btn_tri_cvs: list = []
    _pooled_only_gain = 0   # sessions rescued exclusively by pooled mode

    for i, s in enumerate(sessions):
        cross_ivs = _extract_cross_intervals(s)
        l2_ivs    = _extract_l2_intervals(s)
        r2_ivs    = _extract_press_intervals(s)
        tri_ivs   = _extract_triangle_intervals(s)

        # Track per-button raw counts
        _btn_cross_counts.append(len(cross_ivs))
        _btn_l2_counts.append(len(l2_ivs))
        _btn_r2_counts.append(len(r2_ivs))
        _btn_tri_counts.append(len(tri_ivs))

        # Accumulate per-button CVs for coverage stats (only if sufficient)
        if len(cross_ivs) >= _L5_MIN_PRESSES:
            _btn_cross_cvs.append(_cv(cross_ivs))
        if len(l2_ivs) >= _L5_MIN_PRESSES:
            _btn_l2_cvs.append(_cv(l2_ivs))
        if len(r2_ivs) >= _L5_MIN_PRESSES:
            _btn_r2_cvs.append(_cv(r2_ivs))
        if len(tri_ivs) >= _L5_MIN_PRESSES:
            _btn_tri_cvs.append(_cv(tri_ivs))

        # Priority selection — mirrors TemporalRhythmOracle.extract_features()
        intervals = None
        btn_label = None
        for ivs, label in [
            (cross_ivs, "Cross(X)"),
            (l2_ivs,    "L2_dig"),
            (r2_ivs,    "R2"),
            (tri_ivs,   "Triangle"),
        ]:
            if len(ivs) >= _L5_MIN_PRESSES:
                intervals = ivs
                btn_label = label
                break

        # Pooled fallback
        if intervals is None:
            pool = []
            for ivs in [cross_ivs, l2_ivs, r2_ivs, tri_ivs]:
                if len(ivs) >= _L5_POOL_MIN:
                    pool.extend(ivs)
            if len(pool) >= _L5_MIN_PRESSES:
                intervals = pool
                btn_label = "pooled"
                _pooled_only_gain += 1

        if intervals is None:
            print(f"  L5 WARNING: session {i + 1} — Cross={len(cross_ivs)} L2={len(l2_ivs)} "
                  f"R2={len(r2_ivs)} Triangle={len(tri_ivs)} presses "
                  f"(need {_L5_MIN_PRESSES} single or {_L5_POOL_MIN}+ each pooled) "
                  f"-- excluded from L5 calibration.")
            l5_excluded += 1
            continue

        _ = btn_label  # available for verbose logging
        session_cvs.append(_cv(intervals))
        session_entropies.append(_entropy_bits(intervals))

    if l5_excluded == n_sessions:
        print(f"  L5 WARNING: all {n_sessions} sessions excluded from L5 calibration. "
              "Capture sessions with active button use (>=20 presses per session on any button).")

    # Build 4-button coverage summary
    _l5_button_coverage = {
        "cross":    {
            "n_sessions_sufficient": sum(1 for c in _btn_cross_counts if c >= _L5_MIN_PRESSES),
            "mean_press_count": round(_mean(_btn_cross_counts), 1) if _btn_cross_counts else 0,
            "mean_cv": round(_mean(_btn_cross_cvs), 4) if _btn_cross_cvs else None,
        },
        "l2_dig":   {
            "n_sessions_sufficient": sum(1 for c in _btn_l2_counts if c >= _L5_MIN_PRESSES),
            "mean_press_count": round(_mean(_btn_l2_counts), 1) if _btn_l2_counts else 0,
            "mean_cv": round(_mean(_btn_l2_cvs), 4) if _btn_l2_cvs else None,
        },
        "r2":       {
            "n_sessions_sufficient": sum(1 for c in _btn_r2_counts if c >= _L5_MIN_PRESSES),
            "mean_press_count": round(_mean(_btn_r2_counts), 1) if _btn_r2_counts else 0,
            "mean_cv": round(_mean(_btn_r2_cvs), 4) if _btn_r2_cvs else None,
        },
        "triangle": {
            "n_sessions_sufficient": sum(1 for c in _btn_tri_counts if c >= _L5_MIN_PRESSES),
            "mean_press_count": round(_mean(_btn_tri_counts), 1) if _btn_tri_counts else 0,
            "mean_cv": round(_mean(_btn_tri_cvs), 4) if _btn_tri_cvs else None,
        },
        "pooled_only_gain": _pooled_only_gain,
        "total_excluded": l5_excluded,
    }

    cv_threshold = _percentile(session_cvs, 10) if session_cvs else 0.08
    entropy_threshold = _percentile(session_entropies, 10) if session_entropies else 1.5

    # --- Stick noise floor (L4 calibration input) ---
    all_lx_stds = [_std(f["lx"]) for f in all_features if len(f["lx"]) >= 5]
    all_ly_stds = [_std(f["ly"]) for f in all_features if len(f["ly"]) >= 5]
    all_rx_stds = [_std(f["rx"]) for f in all_features if len(f["rx"]) >= 5]
    all_ry_stds = [_std(f["ry"]) for f in all_features if len(f["ry"]) >= 5]
    stick_noise_floor = _mean(
        [_mean(all_lx_stds), _mean(all_ly_stds), _mean(all_rx_stds), _mean(all_ry_stds)]
    ) if all_lx_stds else 5.0

    # --- IMU noise floor (for stationary threshold in hardware tests) ---
    all_gyro_stds = []
    for f in all_features:
        for axis in ("gyro_x", "gyro_y", "gyro_z"):
            if len(f[axis]) >= 5:
                all_gyro_stds.append(_std(f[axis]))
    imu_noise_floor = _percentile(all_gyro_stds, 95) if all_gyro_stds else 50.0

    # --- L4 Mahalanobis: 6-proxy biometric feature fingerprints ---
    # Extract 6-proxy biometric feature vectors per session.
    # Feature space mirrors BiometricFusionClassifier (6 of 7 features available
    # from capture data; F1 trigger_resistance_change_rate omitted — requires
    # OUTPUT report mode bytes not present in capture JSON).
    fingerprints = []
    for s in sessions:
        fp = _session_biometric_fingerprint(s)
        if fp is not None:
            fingerprints.append(fp)

    mahal_distances: list[float] = []
    if len(fingerprints) >= 2:
        n_fp = len(fingerprints)
        n_feat = len(fingerprints[0])
        # Population mean vector
        feat_means = [
            sum(fp[j] for fp in fingerprints) / n_fp
            for j in range(n_feat)
        ]
        # Population variance vector (with floor to prevent division by zero)
        _VAR_FLOOR = 1e-10
        feat_vars = [
            max(_VAR_FLOOR, sum((fp[j] - feat_means[j]) ** 2 for fp in fingerprints) / n_fp)
            for j in range(n_feat)
        ]
        # Mahalanobis distance of each session from population centroid (diagonal cov)
        for fp in fingerprints:
            dist = math.sqrt(sum(
                (fp[j] - feat_means[j]) ** 2 / feat_vars[j]
                for j in range(n_feat)
            ))
            mahal_distances.append(dist)

    # Principled threshold formula: mean + kσ
    # anomaly_threshold    = mean + 3σ  (~99.7th percentile, Gaussian assumption)
    # continuity_threshold = mean + 2σ  (~95th percentile, Gaussian assumption)
    dist_mean = _mean(mahal_distances)
    dist_std  = _std(mahal_distances)
    if dist_std > 0:
        anomaly_threshold    = dist_mean + 3.0 * dist_std
        continuity_threshold = dist_mean + 2.0 * dist_std
    else:
        # Single session or perfectly consistent sessions: fall back to defaults
        anomaly_threshold    = max(dist_mean * 1.5, 3.0)
        continuity_threshold = max(dist_mean * 1.0, 2.0)

    # --- Confidence level assessment ---
    if n_sessions < 10:
        confidence = "very_low"
        confidence_note = (
            f"N={n_sessions} sessions is insufficient for reliable thresholds. "
            "Minimum N=10 required; N=50 recommended for production."
        )
    elif n_sessions < 25:
        confidence = "low"
        confidence_note = f"N={n_sessions} sessions — thresholds are indicative only. Target N>=50."
    elif n_sessions < 50:
        confidence = "medium"
        confidence_note = f"N={n_sessions} sessions — suitable for initial testing. Target N>=50 for production."
    else:
        confidence = "high"
        confidence_note = f"N={n_sessions} sessions — thresholds suitable for production validation."

    # --- Polling rate summary ---
    polling_rates = [f["polling_rate_hz"] for f in all_features if f["polling_rate_hz"] > 0]
    mean_polling = _mean(polling_rates) if polling_rates else 0.0

    return {
        "calibration_version": "1.0",
        "n_sessions": n_sessions,
        "confidence_level": confidence,
        "confidence_note": confidence_note,
        "hardware_note": (
            "All thresholds derived from real DualShock Edge hardware data. "
            "Replace bridge magic numbers with these values after validating "
            "false-positive rate at each skill tier."
        ),
        "thresholds": {
            "l4_mahalanobis_anomaly": {
                "recommended": round(anomaly_threshold, 3),
                "current_magic_number": 3.0,
                "derivation": (
                    "mean + 3σ of per-session Mahalanobis distance from population centroid "
                    "(6-proxy biometric feature space; ~99.7th percentile assuming Gaussian). "
                    "Feature space: onset_l2, onset_r2, tremor_var, grip_asym, autocorr_lag1, autocorr_lag5."
                ),
                "unit": "Mahalanobis distance (dimensionless)",
                "dist_mean": round(dist_mean, 3) if mahal_distances else None,
                "dist_std":  round(dist_std, 3)  if mahal_distances else None,
                "n_fingerprints": len(fingerprints),
                "ci95": [round(v, 3) for v in _ci95(mahal_distances)] if mahal_distances else None,
            },
            "l4_mahalanobis_continuity": {
                "recommended": round(continuity_threshold, 3),
                "current_magic_number": 2.0,
                "derivation": (
                    "mean + 2σ of per-session Mahalanobis distance from population centroid "
                    "(6-proxy biometric feature space; ~95th percentile assuming Gaussian)."
                ),
                "unit": "Mahalanobis distance (dimensionless)",
                "ci95": [round(v, 3) for v in _ci95(mahal_distances)] if mahal_distances else None,
            },
            "l5_cv_threshold": {
                "recommended": round(cv_threshold, 4),
                "current_magic_number": 0.08,
                "derivation": (
                    "10th percentile of per-session inter-press interval CVs. "
                    "Button priority: Cross > L2_dig > R2 > Triangle; pooled fallback "
                    f"when no single button has >= {_L5_MIN_PRESSES} presses. "
                    f"Sessions used: {len(session_cvs)} / {n_sessions} "
                    f"(excluded {l5_excluded}, pooled-rescued {_pooled_only_gain})."
                ),
                "unit": "coefficient of variation (dimensionless)",
                "ci95": [round(v, 4) for v in _ci95(session_cvs)] if session_cvs else None,
            },
            "l5_entropy_threshold": {
                "recommended": round(entropy_threshold, 3),
                "current_magic_number": 1.5,
                "derivation": (
                    "10th percentile of per-session inter-press interval entropy (10-bin histogram). "
                    f"Sessions used: {len(session_entropies)} / {n_sessions}."
                ),
                "unit": "bits",
                "ci95": [round(v, 3) for v in _ci95(session_entropies)] if session_entropies else None,
            },
            "stick_noise_floor_lsb": {
                "recommended": round(stick_noise_floor, 2),
                "current_magic_number": 5.0,
                "derivation": "mean of per-axis std across all sessions",
                "unit": "LSB (raw ADC counts, range 0–255)",
            },
            "imu_gyro_noise_floor_lsb": {
                "recommended": round(imu_noise_floor, 2),
                "current_magic_number": 50.0,
                "derivation": "95th percentile of per-axis gyro std across sessions",
                "unit": "LSB (raw ADC counts)",
            },
        },
        "session_stats": {
            "mean_polling_rate_hz": round(mean_polling, 1),
            "mean_reports_per_session": round(
                _mean([f["report_count"] for f in all_features]), 0
            ),
            "session_cv_mean": round(_mean(session_cvs), 4) if session_cvs else None,
            "session_entropy_mean": round(_mean(session_entropies), 3) if session_entropies else None,
            "l4_mahal_dist_mean": round(dist_mean, 3) if mahal_distances else None,
            "l4_mahal_dist_std":  round(dist_std, 3)  if mahal_distances else None,
            "l4_fingerprints_extracted": len(fingerprints),
            "l5_button_coverage": _l5_button_coverage,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        prog="threshold_calibrator.py",
        description="Compute empirical PITL thresholds from captured DualShock Edge sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/threshold_calibrator.py sessions/*.json\n"
            "  python scripts/threshold_calibrator.py sessions/s1.json sessions/s2.json "
            "--output calibration_profile.json\n"
        ),
    )
    p.add_argument("sessions", nargs="+",
                   help="Session JSON files produced by capture_session.py")
    p.add_argument("--output", default="calibration_profile.json",
                   help="Output profile path (default: calibration_profile.json)")
    args = p.parse_args()

    # Expand globs (for shells that don't expand them)
    paths = []
    for pattern in args.sessions:
        expanded = glob.glob(pattern)
        if expanded:
            paths.extend(expanded)
        elif os.path.exists(pattern):
            paths.append(pattern)
        else:
            print(f"WARNING: No files matching '{pattern}'")

    if not paths:
        print("ERROR: No session files found.", file=sys.stderr)
        return 1

    print(f"Loading {len(paths)} session file(s)...")
    sessions = []
    for path in paths:
        s = _load_session(path)
        if s:
            sessions.append(s)
            meta = s["metadata"]
            print(f"  {os.path.basename(path)}: "
                  f"{meta.get('report_count', '?')} reports "
                  f"@ {meta.get('polling_rate_hz', '?')} Hz")

    if not sessions:
        print("ERROR: No valid sessions loaded.", file=sys.stderr)
        return 1

    n = len(sessions)
    if n < 10:
        print(f"\nWARNING: Only {n} sessions loaded. "
              "Minimum N=10 required for reliable thresholds; N=50 for production.")

    print(f"\nComputing thresholds from {n} session(s)...")
    profile = compute_thresholds(sessions)

    # Save profile
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    # Print summary
    thresholds = profile["thresholds"]
    print(f"\n{'='*60}")
    print(f"Calibration Profile — {n} sessions ({profile['confidence_level']} confidence)")
    print(f"{'='*60}")
    print(f"{'Threshold':<40} {'Recommended':>12} {'Current':>10}")
    print(f"{'-'*40} {'-'*12} {'-'*10}")
    for name, t in thresholds.items():
        rec = t.get("recommended", "N/A")
        cur = t.get("current_magic_number", "N/A")
        print(f"{name:<40} {rec:>12} {cur:>10}")

    print(f"\nConfidence: {profile['confidence_note']}")
    print(f"\nProfile saved to: {args.output}")
    print(
        "\nNext steps:\n"
        "  1. Review threshold changes vs. current magic numbers\n"
        "  2. Update BiometricFusionClassifier.ANOMALY_THRESHOLD and CONTINUITY_THRESHOLD\n"
        "  3. Update TemporalRhythmOracle CV and entropy thresholds\n"
        "  4. Re-run full test suite: python -m pytest bridge/tests/ -q\n"
        "  5. Re-run hardware tests: pytest tests/hardware/ -v -m hardware\n"
        "  6. Update docs/detection-benchmarks.md with empirical figures"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
