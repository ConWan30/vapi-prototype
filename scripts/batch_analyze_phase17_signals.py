#!/usr/bin/env python3
"""
scripts/batch_analyze_phase17_signals.py — Phase 17 Validation & Tuning

Empirically validates every new Phase 17 signal against all N=69 human
sessions (hw_005–hw_073). Reports distributions, false-positive rates, and
pass/fail verdicts for every new detection feature.

Signals analyzed:
    L2B — IMU-button causal latency oracle (coupled_fraction, 0x31 advisory)
    L2C — Stick-IMU cross-correlation oracle (max_causal_corr, 0x32 advisory)
    L4  — BiometricFusionClassifier new features (tremor FFT, touchpad)
    L5  — TemporalRhythmOracle (R2 timing distribution)
    Auto-calibration agent — threshold evolution simulation
    Humanity formula — old vs new weighted sum

Usage:
    cd C:/Users/Contr/vapi-pebble-prototype
    python scripts/batch_analyze_phase17_signals.py
    python scripts/batch_analyze_phase17_signals.py --max-frames 10000 --no-plots

Output:
    analysis/phase17_validation/results.json  — raw per-session metrics
    docs/phase17-validation-results.md        — human-readable report
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Path setup — run from project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "controller"))
sys.path.insert(0, str(PROJECT_ROOT / "bridge"))

from l2b_imu_press_correlation import ImuPressCorrelationOracle
from l2c_stick_imu_correlation import StickImuCorrelationOracle
from tinyml_biometric_fusion import (
    BiometricFeatureExtractor,
    BiometricFeatureFrame,
    BiometricFusionClassifier,
)
from temporal_rhythm_oracle import TemporalRhythmOracle

# ---------------------------------------------------------------------------
# Player mapping (hw_005-044 = P1, 045-058 = P2, 059-073 = P3)
# ---------------------------------------------------------------------------

def session_player(name: str) -> str:
    try:
        num = int(Path(name).stem.split("_")[1])
    except (IndexError, ValueError):
        return "?"
    if num <= 44:
        return "P1"
    if num <= 58:
        return "P2"
    return "P3"


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------

def make_snap(report: dict, prev_ts: Optional[float]) -> SimpleNamespace:
    """Convert one session report dict to an oracle-compatible SimpleNamespace."""
    f = report.get("features", {})
    ts = float(report.get("timestamp_ms", 0))

    # Derive inter_frame_us from timestamp delta (1ms at 1000 Hz → 1000 µs)
    if prev_ts is not None and ts > prev_ts:
        inter_frame_us = int((ts - prev_ts) * 1000)
    else:
        inter_frame_us = 1000  # sensible default for 1000 Hz sessions

    buttons_0_raw = int(f.get("buttons_0", 0))
    # In raw DualSense HID report bytes, buttons_0 encodes:
    #   bits 0-3: D-pad hat switch (8=neutral, 0=up, 2=right, 4=down, 6=left, ...)
    #   bit 4: Square | bit 5: Cross(X) | bit 6: Circle | bit 7: Triangle
    # The live oracle uses snap.buttons where bit 0 = Cross (from ds.state.cross).
    # Remap: cross_active = bit 5 of buttons_0_raw.
    cross_active = (buttons_0_raw >> 5) & 1

    return SimpleNamespace(
        timestamp_ms=ts,
        left_stick_x=int(f.get("left_stick_x", 128)),
        left_stick_y=int(f.get("left_stick_y", 128)),
        right_stick_x=int(f.get("right_stick_x", 128)),
        right_stick_y=int(f.get("right_stick_y", 128)),
        l2_trigger=int(f.get("l2_trigger", 0)),
        r2_trigger=int(f.get("r2_trigger", 0)),
        # L2B / L5 expect snap.buttons bit 0 = Cross.
        # Cross is at bit 5 of the raw HID buttons_0 byte; remap it to bit 0.
        buttons=cross_active,
        buttons_0=buttons_0_raw,
        gyro_x=float(f.get("gyro_x", 0)),
        gyro_y=float(f.get("gyro_y", 0)),
        gyro_z=float(f.get("gyro_z", 0)),
        accel_x=float(f.get("accel_x", 0)),
        accel_y=float(f.get("accel_y", 0)),
        accel_z=float(f.get("accel_z", 0)),
        l2_effect_mode=int(f.get("l2_effect_mode", 0)),
        r2_effect_mode=int(f.get("r2_effect_mode", 0)),
        inter_frame_us=inter_frame_us,
        touch_active=bool(f.get("touch_active", False)),
        touch0_x=float(f.get("touch0_x", 0)),
        touch0_y=float(f.get("touch0_y", 0)),
    )


# ---------------------------------------------------------------------------
# Single-session analysis
# ---------------------------------------------------------------------------

_L4_FEATURE_NAMES = [
    "trigger_resistance_change_rate",
    "trigger_onset_velocity_l2",
    "trigger_onset_velocity_r2",
    "micro_tremor_accel_variance",
    "grip_asymmetry",
    "stick_autocorr_lag1",
    "stick_autocorr_lag5",
    "tremor_peak_hz",       # NEW Phase 17
    "tremor_band_power",    # NEW Phase 17
    "touchpad_active_fraction",  # NEW Phase 17
    "touch_position_variance",   # NEW Phase 17
]

_L4_SAMPLE_INTERVAL = 500   # extract L4 features every N frames
_L4_WINDOW_FRAMES   = 120   # window size passed to BiometricFeatureExtractor


def analyze_session(path: Path, max_frames: int) -> dict:
    """
    Run all Phase 17 oracles against one session file.

    Returns a dict with per-session metrics.  Processing is streaming:
    only the oracle's own rolling deques are held in memory at any time.
    """
    print(f"  {path.name} ...", end="", flush=True)

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    meta    = data.get("metadata", {})
    reports = data.get("reports", [])
    if max_frames > 0:
        reports = reports[:max_frames]
    n_processed = len(reports)

    polling_hz  = float(meta.get("polling_rate_hz", 0))
    user_notes  = meta.get("user_notes", "")

    # Check if any report has touch_active field (Phase 17 capture feature)
    has_touch_data = any("touch_active" in r.get("features", {}) for r in reports[:100])

    # Initialize oracles
    oracle_l2b  = ImuPressCorrelationOracle()
    oracle_l2c  = StickImuCorrelationOracle()
    oracle_l5   = TemporalRhythmOracle()
    extractor   = BiometricFeatureExtractor()
    snap_window: deque = deque(maxlen=_L4_WINDOW_FRAMES)

    l4_samples: list[BiometricFeatureFrame] = []
    prev_ts: Optional[float] = None
    touch_active_count = 0
    total_frames = 0

    for i, report in enumerate(reports):
        snap = make_snap(report, prev_ts)
        prev_ts = snap.timestamp_ms
        total_frames += 1

        if snap.touch_active:
            touch_active_count += 1

        # Feed oracles
        oracle_l2b.push_snapshot(snap)
        oracle_l2c.push_snapshot(snap)
        oracle_l5.push_frame(snap)

        # Maintain L4 rolling window
        snap_window.append(snap)

        # Periodic L4 feature extraction
        if total_frames % _L4_SAMPLE_INTERVAL == 0 and len(snap_window) >= 10:
            feat = extractor.extract(list(snap_window), window_frames=_L4_WINDOW_FRAMES)
            l4_samples.append(feat)

    # Final L4 extraction
    if snap_window and total_frames % _L4_SAMPLE_INTERVAL != 0:
        feat = extractor.extract(list(snap_window), window_frames=_L4_WINDOW_FRAMES)
        l4_samples.append(feat)

    # ---- L2B metrics ----
    l2b_feat = oracle_l2b.extract_features()
    l2b_coupled_fraction  = l2b_feat.coupled_fraction if l2b_feat else None
    l2b_press_count       = l2b_feat.press_count      if l2b_feat else 0
    l2b_anomaly           = l2b_feat.anomaly          if l2b_feat else None
    l2b_humanity          = oracle_l2b.humanity_score()
    l2b_advisory_fires    = l2b_anomaly is True   # bool: True = false positive on human data

    # ---- L2C metrics ----
    l2c_feat = oracle_l2c.extract_features()
    l2c_max_corr   = l2c_feat.max_causal_corr if l2c_feat else None
    l2c_lag        = l2c_feat.lag_at_max      if l2c_feat else None
    l2c_anomaly    = l2c_feat.anomaly         if l2c_feat else None
    l2c_static     = l2c_feat is None         # stick never left dead zone
    l2c_humanity   = oracle_l2c.humanity_score()
    l2c_advisory_fires = l2c_anomaly is True

    # ---- L5 metrics ----
    l5_result = oracle_l5.classify()
    l5_stats  = oracle_l5.extract_features()
    l5_cv         = l5_stats.cv          if l5_stats else None
    l5_entropy    = l5_stats.entropy     if l5_stats else None
    l5_quant      = l5_stats.quant_score if l5_stats else None
    l5_advisory_fires = l5_result is not None

    # ---- L4 feature aggregation ----
    if l4_samples:
        vecs = np.array([s.to_vector() for s in l4_samples], dtype=np.float64)
        l4_vec_mean = vecs.mean(axis=0).tolist()
        l4_vec_std  = vecs.std(axis=0).tolist() if len(vecs) > 1 else [0.0] * 11
    else:
        l4_vec_mean = [0.0] * 11
        l4_vec_std  = [0.0] * 11

    print(
        f" L2B={l2b_coupled_fraction:.3f}({l2b_press_count}px)" if l2b_feat else " L2B=None",
        f"L2C={l2c_max_corr:.3f}" if l2c_feat else ("L2C=None(static)" if l2c_static else "L2C=None"),
        f"L5_cv={l5_cv:.3f}" if l5_cv else "L5=None",
    )

    return {
        "session":        path.name,
        "player":         session_player(path.name),
        "n_reports":      len(data.get("reports", [])),
        "n_processed":    n_processed,
        "polling_hz":     polling_hz,
        "user_notes":     user_notes,
        "has_touch_data": has_touch_data,
        "touch_active_count": touch_active_count,
        # L2B
        "l2b_press_count":      l2b_press_count,
        "l2b_coupled_fraction": l2b_coupled_fraction,
        "l2b_anomaly":          l2b_anomaly,
        "l2b_humanity":         l2b_humanity,
        "l2b_advisory_fires":   l2b_advisory_fires,
        # L2C
        "l2c_max_corr":       l2c_max_corr,
        "l2c_lag":            l2c_lag,
        "l2c_anomaly":        l2c_anomaly,
        "l2c_static":         l2c_static,
        "l2c_humanity":       l2c_humanity,
        "l2c_advisory_fires": l2c_advisory_fires,
        # L5
        "l5_cv":             l5_cv,
        "l5_entropy":        l5_entropy,
        "l5_quant":          l5_quant,
        "l5_advisory_fires": l5_advisory_fires,
        # L4 features
        "l4_vec_mean": l4_vec_mean,
        "l4_vec_std":  l4_vec_std,
        "l4_n_samples": len(l4_samples),
    }


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def _stats(vals: list) -> dict:
    """Compute summary statistics for a list of floats (Nones excluded)."""
    clean = [v for v in vals if v is not None]
    if not clean:
        return {"n": 0, "mean": None, "std": None, "min": None,
                "p5": None, "p10": None, "p50": None, "p90": None, "max": None}
    a = np.array(clean, dtype=np.float64)
    return {
        "n":    len(clean),
        "mean": float(a.mean()),
        "std":  float(a.std()),
        "min":  float(a.min()),
        "p5":   float(np.percentile(a, 5)),
        "p10":  float(np.percentile(a, 10)),
        "p50":  float(np.percentile(a, 50)),
        "p90":  float(np.percentile(a, 90)),
        "max":  float(a.max()),
    }


def _player_split(rows: list[dict], key: str) -> dict:
    """Return {player: [values]} grouping for a given metric key."""
    out: dict[str, list] = {}
    for r in rows:
        p = r["player"]
        v = r.get(key)
        out.setdefault(p, []).append(v)
    return out


# ---------------------------------------------------------------------------
# Calibration threshold simulation
# ---------------------------------------------------------------------------

def simulate_calibration(rows: list[dict], splits: list[int]) -> list[dict]:
    """
    Simulate what L4 thresholds would be computed at each cumulative session count.

    Uses the same population-Mahalanobis approach as threshold_calibrator.py:
        anomaly_threshold    = mean(dist) + 3 * std(dist)
        continuity_threshold = mean(dist) + 2 * std(dist)

    Where dist is computed from the 11-feature L4 vectors.
    """
    # Collect valid L4 vectors (sessions with l4_n_samples > 0)
    vecs_ordered = []
    for r in rows:
        if r["l4_n_samples"] > 0 and r["l4_vec_mean"] is not None:
            vecs_ordered.append(np.array(r["l4_vec_mean"], dtype=np.float64))

    results = []
    for n in splits:
        subset = vecs_ordered[:n]
        if len(subset) < 5:
            continue
        mat = np.array(subset, dtype=np.float64)
        pop_mean = mat.mean(axis=0)
        pop_var  = np.maximum(mat.var(axis=0), 1e-9)

        dists = []
        for v in subset:
            diff = v - pop_mean
            d = float(np.sqrt(np.sum(diff ** 2 / pop_var)))
            dists.append(d)

        a_arr = np.array(dists)
        m, s = float(a_arr.mean()), float(a_arr.std())
        results.append({
            "n_sessions":          n,
            "dist_mean":           round(m, 4),
            "dist_std":            round(s, 4),
            "anomaly_threshold":   round(m + 3 * s, 4),
            "continuity_threshold": round(m + 2 * s, 4),
        })
    return results


# ---------------------------------------------------------------------------
# Humanity formula comparison
# ---------------------------------------------------------------------------

def humanity_comparison(rows: list[dict]) -> dict:
    """
    Compare old vs new humanity_probability formula across sessions.

    OLD (pre-Phase 17):  0.40*L4 + 0.40*L5 + 0.20*E4
    NEW (Phase 17):      0.28*L4 + 0.27*L5 + 0.20*E4 + 0.15*L2B + 0.10*L2C

    E4 = 0.5 (neutral, not computable in batch)
    L4_score = 1.0 (sessions within normal threshold)
    L5_score = 1 - (0.5 if advisory fired else 0)  (simplified)
    L2B_score = l2b_humanity
    L2C_score = l2c_humanity
    """
    old_scores = []
    new_scores = []

    for r in rows:
        # L4: 1.0 if no anomaly else 0.5 (simplified — batch cross-session check)
        l4_score = 1.0  # all human sessions expected clean

        # L5
        l5_score = 0.5 if r["l5_advisory_fires"] else 1.0

        # E4 — not computable in batch
        e4_score = 0.5

        # L2B / L2C
        l2b = r["l2b_humanity"]   # float [0, 1]
        l2c = r["l2c_humanity"]   # float [0, 1]

        old_h = 0.40 * l4_score + 0.40 * l5_score + 0.20 * e4_score
        new_h = 0.28 * l4_score + 0.27 * l5_score + 0.20 * e4_score + 0.15 * l2b + 0.10 * l2c

        old_scores.append(old_h)
        new_scores.append(new_h)

    old_arr = np.array(old_scores)
    new_arr = np.array(new_scores)
    delta   = new_arr - old_arr

    return {
        "old": {"mean": float(old_arr.mean()), "std": float(old_arr.std()),
                "min": float(old_arr.min()), "max": float(old_arr.max())},
        "new": {"mean": float(new_arr.mean()), "std": float(new_arr.std()),
                "min": float(new_arr.min()), "max": float(new_arr.max())},
        "delta": {"mean": float(delta.mean()), "std": float(delta.std()),
                  "min": float(delta.min()), "max": float(delta.max())},
        "n_below_0_5_old": int((old_arr < 0.5).sum()),
        "n_below_0_5_new": int((new_arr < 0.5).sum()),
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def fmt(v, precision: int = 4, pct: bool = False) -> str:
    if v is None:
        return "N/A"
    if pct:
        return f"{v:.1%}"
    return f"{v:.{precision}f}"


def generate_report(rows: list[dict], calib_sim: list[dict],
                    hum_cmp: dict, args: argparse.Namespace) -> str:
    n = len(rows)
    n_sessions = n
    anomalous_sessions = [
        r for r in rows
        if r["polling_hz"] > 0 and (r["polling_hz"] < 800 or r["polling_hz"] > 1100)
    ]

    # ----------------------------------------------------------------
    # L2B aggregation
    # ----------------------------------------------------------------
    l2b_has_data  = [r for r in rows if r["l2b_coupled_fraction"] is not None]
    l2b_none      = [r for r in rows if r["l2b_coupled_fraction"] is None]
    l2b_fp        = [r for r in l2b_has_data if r["l2b_advisory_fires"]]
    l2b_cf_vals   = [r["l2b_coupled_fraction"] for r in l2b_has_data]
    l2b_stats     = _stats(l2b_cf_vals)
    l2b_by_player = _player_split(l2b_has_data, "l2b_coupled_fraction")

    # ----------------------------------------------------------------
    # L2C aggregation
    # ----------------------------------------------------------------
    l2c_active    = [r for r in rows if r["l2c_max_corr"] is not None]
    l2c_static    = [r for r in rows if r["l2c_static"]]
    l2c_fp        = [r for r in l2c_active if r["l2c_advisory_fires"]]
    l2c_corr_vals = [r["l2c_max_corr"] for r in l2c_active]
    l2c_lag_vals  = [r["l2c_lag"] for r in l2c_active if r["l2c_lag"] is not None]
    l2c_stats     = _stats(l2c_corr_vals)
    l2c_by_player = _player_split(l2c_active, "l2c_max_corr")

    # ----------------------------------------------------------------
    # L5 aggregation
    # ----------------------------------------------------------------
    l5_has_data  = [r for r in rows if r["l5_cv"] is not None]
    l5_fp        = [r for r in rows if r["l5_advisory_fires"]]
    l5_cv_stats  = _stats([r["l5_cv"] for r in l5_has_data])
    l5_ent_stats = _stats([r["l5_entropy"] for r in l5_has_data])

    # ----------------------------------------------------------------
    # L4 feature aggregation
    # ----------------------------------------------------------------
    l4_has_data  = [r for r in rows if r["l4_n_samples"] > 0]
    l4_touch_has = [r for r in rows if r["has_touch_data"]]

    l4_per_feature: list[dict] = []
    for fi, fname in enumerate(_L4_FEATURE_NAMES):
        vals = [r["l4_vec_mean"][fi] for r in l4_has_data
                if r["l4_vec_mean"] is not None]
        s = _stats(vals)
        l4_per_feature.append({"name": fname, **s})

    # Compute population Mahalanobis distances using all valid L4 vectors
    l4_mahal_stats = None
    l4_n_exceed_threshold = 0
    ANOMALY_THRESH_OLD = 6.905  # N=69 calibration profile value
    if len(l4_has_data) >= 5:
        mat = np.array([r["l4_vec_mean"] for r in l4_has_data], dtype=np.float64)
        pop_mean = mat.mean(axis=0)
        pop_var  = np.maximum(mat.var(axis=0), 1e-9)
        dists = []
        for row in l4_has_data:
            diff = np.array(row["l4_vec_mean"]) - pop_mean
            d = float(np.sqrt(np.sum(diff ** 2 / pop_var)))
            row["l4_mahal_dist"] = d
            dists.append(d)
        l4_mahal_stats = _stats(dists)
        l4_n_exceed_threshold = sum(1 for d in dists if d > ANOMALY_THRESH_OLD)

    # ----------------------------------------------------------------
    # Touch data coverage
    # ----------------------------------------------------------------
    n_touch_sessions = sum(1 for r in rows if r["has_touch_data"])

    lines = []
    lines.append("# Phase 17 Validation & Tuning — Empirical Results")
    lines.append("")
    lines.append(f"**Dataset:** N={n_sessions} human sessions (hw_005–hw_073)")
    lines.append(f"**Players:** 3 (P1=hw_005–044, P2=hw_045–058, P3=hw_059–073)")
    lines.append(f"**Max frames per session analyzed:** {args.max_frames:,} (≈{args.max_frames//1000}s at 1000 Hz)")
    lines.append(f"**Anomalous polling-rate sessions:** {len(anomalous_sessions)} "
                 f"({', '.join(r['session'] for r in anomalous_sessions)})")
    lines.append("")

    # ----------------------------------------------------------------
    # L2B
    # ----------------------------------------------------------------
    lines.append("## 1. L2B — IMU-Button Causal Latency Oracle")
    lines.append("")
    lines.append("**Oracle parameters:** precursor window 5–80 ms, IMU spike threshold +30 LSB, "
                 "coupled_fraction threshold 0.55, min 15 press events.")
    lines.append("")
    lines.append(f"- Sessions with ≥15 press events: {len(l2b_has_data)}/{n_sessions}")
    lines.append(f"- Sessions with <15 press events (oracle returns None): {len(l2b_none)}")
    lines.append(f"- **False positives (0x31 fired on human session):** {len(l2b_fp)}")
    lines.append("")
    lines.append("### coupled_fraction distribution")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for k in ["n", "mean", "std", "min", "p5", "p10", "p50", "p90", "max"]:
        lines.append(f"| {k} | {fmt(l2b_stats.get(k))} |")
    lines.append("")
    lines.append("### Per-player breakdown")
    lines.append("")
    lines.append("| Player | N | Mean | Std | Min | Max |")
    lines.append("|--------|---|------|-----|-----|-----|")
    for p, vals in sorted(l2b_by_player.items()):
        s = _stats([v for v in vals if v is not None])
        lines.append(f"| {p} | {s['n']} | {fmt(s['mean'])} | {fmt(s['std'])} "
                     f"| {fmt(s['min'])} | {fmt(s['max'])} |")
    lines.append("")

    # Pass/fail verdict
    pass_l2b_fp = len(l2b_fp) == 0
    pass_l2b_mean = l2b_stats["mean"] is not None and l2b_stats["mean"] >= 0.55
    pass_l2b_std  = l2b_stats["std"]  is not None and l2b_stats["std"]  <= 0.20
    lines.append("### Pass/Fail")
    lines.append("")
    lines.append(f"| Criterion | Threshold | Actual | Result |")
    lines.append(f"|-----------|-----------|--------|--------|")
    lines.append(f"| Zero false positives on human data | 0 FP | {len(l2b_fp)} FP "
                 f"| {'PASS' if pass_l2b_fp else 'FAIL'} |")
    lines.append(f"| Mean coupled_fraction ≥ 0.55 | ≥0.55 | {fmt(l2b_stats['mean'])} "
                 f"| {'PASS' if pass_l2b_mean else 'FAIL'} |")
    lines.append(f"| Std ≤ 0.20 (signal consistency) | ≤0.20 | {fmt(l2b_stats['std'])} "
                 f"| {'PASS' if pass_l2b_std else 'FAIL'} |")
    lines.append("")

    if l2b_fp:
        lines.append("**FALSE POSITIVE SESSIONS (require investigation):**")
        for r in l2b_fp:
            lines.append(f"- {r['session']} (player={r['player']}, "
                         f"coupled_fraction={fmt(r['l2b_coupled_fraction'])}, "
                         f"presses={r['l2b_press_count']})")
        lines.append("")

    # ----------------------------------------------------------------
    # L2C
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 2. L2C — Stick-IMU Temporal Cross-Correlation Oracle")
    lines.append("")
    lines.append("**Oracle parameters:** causal lags 10–60 frames, correlation threshold 0.15, "
                 "min stick std 0.005, min 80 frames.")
    lines.append("")
    lines.append(f"- Sessions with active stick (non-static): {len(l2c_active)}/{n_sessions}")
    lines.append(f"- Sessions with static stick (oracle returns None): {len(l2c_static)}")
    lines.append(f"- **False positives (0x32 fired on human session):** {len(l2c_fp)}")
    if l2c_lag_vals:
        lines.append(f"- Mean lag at max correlation: {np.mean(l2c_lag_vals):.1f} frames "
                     f"(std={np.std(l2c_lag_vals):.1f})")
    lines.append("")
    lines.append("### max_causal_corr distribution (active-stick sessions)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for k in ["n", "mean", "std", "min", "p5", "p10", "p50", "p90", "max"]:
        lines.append(f"| {k} | {fmt(l2c_stats.get(k))} |")
    lines.append("")
    lines.append("### Per-player breakdown")
    lines.append("")
    lines.append("| Player | N | Mean | Std | Min | Max |")
    lines.append("|--------|---|------|-----|-----|-----|")
    for p, vals in sorted(l2c_by_player.items()):
        s = _stats([v for v in vals if v is not None])
        if s["n"] == 0:
            continue
        lines.append(f"| {p} | {s['n']} | {fmt(s['mean'])} | {fmt(s['std'])} "
                     f"| {fmt(s['min'])} | {fmt(s['max'])} |")
    lines.append("")

    pass_l2c_fp   = len(l2c_fp) == 0
    pass_l2c_mean = l2c_stats["mean"] is not None and l2c_stats["mean"] >= 0.15
    lines.append("### Pass/Fail")
    lines.append("")
    lines.append(f"| Criterion | Threshold | Actual | Result |")
    lines.append(f"|-----------|-----------|--------|--------|")
    lines.append(f"| Zero false positives on human data | 0 FP | {len(l2c_fp)} FP "
                 f"| {'PASS' if pass_l2c_fp else 'FAIL'} |")
    lines.append(f"| Mean max_causal_corr ≥ 0.15 (above fire threshold) | ≥0.15 "
                 f"| {fmt(l2c_stats['mean'])} | {'PASS' if pass_l2c_mean else 'FAIL'} |")
    lines.append("")

    if l2c_fp:
        lines.append("**FALSE POSITIVE SESSIONS:**")
        for r in l2c_fp:
            lines.append(f"- {r['session']} (player={r['player']}, "
                         f"max_corr={fmt(r['l2c_max_corr'])}, lag={r['l2c_lag']})")
        lines.append("")

    # ----------------------------------------------------------------
    # L5
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 3. L5 — Temporal Rhythm Oracle (sanity check)")
    lines.append("")
    lines.append("**Oracle parameters:** CV threshold 0.08, entropy threshold 1.0 bits, "
                 "quant threshold 0.55, min 20 R2 intervals.")
    lines.append("")
    lines.append(f"- Sessions with ≥20 R2 presses: {len(l5_has_data)}/{n_sessions}")
    lines.append(f"- **False positives (0x2B fired on human session):** {len(l5_fp)}")
    lines.append("")
    lines.append(f"| Metric | CV | Entropy (bits) |")
    lines.append(f"|--------|-----|----------------|")
    lines.append(f"| Mean   | {fmt(l5_cv_stats.get('mean'))} | {fmt(l5_ent_stats.get('mean'))} |")
    lines.append(f"| Std    | {fmt(l5_cv_stats.get('std'))} | {fmt(l5_ent_stats.get('std'))} |")
    lines.append(f"| P10    | {fmt(l5_cv_stats.get('p10'))} | {fmt(l5_ent_stats.get('p10'))} |")
    lines.append(f"| Min    | {fmt(l5_cv_stats.get('min'))} | {fmt(l5_ent_stats.get('min'))} |")
    lines.append("")
    if l5_fp:
        lines.append("**FALSE POSITIVE SESSIONS:**")
        for r in l5_fp:
            lines.append(f"- {r['session']} (CV={fmt(r['l5_cv'])}, entropy={fmt(r['l5_entropy'])})")
        lines.append("")

    # ----------------------------------------------------------------
    # L4 new features
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 4. L4 — New Biometric Features (Phase 17 additions)")
    lines.append("")
    lines.append(f"Touch data present in sessions: {n_touch_sessions}/{n_sessions} "
                 f"(Phase 17 capture_session.py adds touch_active/touch0_x/y)")
    lines.append("")
    lines.append("### Per-feature distribution across all sessions")
    lines.append("")
    lines.append("| Feature | Mean | Std | Min | P10 | P90 | Max |")
    lines.append("|---------|------|-----|-----|-----|-----|-----|")
    for feat in l4_per_feature:
        is_new = feat["name"] in {
            "tremor_peak_hz", "tremor_band_power",
            "touchpad_active_fraction", "touch_position_variance"
        }
        tag = " **[NEW]**" if is_new else ""
        lines.append(
            f"| {feat['name']}{tag} | {fmt(feat.get('mean'), 4)} | {fmt(feat.get('std'), 4)} "
            f"| {fmt(feat.get('min'), 4)} | {fmt(feat.get('p10'), 4)} "
            f"| {fmt(feat.get('p90'), 4)} | {fmt(feat.get('max'), 4)} |"
        )
    lines.append("")
    lines.append("**Interpretation notes:**")
    lines.append("- `tremor_peak_hz`: at 1000 Hz polling with 120-frame window, FFT resolution "
                 "≈ 8.3 Hz/bin. The 8–12 Hz band has ≤1 bin — spectral resolution is insufficient "
                 "to distinguish physiological tremor at this window length. Values near 0 Hz "
                 "indicate DC-dominated spectrum (static stick).")
    lines.append("- `tremor_band_power`: similarly affected by FFT resolution. Expect near-zero "
                 "values for most sessions due to the spectral leakage issue.")
    lines.append("- `touchpad_active_fraction`: 0.0 for all sessions captured before Phase 17 "
                 "(touch_active field absent). Will be non-zero only in post-Phase 17 captures.")
    lines.append("- `touch_position_variance`: same caveat as touchpad_active_fraction.")
    lines.append("")

    # Mahalanobis on 11-feature space
    if l4_mahal_stats:
        lines.append("### L4 11-feature Mahalanobis distances (cross-session)")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k in ["n", "mean", "std", "min", "p10", "p50", "p90", "max"]:
            lines.append(f"| {k} | {fmt(l4_mahal_stats.get(k))} |")
        lines.append(f"| Sessions exceeding old threshold ({ANOMALY_THRESH_OLD}) | "
                     f"{l4_n_exceed_threshold}/{len(l4_has_data)} |")
        lines.append("")
        new_anomaly_thresh = (l4_mahal_stats["mean"] + 3 * l4_mahal_stats["std"]
                              if l4_mahal_stats["mean"] is not None else None)
        new_cont_thresh    = (l4_mahal_stats["mean"] + 2 * l4_mahal_stats["std"]
                              if l4_mahal_stats["mean"] is not None else None)
        lines.append(f"**Recommended 11-feature thresholds (mean+3σ / mean+2σ):**")
        lines.append(f"- anomaly_threshold = {fmt(new_anomaly_thresh)}")
        lines.append(f"- continuity_threshold = {fmt(new_cont_thresh)}")
        lines.append(f"- (old 7-feature calibration: anomaly=6.905, continuity=5.190)")
        lines.append("")

    # ----------------------------------------------------------------
    # Calibration simulation
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 5. Auto-Calibration Agent — Threshold Evolution Simulation")
    lines.append("")
    lines.append("Cumulative threshold computation at N=20, N=40, N=69 sessions "
                 "(using 11-feature L4 vectors):")
    lines.append("")
    lines.append("| N Sessions | Dist Mean | Dist Std | Anomaly Threshold | Continuity Threshold |")
    lines.append("|------------|-----------|----------|-------------------|----------------------|")
    for c in calib_sim:
        lines.append(
            f"| {c['n_sessions']} | {c['dist_mean']} | {c['dist_std']} "
            f"| {c['anomaly_threshold']} | {c['continuity_threshold']} |"
        )
    lines.append("")
    if len(calib_sim) >= 2:
        t1 = calib_sim[0]["anomaly_threshold"]
        t2 = calib_sim[-1]["anomaly_threshold"]
        delta_pct = abs(t2 - t1) / max(t1, 1e-6) * 100
        guard_ok = delta_pct <= 10.0
        lines.append(f"Delta N=20→N={calib_sim[-1]['n_sessions']}: {delta_pct:.1f}% — "
                     f"10% delta guard: {'PASS' if guard_ok else 'FAIL (would be rejected)'}")
        lines.append("")

    # ----------------------------------------------------------------
    # Humanity formula comparison
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 6. Humanity Formula — Old vs New")
    lines.append("")
    lines.append("Formulas (E4=0.5 neutral, L4=1.0 for all clean sessions):")
    lines.append("- **Old** (pre-Phase 17): `0.40·L4 + 0.40·L5 + 0.20·E4`")
    lines.append("- **New** (Phase 17):     `0.28·L4 + 0.27·L5 + 0.20·E4 + 0.15·L2B + 0.10·L2C`")
    lines.append("")
    lines.append("| Formula | Mean | Std | Min | Max |")
    lines.append("|---------|------|-----|-----|-----|")
    o = hum_cmp["old"]
    nw = hum_cmp["new"]
    lines.append(f"| Old | {fmt(o['mean'])} | {fmt(o['std'])} | {fmt(o['min'])} | {fmt(o['max'])} |")
    lines.append(f"| New | {fmt(nw['mean'])} | {fmt(nw['std'])} | {fmt(nw['min'])} | {fmt(nw['max'])} |")
    delta = hum_cmp["delta"]
    lines.append(f"| Δ (new − old) | {fmt(delta['mean'])} | {fmt(delta['std'])} "
                 f"| {fmt(delta['min'])} | {fmt(delta['max'])} |")
    lines.append("")
    lines.append(f"Sessions below 0.5 humanity — old formula: {hum_cmp['n_below_0_5_old']}, "
                 f"new formula: {hum_cmp['n_below_0_5_new']}")
    lines.append("")

    below_05 = hum_cmp["n_below_0_5_new"]
    pass_hum = below_05 == 0
    lines.append(f"**Pass criterion (no genuine session below 0.5):** "
                 f"{'PASS' if pass_hum else f'FAIL — {below_05} sessions below 0.5'}")
    lines.append("")

    # ----------------------------------------------------------------
    # Per-session summary table
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 7. Per-Session Summary")
    lines.append("")
    lines.append("| Session | Player | Hz | L2B_cf | L2B_FP | L2C_corr | L2C_FP | L5_cv | L5_FP |")
    lines.append("|---------|--------|----|--------|--------|----------|--------|-------|-------|")
    for r in rows:
        hz = f"{r['polling_hz']:.0f}"
        cf  = fmt(r["l2b_coupled_fraction"], 3) if r["l2b_coupled_fraction"] is not None else "-"
        cc  = fmt(r["l2c_max_corr"], 3)         if r["l2c_max_corr"] is not None else "-"
        cv  = fmt(r["l5_cv"], 3)                if r["l5_cv"] is not None else "-"
        l2b_fp_mark = "**FP**" if r["l2b_advisory_fires"] else "ok"
        l2c_fp_mark = "**FP**" if r["l2c_advisory_fires"] else "ok"
        l5_fp_mark  = "**FP**" if r["l5_advisory_fires"]  else "ok"
        lines.append(
            f"| {r['session']} | {r['player']} | {hz} "
            f"| {cf} | {l2b_fp_mark} | {cc} | {l2c_fp_mark} | {cv} | {l5_fp_mark} |"
        )
    lines.append("")

    # ----------------------------------------------------------------
    # Tuning recommendations
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 8. Tuning Recommendations")
    lines.append("")
    recommendations = []

    if not pass_l2b_fp:
        recommendations.append(
            "**[CRITICAL] L2B false positives detected.** "
            "Investigate the flagged sessions. Consider widening the precursor window "
            "(e.g. 5–120 ms) or lowering the IMU spike threshold from 30 → 20 LSB "
            "for sessions with low gyro activity."
        )
    elif l2b_stats["mean"] is not None and l2b_stats["mean"] < 0.65:
        recommendations.append(
            "**[OPTIONAL] L2B mean coupled_fraction is lower than expected "
            f"({fmt(l2b_stats['mean'])}).** "
            "Consider increasing the precursor window from 80 ms → 100 ms to capture "
            "slower human micro-impulses, especially for Player 3 style."
        )

    if not pass_l2c_fp:
        recommendations.append(
            "**[CRITICAL] L2C false positives detected.** "
            "Narrow the causal lag range or raise the correlation threshold from 0.15 → 0.20."
        )

    if l5_fp:
        recommendations.append(
            f"**[WARNING] L5 false positives: {len(l5_fp)} session(s).** "
            "Review CV and entropy values. The current thresholds (CV<0.08, entropy<1.0) "
            "appear too tight for these sessions."
        )

    if n_touch_sessions == 0:
        recommendations.append(
            "**[INFO] No touch data in any existing session.** "
            "The touchpad_active_fraction and touch_position_variance features are "
            "frozen at 0.0 for all current sessions. These 2 features dilute the "
            "11-feature L4 space with uninformative zero-variance dimensions. "
            "Recommendation: exclude touch features from Mahalanobis computation "
            "until post-Phase 17 sessions with touch data are available, OR set "
            "their variance floor to 1.0 to prevent them from inflating distances."
        )

    if l4_mahal_stats and l4_n_exceed_threshold > 0:
        recommendations.append(
            f"**[CRITICAL] {l4_n_exceed_threshold} sessions exceed the old "
            f"L4 anomaly threshold ({ANOMALY_THRESH_OLD}) in the 11-feature space.** "
            "The old thresholds were calibrated on a 6-7 feature space. "
            "Recompute thresholds using the 11-feature space values above "
            f"(recommended: anomaly={fmt(new_anomaly_thresh)}, "
            f"continuity={fmt(new_cont_thresh)})."
        )

    if new_anomaly_thresh and abs(new_anomaly_thresh - ANOMALY_THRESH_OLD) / ANOMALY_THRESH_OLD > 0.10:
        recommendations.append(
            f"**[INFO] 11-feature L4 anomaly threshold ({fmt(new_anomaly_thresh)}) differs "
            f"from current calibration_profile.json value ({ANOMALY_THRESH_OLD}) by "
            f"{abs(new_anomaly_thresh - ANOMALY_THRESH_OLD) / ANOMALY_THRESH_OLD:.1%}.** "
            "Update calibration_profile.json with the 11-feature values before "
            "enabling L4 in production."
        )

    if not recommendations:
        lines.append("All signals pass validation. No tuning required. "
                     "Proceed to commit Phase 17 as production-ready.")
    else:
        for rec in recommendations:
            lines.append(f"- {rec}")
            lines.append("")

    # ----------------------------------------------------------------
    # Summary verdict
    # ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append("## 9. Summary Verdict")
    lines.append("")
    all_pass = pass_l2b_fp and pass_l2c_fp and pass_hum and (len(l5_fp) == 0)
    verdict = "PASS — Phase 17 signals are production-safe on all human sessions." if all_pass else \
              "FAIL — Address the issues above before merging Phase 17."
    lines.append(f"**{verdict}**")
    lines.append("")
    lines.append(f"| Signal | False Positives | Status |")
    lines.append(f"|--------|-----------------|--------|")
    lines.append(f"| L2B (0x31) | {len(l2b_fp)} | {'PASS' if pass_l2b_fp else 'FAIL'} |")
    lines.append(f"| L2C (0x32) | {len(l2c_fp)} | {'PASS' if pass_l2c_fp else 'FAIL'} |")
    lines.append(f"| L5 (0x2B)  | {len(l5_fp)} | {'PASS' if len(l5_fp)==0 else 'FAIL'} |")
    lines.append(f"| Humanity ≥ 0.5 | {hum_cmp['n_below_0_5_new']} below | "
                 f"{'PASS' if pass_hum else 'FAIL'} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 17 signal validation")
    parser.add_argument(
        "--sessions-dir",
        default="sessions/human",
        help="Directory containing hw_*.json session files",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Max frames to process per session (default: 0 = all frames)",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip matplotlib plot generation",
    )
    parser.add_argument(
        "--output",
        default="docs/phase17-validation-results.md",
        help="Output markdown report path",
    )
    args = parser.parse_args()

    sessions_dir = PROJECT_ROOT / args.sessions_dir
    session_files = sorted(sessions_dir.glob("hw_*.json"))
    if not session_files:
        print(f"No hw_*.json files found in {sessions_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Phase 17 Validation — {len(session_files)} sessions in {sessions_dir}")
    print(f"Max frames per session: {args.max_frames:,}")
    print()

    # ---- Per-session analysis ----
    rows = []
    for sf in session_files:
        try:
            result = analyze_session(sf, args.max_frames)
            rows.append(result)
        except Exception as exc:
            print(f"  ERROR processing {sf.name}: {exc}", file=sys.stderr)

    print(f"\nProcessed {len(rows)}/{len(session_files)} sessions successfully.")

    # ---- Calibration simulation ----
    calib_splits = [s for s in [20, 40, len(rows)] if s <= len(rows)]
    calib_sim = simulate_calibration(rows, calib_splits)

    # ---- Humanity formula comparison ----
    hum_cmp = humanity_comparison(rows)

    # ---- Generate report ----
    report_md = generate_report(rows, calib_sim, hum_cmp, args)

    output_path = PROJECT_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_md, encoding="utf-8")
    print(f"\nReport saved to: {output_path}")

    # ---- Save raw metrics JSON ----
    raw_path = PROJECT_ROOT / "analysis/phase17_validation/results.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    # Make rows JSON-serializable (convert np types)
    def _serialize(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        raise TypeError(f"Not serializable: {type(obj)}")

    with open(raw_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, default=_serialize)
    print(f"Raw metrics saved to: {raw_path}")

    # ---- Optional plots ----
    if not args.no_plots:
        try:
            _generate_plots(rows, PROJECT_ROOT / "analysis/phase17_validation")
        except ImportError:
            print("matplotlib not available — skipping plots")
        except Exception as exc:
            print(f"Plot generation failed: {exc}")


def _generate_plots(rows: list[dict], out_dir: Path) -> None:
    """Generate summary plots saved to out_dir/."""
    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- L2B coupled_fraction histogram ----
    cf_vals = [r["l2b_coupled_fraction"] for r in rows if r["l2b_coupled_fraction"] is not None]
    if cf_vals:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(cf_vals, bins=20, color="steelblue", edgecolor="white")
        ax.axvline(0.55, color="red", linestyle="--", label="fire threshold (0.55)")
        ax.set_title("L2B coupled_fraction — Human Sessions (N=69)")
        ax.set_xlabel("coupled_fraction")
        ax.set_ylabel("sessions")
        ax.legend()
        fig.tight_layout()
        fig.savefig(out_dir / "l2b_coupled_fraction.png", dpi=150)
        plt.close(fig)
        print(f"  Saved: {out_dir / 'l2b_coupled_fraction.png'}")

    # ---- L2C max_causal_corr histogram ----
    corr_vals = [r["l2c_max_corr"] for r in rows if r["l2c_max_corr"] is not None]
    if corr_vals:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(corr_vals, bins=20, color="darkorange", edgecolor="white")
        ax.axvline(0.15, color="red", linestyle="--", label="fire threshold (0.15)")
        ax.set_title("L2C max_causal_corr — Active-stick Sessions")
        ax.set_xlabel("max_causal_corr")
        ax.set_ylabel("sessions")
        ax.legend()
        fig.tight_layout()
        fig.savefig(out_dir / "l2c_max_causal_corr.png", dpi=150)
        plt.close(fig)
        print(f"  Saved: {out_dir / 'l2c_max_causal_corr.png'}")

    # ---- L4 new feature distributions ----
    new_feat_idxs = {
        "tremor_peak_hz": 7, "tremor_band_power": 8,
        "touchpad_active_fraction": 9, "touch_position_variance": 10,
    }
    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    for ax, (fname, fi) in zip(axes, new_feat_idxs.items()):
        vals = [r["l4_vec_mean"][fi] for r in rows
                if r["l4_vec_mean"] is not None]
        ax.hist(vals, bins=20, edgecolor="white")
        ax.set_title(fname, fontsize=9)
        ax.set_xlabel("value")
    fig.suptitle("L4 New Features — Human Sessions (Phase 17)")
    fig.tight_layout()
    fig.savefig(out_dir / "l4_new_features.png", dpi=150)
    plt.close(fig)
    print(f"  Saved: {out_dir / 'l4_new_features.png'}")

    # ---- Humanity formula comparison ----
    old_scores, new_scores = [], []
    for r in rows:
        l4, l5 = 1.0, 0.5 if r["l5_advisory_fires"] else 1.0
        e4 = 0.5
        l2b, l2c = r["l2b_humanity"], r["l2c_humanity"]
        old_scores.append(0.40 * l4 + 0.40 * l5 + 0.20 * e4)
        new_scores.append(0.28 * l4 + 0.27 * l5 + 0.20 * e4 + 0.15 * l2b + 0.10 * l2c)

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.scatter(old_scores, new_scores, alpha=0.7, edgecolors="none")
    ax.axhline(0.5, color="red", linestyle="--", label="danger threshold (0.5)")
    ax.axvline(0.5, color="red", linestyle="--")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.3, label="y=x")
    ax.set_xlabel("Old humanity_prob")
    ax.set_ylabel("New humanity_prob")
    ax.set_title("Humanity Formula: Old vs New")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "humanity_comparison.png", dpi=150)
    plt.close(fig)
    print(f"  Saved: {out_dir / 'humanity_comparison.png'}")


if __name__ == "__main__":
    main()
