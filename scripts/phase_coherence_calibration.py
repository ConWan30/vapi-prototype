"""
phase_coherence_calibration.py — Phase 45 investigation (NEGATIVE RESULT — ARCHIVED)

NEGATIVE RESULT: accel_phase_coherence was ruled out as a biometric feature for
active gameplay sessions. Gravity vector dominates accel_x/accel_z during still
frames, preventing cross-axis phase coherence from reflecting tremor oscillation.
Median coherence: -0.058 (still-frame gated), -0.061 (ungated). Separation ratio
regressed to 0.305 (from 0.362). Index-9 slot reverted to touchpad_active_fraction
(zero-variance, excluded by mask). See docs/phase-coherence-calibration.md for the
full calibration report.

Hardware-viable path (not implemented): dedicated stationary-grip enrollment capture
(30s, no gameplay, controller held at rest) would isolate micro-tremor from game-motion
contamination. Cross-axis accel coherence WOULD work in those conditions. This would
require a new enrollment step in the VAPI device certification ceremony.

Original description:
Ran accel_phase_coherence against N=69 calibration sessions, re-derived L4
Mahalanobis thresholds, and computed the updated inter-person separation ratio.

Outputs:
  docs/phase-coherence-calibration.md  — human-readable calibration report
  console                              — distribution summary for review

Usage:
  python scripts/phase_coherence_calibration.py sessions/human/hw_*.json
  python scripts/phase_coherence_calibration.py   # auto-discovers sessions/human/
"""

from __future__ import annotations

import json
import math
import sys
import types
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths / imports
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "sessions" / "human"
DOCS_DIR     = PROJECT_ROOT / "docs"
CONTROLLER_DIR = PROJECT_ROOT / "controller"

if str(CONTROLLER_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_DIR))

from tinyml_biometric_fusion import (
    BiometricFeatureExtractor,
    CALIBRATION_WINDOW_FRAMES,
)

# ---------------------------------------------------------------------------
# Same exclusions as Phase 43 / L4 calibration (N=69 → 64 included)
# ---------------------------------------------------------------------------

EXCLUDED_SESSIONS = {"hw_043", "hw_044", "hw_059", "hw_067", "hw_069", "hw_073"}

PLAYER_MAP: dict[str, list[str]] = {
    "P1": [f"hw_{i:03d}" for i in range(5, 45)],
    "P2": [f"hw_{i:03d}" for i in range(45, 59)],
    "P3": [f"hw_{i:03d}" for i in range(59, 74)],
}

WINDOW_SIZE = CALIBRATION_WINDOW_FRAMES  # 1024 frames

# Tikhonov regularisation (matches BiometricFusionClassifier)
TIKHONOV_LAMBDA = 0.01
ZERO_VAR_THRESHOLD = 1e-4

# ---------------------------------------------------------------------------
# Session loading
# ---------------------------------------------------------------------------

def _build_snap(report: dict, inter_frame_us: int = 1000) -> types.SimpleNamespace:
    # Session JSONs store per-frame data under report["features"]
    f = report.get("features", report)  # fallback to report itself for old format
    s = types.SimpleNamespace(
        accel_x          = float(f.get("accel_x",          0.0)),
        accel_y          = float(f.get("accel_y",          0.0)),
        accel_z          = float(f.get("accel_z",          0.0)),
        gyro_x           = float(f.get("gyro_x",           0.0)),
        gyro_y           = float(f.get("gyro_y",           0.0)),
        gyro_z           = float(f.get("gyro_z",           0.0)),
        left_stick_x     = int(f.get("left_stick_x",       128)),
        left_stick_y     = int(f.get("left_stick_y",       128)),
        right_stick_x    = int(f.get("right_stick_x",      128)),
        right_stick_y    = int(f.get("right_stick_y",      128)),
        l2_trigger       = int(f.get("l2_trigger",         0)),
        r2_trigger       = int(f.get("r2_trigger",         0)),
        l2_effect_mode   = int(f.get("l2_effect_mode",     0)),
        r2_effect_mode   = int(f.get("r2_effect_mode",     0)),
        inter_frame_us   = inter_frame_us,
        touch_active     = bool(f.get("touch_active",      False)),
        touch0_x         = int(f.get("touch0_x",           0)),
        touch0_y         = int(f.get("touch0_y",           0)),
    )
    return s


