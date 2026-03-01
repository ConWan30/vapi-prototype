"""
threshold_calibrator.py — Empirical PITL threshold calibration tool.

Takes one or more session files produced by scripts/capture_session.py and
computes recommended threshold values for the PITL detection stack.

WARNING: Minimum N=10 sessions is required for reliable thresholds.
Fewer than 10 sessions will produce a warning and wide confidence intervals.
Production thresholds require N≥50 sessions across multiple sessions and days.

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

    # --- L5: timing coefficient of variation per session ---
    session_cvs = [
        _cv(f["inter_event_ms"])
        for f in all_features
        if len(f["inter_event_ms"]) >= 10
    ]
    # L5 CV threshold: set to 10th percentile of human session CVs.
    # Sessions below this threshold are flagged as suspiciously regular.
    # If human players have CV ≥ 0.15, setting threshold at P10 gives ~90% TPR for macros.
    cv_threshold = _percentile(session_cvs, 10) if session_cvs else 0.08

    # --- L5: timing entropy per session ---
    session_entropies = [
        _entropy_bits(f["inter_event_ms"])
        for f in all_features
        if len(f["inter_event_ms"]) >= 10
    ]
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

    # --- L4 Mahalanobis: inter-session fingerprint distances ---
    # Build per-session fingerprint vectors (stick axis means)
    fingerprints = []
    for f in all_features:
        if all(len(f[k]) >= 5 for k in ("lx", "ly", "rx", "ry")):
            fingerprints.append({
                "lx": _mean(f["lx"]), "ly": _mean(f["ly"]),
                "rx": _mean(f["rx"]), "ry": _mean(f["ry"]),
            })

    intra_distances = []
    if len(fingerprints) >= 2:
        # Compute pairwise L2 distances between session fingerprints
        for i in range(len(fingerprints)):
            for j in range(i + 1, len(fingerprints)):
                a, b = fingerprints[i], fingerprints[j]
                dist = math.sqrt(sum(
                    (a[k] - b[k]) ** 2 for k in ("lx", "ly", "rx", "ry")
                ))
                intra_distances.append(dist)

    # Continuity threshold: 95th percentile of same-device inter-session distances
    # Sessions above this threshold are flagged as a different biometric profile
    continuity_threshold = _percentile(intra_distances, 95) if intra_distances else 2.0
    # Anomaly threshold: continuity_threshold × 1.5 (sessions much further away = anomaly)
    anomaly_threshold = continuity_threshold * 1.5 if continuity_threshold > 0 else 3.0

    # --- Confidence level assessment ---
    if n_sessions < 5:
        confidence = "very_low"
        confidence_note = (
            f"N={n_sessions} sessions is insufficient for reliable thresholds. "
            "Minimum N=10 required; N=50 recommended for production."
        )
    elif n_sessions < 10:
        confidence = "low"
        confidence_note = f"N={n_sessions} sessions — thresholds are indicative only. Target N≥50."
    elif n_sessions < 30:
        confidence = "medium"
        confidence_note = f"N={n_sessions} sessions — suitable for initial testing. Target N≥50 for production."
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
                "derivation": "inter-session fingerprint L2 × 1.5 (P95 baseline × safety factor)",
                "unit": "Mahalanobis distance (dimensionless)",
                "ci95": [round(v, 3) for v in _ci95(intra_distances)] if intra_distances else None,
            },
            "l4_mahalanobis_continuity": {
                "recommended": round(continuity_threshold, 3),
                "current_magic_number": 2.0,
                "derivation": "95th percentile of same-device inter-session fingerprint L2 distances",
                "unit": "Mahalanobis distance (dimensionless)",
                "ci95": [round(v, 3) for v in _ci95(intra_distances)] if intra_distances else None,
            },
            "l5_cv_threshold": {
                "recommended": round(cv_threshold, 4),
                "current_magic_number": 0.08,
                "derivation": "10th percentile of human session timing CVs",
                "unit": "coefficient of variation (dimensionless)",
                "ci95": [round(v, 4) for v in _ci95(session_cvs)] if session_cvs else None,
            },
            "l5_entropy_threshold": {
                "recommended": round(entropy_threshold, 3),
                "current_magic_number": 1.5,
                "derivation": "10th percentile of human session timing entropy (10-bin histogram)",
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