def _load_session(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        warnings.warn(f"Could not load {path.name}: {e}")
        return None

    meta    = data.get("metadata", {})
    reports = data.get("reports", [])
    if not reports:
        return None

    # Estimate inter-frame interval from polling_rate_hz in metadata
    polling = float(meta.get("polling_rate_hz", 1000.0))
    if polling <= 0:
        polling = 1000.0
    inter_frame_us = int(1_000_000.0 / polling)

    snaps = [_build_snap(r, inter_frame_us) for r in reports]
    return {
        "name":    path.stem,
        "polling": polling,
        "snaps":   snaps,
        "n":       len(snaps),
    }

# ---------------------------------------------------------------------------
# Feature extraction over windows
# ---------------------------------------------------------------------------

_GYRO_STILL_THRESH = 20.0


def _count_still_frames(snaps: list) -> int:
    """Count frames where gyro magnitude < 20.0 LSB (same threshold as still-frame gate)."""
    count = 0
    for s in snaps:
        gx = float(getattr(s, "gyro_x", 0.0))
        gy = float(getattr(s, "gyro_y", 0.0))
        gz = float(getattr(s, "gyro_z", 0.0))
        if math.sqrt(gx * gx + gy * gy + gz * gz) < _GYRO_STILL_THRESH:
            count += 1
    return count


def _session_feature_vectors(session: dict) -> tuple[list[np.ndarray], dict]:
    """
    Extract one feature vector per WINDOW_SIZE window (non-overlapping).
    Returns (vectors, stats) where stats includes still-frame counts and zero-return fraction.
    """
    snaps = session["snaps"]
    extractor = BiometricFeatureExtractor()
    vectors: list[np.ndarray] = []
    still_counts: list[int] = []
    zero_coherence_windows: int = 0

    for start in range(0, len(snaps) - WINDOW_SIZE + 1, WINDOW_SIZE):
        window = snaps[start : start + WINDOW_SIZE]
        feat = extractor.extract(window, window_frames=WINDOW_SIZE)
        vec = feat.to_vector().astype(np.float64)
        vectors.append(vec)
        still_counts.append(_count_still_frames(window))
        if vec[9] == 0.0:  # index 9 = accel_phase_coherence
            zero_coherence_windows += 1

    n_windows = len(vectors)
    stats = {
        "n_windows":             n_windows,
        "mean_still_frames":     float(np.mean(still_counts)) if still_counts else 0.0,
        "min_still_frames":      int(min(still_counts)) if still_counts else 0,
        "max_still_frames":      int(max(still_counts)) if still_counts else 0,
        "zero_coherence_windows": zero_coherence_windows,
        "zero_fraction":         zero_coherence_windows / n_windows if n_windows else 1.0,
    }
    return vectors, stats


def _mean_feature_vector(vectors: list[np.ndarray]) -> np.ndarray:
    return np.mean(vectors, axis=0) if vectors else np.zeros(11)

# ---------------------------------------------------------------------------
# Mahalanobis threshold derivation
# ---------------------------------------------------------------------------

def _mahalanobis(x: np.ndarray, mean: np.ndarray, inv_cov: np.ndarray) -> float:
    d = x - mean
    val = float(d @ inv_cov @ d)
    return math.sqrt(max(val, 0.0))


def _derive_thresholds(
    all_vecs: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    """
    Returns (population_mean, inv_cov, distances, active_indices).
    Zero-variance features excluded from Mahalanobis (same as live path).
    """
    mat = np.array(all_vecs, dtype=np.float64)
    variances = np.var(mat, axis=0)
    active = [i for i, v in enumerate(variances) if v >= ZERO_VAR_THRESHOLD]

    mat_active = mat[:, active]
    mean = np.mean(mat_active, axis=0)
    cov  = np.cov(mat_active.T)
    # Tikhonov regularisation
    if cov.ndim == 0:
        cov = np.array([[float(cov)]])
    cov += TIKHONOV_LAMBDA * np.eye(cov.shape[0])
    try:
        inv_cov = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        inv_cov = np.diag(1.0 / (np.diag(cov) + 1e-9))

    distances = [_mahalanobis(v[active], mean, inv_cov) for v in all_vecs]
    return mean, inv_cov, np.array(distances), active

# ---------------------------------------------------------------------------
# Inter-person separation ratio
# ---------------------------------------------------------------------------

def _separation_ratio(
    player_vecs: dict[str, list[np.ndarray]],
    mean: np.ndarray,
    inv_cov: np.ndarray,
    active: list[int],
) -> float:
    """
    Separation ratio = mean(inter-player Mahal) / mean(intra-player Mahal).
    >2.0 is required for reliable identification.
    """
    player_means = {
        p: np.mean([v[active] for v in vecs], axis=0)
        for p, vecs in player_vecs.items()
        if vecs
    }
    players = list(player_means.keys())

    # Intra-player: mean distance of each session from that player's centroid
    intra_dists: list[float] = []
    for p, vecs in player_vecs.items():
        pm = player_means[p]
        for v in vecs:
            d = v[active] - pm
            val = float(d @ inv_cov @ d)
            intra_dists.append(math.sqrt(max(val, 0.0)))

    # Inter-player: distance between player centroids
    inter_dists: list[float] = []
    for i, p1 in enumerate(players):
        for p2 in players[i + 1:]:
            d = player_means[p1] - player_means[p2]
            val = float(d @ inv_cov @ d)
            inter_dists.append(math.sqrt(max(val, 0.0)))

    if not intra_dists or not inter_dists:
        return 0.0
    return float(np.mean(inter_dists)) / (float(np.mean(intra_dists)) + 1e-9)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) > 1:
        session_paths = [Path(p) for p in sys.argv[1:] if Path(p).is_file()]
    else:
        session_paths = sorted(SESSIONS_DIR.glob("hw_*.json"))

    if not session_paths:
        print("ERROR: No session files found.")
        sys.exit(1)

    # ---- Load sessions ----
    sessions: dict[str, dict] = {}
    for path in session_paths:
        stem = path.stem
        if stem in EXCLUDED_SESSIONS:
            continue
        s = _load_session(path)
        if s is None:
            continue
        # Exclude anomalous polling rates (same rule as calibrator)
        if not (800.0 <= s["polling"] <= 1100.0):
            continue
        sessions[stem] = s

    print(f"Loaded {len(sessions)} sessions (after exclusions)")

    # ---- Per-session feature extraction ----
    all_vecs: list[np.ndarray] = []
    player_vecs: dict[str, list[np.ndarray]] = defaultdict(list)
    player_coherence: dict[str, list[float]] = defaultdict(list)
    player_still_counts: dict[str, list[float]] = defaultdict(list)
    player_zero_fractions: dict[str, list[float]] = defaultdict(list)
    session_coherence: dict[str, float] = {}
    all_still_counts: list[float] = []
    all_zero_fractions: list[float] = []

    for name, session in sessions.items():
        vecs, stats = _session_feature_vectors(session)
        if not vecs:
            continue
        mean_vec = _mean_feature_vector(vecs)
        # Index 9 = accel_phase_coherence
        coh = float(mean_vec[9])
        session_coherence[name] = coh
        all_vecs.append(mean_vec)
        all_still_counts.append(stats["mean_still_frames"])
        all_zero_fractions.append(stats["zero_fraction"])

        # Assign player
        player = None
        for p, names in PLAYER_MAP.items():
            if name in names:
                player = p
                break
        if player:
            player_vecs[player].append(mean_vec)
            player_coherence[player].append(coh)
            player_still_counts[player].append(stats["mean_still_frames"])
            player_zero_fractions[player].append(stats["zero_fraction"])

    if not all_vecs:
        print("ERROR: No feature vectors extracted.")
        sys.exit(1)

    # ---- L4 threshold derivation ----
    pop_mean, inv_cov, distances, active_indices = _derive_thresholds(all_vecs)

    dist_mean = float(np.mean(distances))
    dist_std  = float(np.std(distances))
    anomaly_threshold    = dist_mean + 3 * dist_std   # mean + 3σ
    continuity_threshold = dist_mean + 2 * dist_std   # mean + 2σ

    # ---- Separation ratio ----
    sep_ratio = _separation_ratio(player_vecs, pop_mean, inv_cov, active_indices)

    # ---- Coherence distribution ----
    all_coh = list(session_coherence.values())
    def _stats(vals: list[float]) -> dict:
        if not vals:
            return {}
        a = np.array(vals)
        return {
            "n":      len(vals),
            "min":    float(np.min(a)),
            "p10":    float(np.percentile(a, 10)),
            "median": float(np.median(a)),
            "mean":   float(np.mean(a)),
            "p90":    float(np.percentile(a, 90)),
            "max":    float(np.max(a)),
            "std":    float(np.std(a)),
        }

    pop_stats = _stats(all_coh)
    player_stats = {p: _stats(v) for p, v in player_coherence.items()}
    pop_still_stats = _stats(all_still_counts)
    player_still_stats = {p: _stats(v) for p, v in player_still_counts.items()}

    sessions_fully_zero     = sum(1 for zf in all_zero_fractions if zf == 1.0)
    sessions_partially_zero = sum(1 for zf in all_zero_fractions if 0.0 < zf < 1.0)
    sessions_fully_active   = sum(1 for zf in all_zero_fractions if zf == 0.0)
    total_sessions = len(all_zero_fractions)
    pct_fully_zero = 100.0 * sessions_fully_zero / total_sessions if total_sessions else 0.0

    radar_score = round(pop_stats["median"] * 100) if (pop_stats and pct_fully_zero < 50) else 0

    # ---- Print summary ----
    print("\n=== Still-Frame Gate Statistics (gyro < 20.0 LSB) ===")
    print(f"{'Player':<8} {'N':>4}  {'mean_still':>12}  {'min_still':>10}  {'max_still':>10}")
    print("-" * 55)
    for p, st in player_still_stats.items():
        if not st:
            continue
        print(f"{p:<8} {st['n']:>4}  {st['mean']:>12.1f}  {st['min']:>10.1f}  {st['max']:>10.1f}")
    if pop_still_stats:
        st = pop_still_stats
        print(f"{'ALL':<8} {st['n']:>4}  {st['mean']:>12.1f}  {st['min']:>10.1f}  {st['max']:>10.1f}")
    print(f"\n  Sessions fully inactive   (all windows 0.0): {sessions_fully_zero}/{total_sessions} ({pct_fully_zero:.1f}%)")
    print(f"  Sessions partially active:                   {sessions_partially_zero}/{total_sessions}")
    print(f"  Sessions fully active     (no 0.0 windows):  {sessions_fully_active}/{total_sessions}")

    print("\n=== accel_phase_coherence Distribution (still-frame gated) ===")
    print(f"{'Player':<8} {'N':>4}  {'min':>6}  {'p10':>6}  {'median':>8}  {'mean':>7}  {'p90':>6}  {'max':>6}  {'std':>6}")
    print("-" * 75)
    for p, st in player_stats.items():
        if not st:
            continue
        print(f"{p:<8} {st['n']:>4}  {st['min']:>6.3f}  {st['p10']:>6.3f}  {st['median']:>8.3f}  {st['mean']:>7.3f}  {st['p90']:>6.3f}  {st['max']:>6.3f}  {st['std']:>6.3f}")
    print("-" * 75)
    if pop_stats:
        st = pop_stats
        print(f"{'ALL':<8} {st['n']:>4}  {st['min']:>6.3f}  {st['p10']:>6.3f}  {st['median']:>8.3f}  {st['mean']:>7.3f}  {st['p90']:>6.3f}  {st['max']:>6.3f}  {st['std']:>6.3f}")

    print(f"\n=== L4 Thresholds ===")
    print(f"  Population Mahal mean:   {dist_mean:.3f}")
    print(f"  Population Mahal std:    {dist_std:.3f}")
    print(f"  Anomaly    (mean+3s):    {anomaly_threshold:.3f}  (was 7.019)")
    print(f"  Continuity (mean+2s):    {continuity_threshold:.3f}  (was 5.369)")
    print(f"  Active features:         {len(active_indices)}/11  (indices {active_indices})")

    print(f"\n=== Inter-Person Separation ===")
    print(f"  Separation ratio:  {sep_ratio:.3f}  (was 0.362, target >2.0)")

    print(f"\n=== RADAR_DATA score ===")
    print(f"  Recommended score: {radar_score}  (median*100; 0 if >50pct sessions inactive)")

    # ---- Write docs/phase-coherence-calibration.md ----
    def _fmt(v: float, p: int = 3) -> str:
        return f"{v:.{p}f}"

    lines: list[str] = [
        "# Phase 45 — accel_phase_coherence Calibration Report",
        "",
        f"**Generated from N={len(all_vecs)} sessions** (after Phase 43 exclusions).",
        f"Excluded: {', '.join(sorted(EXCLUDED_SESSIONS))}.",
        f"Window size: {WINDOW_SIZE} frames per vector.",
        "",
        "---",
        "",
        "## 1. Coherence Distribution",
        "",
        "accel_phase_coherence range: [-1, 1].  Expected: human rigid grip ~0.6–0.9; noise injection ~0.0 ± 0.1.",
        "",
        "| Player | N | min | p10 | median | mean | p90 | max | std |",
        "|--------|---|-----|-----|--------|------|-----|-----|-----|",
    ]
    for p, st in player_stats.items():
        if not st:
            continue
        lines.append(
            f"| {p} | {st['n']} | {_fmt(st['min'])} | {_fmt(st['p10'])} | "
            f"{_fmt(st['median'])} | {_fmt(st['mean'])} | {_fmt(st['p90'])} | "
            f"{_fmt(st['max'])} | {_fmt(st['std'])} |"
        )
    if pop_stats:
        st = pop_stats
        lines.append(
            f"| **ALL** | **{st['n']}** | {_fmt(st['min'])} | {_fmt(st['p10'])} | "
            f"**{_fmt(st['median'])}** | {_fmt(st['mean'])} | {_fmt(st['p90'])} | "
            f"{_fmt(st['max'])} | {_fmt(st['std'])} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 2. L4 Mahalanobis Thresholds",
        "",
        "| | Old (Phase 43) | New (Phase 45) | Delta |",
        "|-|----------------|----------------|-------|",
        f"| Anomaly threshold (mean+3s) | 7.019 | {_fmt(anomaly_threshold)} | {_fmt(anomaly_threshold - 7.019, 3)} |",
        f"| Continuity threshold (mean+2s) | 5.369 | {_fmt(continuity_threshold)} | {_fmt(continuity_threshold - 5.369, 3)} |",
        f"| Population Mahal mean | — | {_fmt(dist_mean)} | — |",
        f"| Population Mahal std | — | {_fmt(dist_std)} | — |",
        f"| Active feature count | 8 (3 zero-var slots) | {len(active_indices)} | — |",
        "",
        f"**Note:** accel_phase_coherence is at index 9. If it is now non-zero-variance,",
        f"the active count increases from 8 → {len(active_indices)}. The threshold change",
        "reflects the updated population distribution with this slot active.",
        "",
        "---",
        "",
        "## 3. Inter-Person Separation Ratio",
        "",
        "| | Old (Phase 43) | New (Phase 45) |",
        "|-|----------------|----------------|",
        f"| Separation ratio | 0.362 | {_fmt(sep_ratio, 3)} |",
        "| Target for reliable identification | >2.0 | >2.0 |",
        "",
        f"{'**Improved**' if sep_ratio > 0.362 else '**Unchanged or regressed**'}: "
        f"ratio moved from 0.362 → {_fmt(sep_ratio, 3)}.",
        ("accel_phase_coherence activates a previously dead feature slot but separation"
         " improvement is bounded by the fundamental P1/P2 similarity — they share the"
         " same tremor oscillator topology. Primary fix path remains post-Phase-17 touchpad recapture."),
        "",
        "---",
        "",
        "## 4. Dashboard RADAR_DATA Score",
        "",
        f"Recommended score for Phase Coherence radar entry: **{radar_score}**",
        f"(= population median {_fmt(pop_stats['median'])} × 100, rounded).",
        "",
        "---",
        "",
        "## 5. Action Items",
        "",
        "- [ ] Review threshold delta — if within ±5% of 7.019/5.369, keep current constants",
        "- [ ] If separation ratio improved beyond 1.0, update whitepaper §8.6 note",
        f"- [ ] Update RADAR_DATA score to {radar_score} in VAPIDashboard.jsx",
        "- [ ] Confirm: after approval, update ANOMALY_THRESHOLD and CONTINUITY_THRESHOLD in source",
    ]

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DOCS_DIR / "phase-coherence-calibration.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nCalibration report written to: {out_path}")


if __name__ == "__main__":
    main()
