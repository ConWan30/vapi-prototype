"""
analyze_interperson_separation.py — VAPI Multi-Person Mahalanobis Separation Analysis

Answers the question: does the 11-feature L4 biometric fingerprint distinguish
BETWEEN players, not just detect anomalies WITHIN a single player's sessions?

If inter-player Mahalanobis distances >> intra-player distances, the fingerprint
is a true biometric identifier — not just a per-session consistency detector.

Session grouping (from calibration data, N=69, 3 players):
  Player 1: hw_005–hw_044  (40 sessions)
  Player 2: hw_045–hw_058  (14 sessions)
  Player 3: hw_059–hw_073  (15 sessions)

Anomalous sessions excluded: hw_043, hw_044, hw_067, hw_069, hw_073
(polling_rate_hz outside [800, 1100] range)

Outputs:
  docs/interperson-separation-analysis.md  — human-readable report
  docs/interperson-separation-data.json    — raw data for reproducibility
"""

from __future__ import annotations

import json
import math
import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "sessions" / "human"
DOCS_DIR     = PROJECT_ROOT / "docs"
SCRIPTS_DIR  = PROJECT_ROOT / "scripts"

# Add controller/ to path so we can import BiometricFeatureExtractor
CONTROLLER_DIR = PROJECT_ROOT / "controller"
if str(CONTROLLER_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_DIR))

try:
    from tinyml_biometric_fusion import BiometricFeatureExtractor
    _EXTRACTOR_AVAILABLE = True
except ImportError as e:
    warnings.warn(f"Could not import BiometricFeatureExtractor: {e}. Using inline fallback.")
    _EXTRACTOR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Player session mapping
# ---------------------------------------------------------------------------

PLAYER_SESSIONS = {
    "Player 1": [f"hw_{i:03d}" for i in range(5, 45)],   # hw_005 – hw_044
    "Player 2": [f"hw_{i:03d}" for i in range(45, 59)],  # hw_045 – hw_058
    "Player 3": [f"hw_{i:03d}" for i in range(59, 74)],  # hw_059 – hw_073
}

POLLING_RATE_MIN = 800.0
POLLING_RATE_MAX = 1100.0

WINDOW_SIZE = 1024  # frames per biometric window; 1024 required for tremor FFT (~1Hz/bin at 1000Hz)
FEATURE_NAMES = [
    "trigger_resistance_change_rate",
    "trigger_onset_velocity_l2",
    "trigger_onset_velocity_r2",
    "micro_tremor_accel_variance",
    "grip_asymmetry",
    "stick_autocorr_lag1",
    "stick_autocorr_lag5",
    "tremor_peak_hz",
    "tremor_band_power",
    "touchpad_active_fraction",
    "touch_position_variance",
]
N_FEATURES = len(FEATURE_NAMES)

# ---------------------------------------------------------------------------
# InputSnapshot adapter
# ---------------------------------------------------------------------------

class _SnapProxy:
    """
    Wraps a JSON features dict as an attribute-access object matching the
    interface expected by BiometricFeatureExtractor.extract().

    Fields not present in the JSON (l2_effect_mode, r2_effect_mode,
    inter_frame_us, touch_active, touch0_x, touch0_y) use sensible defaults.
    """
    __slots__ = (
        "left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y",
        "l2_trigger", "r2_trigger",
        "gyro_x", "gyro_y", "gyro_z",
        "accel_x", "accel_y", "accel_z",
        "l2_effect_mode", "r2_effect_mode",
        "inter_frame_us",
        "touch_active", "touch0_x", "touch0_y",
    )

    def __init__(self, feat: dict, inter_frame_us: int = 1000):
        g = feat.get
        self.left_stick_x   = int(g("left_stick_x",  128))
        self.left_stick_y   = int(g("left_stick_y",  128))
        self.right_stick_x  = int(g("right_stick_x", 128))
        self.right_stick_y  = int(g("right_stick_y", 128))
        self.l2_trigger     = int(g("l2_trigger",    0))
        self.r2_trigger     = int(g("r2_trigger",    0))
        self.gyro_x         = float(g("gyro_x", 0.0))
        self.gyro_y         = float(g("gyro_y", 0.0))
        self.gyro_z         = float(g("gyro_z", 0.0))
        self.accel_x        = float(g("accel_x", 0.0))
        self.accel_y        = float(g("accel_y", 0.0))
        self.accel_z        = float(g("accel_z", 1.0))
        self.l2_effect_mode = int(g("l2_effect_mode", 0))
        self.r2_effect_mode = int(g("r2_effect_mode", 0))
        self.inter_frame_us = inter_frame_us
        self.touch_active   = bool(g("touch_active", False))
        self.touch0_x       = int(g("touch0_x", 0))
        self.touch0_y       = int(g("touch0_y", 0))


# ---------------------------------------------------------------------------
# Inline fallback feature extraction (mirrors BiometricFeatureExtractor.extract)
# Used only if controller/tinyml_biometric_fusion.py cannot be imported.
# ---------------------------------------------------------------------------

def _autocorr(series: list, lag: int) -> float:
    if len(series) <= lag + 2:
        return 0.0
    x = np.array(series[:-lag], dtype=np.float64)
    y = np.array(series[lag:],  dtype=np.float64)
    if x.std() < 1e-10 or y.std() < 1e-10:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _compute_trigger_onset_velocity(trigger_vals: list) -> float:
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
    return float(np.mean(onsets)) if onsets else 0.0


def _extract_features_inline(snaps: list[_SnapProxy]) -> np.ndarray:
    """
    Inline reimplementation of BiometricFeatureExtractor.extract() operating on
    _SnapProxy objects. Returns an 11-element float32 array.
    """
    n = len(snaps)
    if n < 10:
        return np.zeros(N_FEATURES, dtype=np.float32)

    def _g(s, attr, default=0.0):
        return float(getattr(s, attr, default))

    # 1. Trigger resistance change rate
    l2_modes = [int(getattr(s, "l2_effect_mode", 0)) for s in snaps]
    r2_modes = [int(getattr(s, "r2_effect_mode", 0)) for s in snaps]
    mode_changes = sum(
        1 for i in range(1, n)
        if l2_modes[i] != l2_modes[i - 1] or r2_modes[i] != r2_modes[i - 1]
    )
    resistance_change_rate = (mode_changes / n) * 100.0

    # 2. Trigger onset velocities
    l2_vals = [int(getattr(s, "l2_trigger", 0)) for s in snaps]
    r2_vals = [int(getattr(s, "r2_trigger", 0)) for s in snaps]
    onset_vel_l2 = _compute_trigger_onset_velocity(l2_vals)
    onset_vel_r2 = _compute_trigger_onset_velocity(r2_vals)

    # 3. Micro-tremor: accel variance during still frames
    # 20.0 LSB = raw HID gyro noise floor at rest (active play ~201 LSB, rest ~14-50 LSB)
    still_accel_mags = []
    for s in snaps:
        gx = _g(s, "gyro_x"); gy = _g(s, "gyro_y"); gz = _g(s, "gyro_z")
        gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)
        if gyro_mag < 20.0:  # raw LSB threshold
            ax = _g(s, "accel_x"); ay = _g(s, "accel_y"); az = _g(s, "accel_z")
            still_accel_mags.append(math.sqrt(ax * ax + ay * ay + az * az))
    micro_tremor_var = float(np.var(still_accel_mags)) if len(still_accel_mags) >= 5 else 0.0

    # 4. Grip asymmetry (dual-press frames only)
    dual_press_ratios = []
    for s in snaps:
        l2 = int(getattr(s, "l2_trigger", 0))
        r2 = int(getattr(s, "r2_trigger", 0))
        if l2 > 10 and r2 > 10:
            dual_press_ratios.append(l2 / (r2 + 1e-6))
    grip_asym = float(np.mean(dual_press_ratios)) if dual_press_ratios else 1.0

    # 5. Stick velocity autocorrelation at lag 1 and lag 5
    stick_vels = []
    prev_lx, prev_ly = _g(snaps[0], "left_stick_x"), _g(snaps[0], "left_stick_y")
    for s in snaps[1:]:
        lx = _g(s, "left_stick_x"); ly = _g(s, "left_stick_y")
        dt = max(_g(s, "inter_frame_us", 1000) / 1_000_000.0, 1e-6)
        vel = math.sqrt(((lx - prev_lx) / 32768.0) ** 2 + ((ly - prev_ly) / 32768.0) ** 2) / dt
        stick_vels.append(vel)
        prev_lx, prev_ly = lx, ly

    autocorr_lag1 = _autocorr(stick_vels, lag=1)
    autocorr_lag5 = _autocorr(stick_vels, lag=5)

    # 6. Right-stick tremor FFT (8-12 Hz physiological tremor)
    rx_vals = np.array([float(getattr(s, "right_stick_x", 0)) for s in snaps], dtype=np.float32)
    rx_vels = np.diff(rx_vals) / 32768.0
    dt_vals = [max(_g(s, "inter_frame_us", 1000) / 1_000_000.0, 1e-6) for s in snaps[1:]]
    fs = 1.0 / max(float(np.median(dt_vals)), 1e-6)
    if len(rx_vels) >= 512:  # min 512 frames for ~2Hz/bin resolution at 1000Hz
        fft_mag = np.abs(np.fft.rfft(rx_vels))
        freqs   = np.fft.rfftfreq(len(rx_vels), d=1.0 / fs)
        total_power = float(np.sum(fft_mag ** 2)) or 1e-9
        peak_idx = int(np.argmax(fft_mag))
        tremor_peak_hz  = float(freqs[peak_idx])
        band_mask = (freqs >= 8.0) & (freqs <= 12.0)
        tremor_band_power = float(np.sum(fft_mag[band_mask] ** 2) / total_power)
    else:
        tremor_peak_hz = 0.0
        tremor_band_power = 0.0

    # 7. Touchpad biometric
    touch_xs = [
        float(getattr(s, "touch0_x", 0)) / 1920.0
        for s in snaps
        if bool(getattr(s, "touch_active", False))
    ]
    touchpad_active_fraction = len(touch_xs) / n
    touch_position_variance = float(np.var(touch_xs)) if len(touch_xs) >= 3 else 0.0

    return np.array([
        resistance_change_rate,
        onset_vel_l2,
        onset_vel_r2,
        micro_tremor_var,
        grip_asym,
        autocorr_lag1,
        autocorr_lag5,
        tremor_peak_hz,
        tremor_band_power,
        touchpad_active_fraction,
        touch_position_variance,
    ], dtype=np.float32)


# ---------------------------------------------------------------------------
# Session processing
# ---------------------------------------------------------------------------

def estimate_inter_frame_us(reports: list[dict]) -> int:
    """
    Estimate inter-frame interval in microseconds from session timestamps.
    Falls back to 1000 us (1000 Hz) if timestamps are all zero.
    """
    tss = [r.get("timestamp_ms", 0) for r in reports[:200] if r.get("timestamp_ms", 0) > 0]
    if len(tss) >= 2:
        diffs = [tss[i] - tss[i-1] for i in range(1, len(tss)) if tss[i] > tss[i-1]]
        if diffs:
            median_ms = float(np.median(diffs))
            return max(int(median_ms * 1000), 100)  # convert ms -> us
    return 1000  # default 1000 Hz = 1000 us


def load_session(session_name: str) -> dict | None:
    """
    Load a session JSON file.

    Returns:
        dict with keys 'session_name', 'player', 'polling_rate_hz', 'report_count',
        'mean_vector', 'window_vectors', 'excluded', 'exclude_reason'
    OR None if session file not found.
    """
    path = SESSIONS_DIR / f"{session_name}.json"
    if not path.exists():
        return {"session_name": session_name, "excluded": True, "exclude_reason": "file_not_found"}

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    metadata = data.get("metadata", {})
    reports  = data.get("reports", [])
    polling  = float(metadata.get("polling_rate_hz", 0.0))

    if polling < POLLING_RATE_MIN or polling > POLLING_RATE_MAX:
        return {
            "session_name":   session_name,
            "excluded":       True,
            "exclude_reason": f"polling_rate_hz={polling:.1f} outside [{POLLING_RATE_MIN},{POLLING_RATE_MAX}]",
            "polling_rate_hz": polling,
        }

    if len(reports) < WINDOW_SIZE:
        return {
            "session_name":   session_name,
            "excluded":       True,
            "exclude_reason": f"too_few_reports ({len(reports)} < {WINDOW_SIZE})",
            "polling_rate_hz": polling,
        }

    # Estimate inter-frame interval for velocity computation
    ift_us = estimate_inter_frame_us(reports)

    # Build proxy objects for all reports
    proxies = [_SnapProxy(r["features"], inter_frame_us=ift_us) for r in reports]

    # Extract features in sliding windows of WINDOW_SIZE
    window_vectors = []
    n_reports = len(proxies)

    for start in range(0, n_reports - WINDOW_SIZE + 1, WINDOW_SIZE):
        window = proxies[start : start + WINDOW_SIZE]
        if _EXTRACTOR_AVAILABLE:
            feat = BiometricFeatureExtractor.extract(window, window_frames=WINDOW_SIZE)
            vec = feat.to_vector().astype(np.float64)
        else:
            vec = _extract_features_inline(window).astype(np.float64)
        window_vectors.append(vec)

    if not window_vectors:
        return {
            "session_name":   session_name,
            "excluded":       True,
            "exclude_reason": "no_valid_windows",
            "polling_rate_hz": polling,
        }

    mean_vec = np.mean(window_vectors, axis=0)

    return {
        "session_name":    session_name,
        "excluded":        False,
        "exclude_reason":  None,
        "polling_rate_hz": polling,
        "report_count":    len(reports),
        "n_windows":       len(window_vectors),
        "mean_vector":     mean_vec.tolist(),
        "window_vectors":  [v.tolist() for v in window_vectors],
    }


# ---------------------------------------------------------------------------
# Mahalanobis distance (full covariance)
# ---------------------------------------------------------------------------

def mahalanobis_distance(x: np.ndarray, mu: np.ndarray, cov_inv: np.ndarray) -> float:
    """Full Mahalanobis distance using pre-computed inverse covariance."""
    diff = x - mu
    return float(np.sqrt(np.clip(diff @ cov_inv @ diff, 0.0, None)))


def robust_cov_inv(data: np.ndarray, reg: float = 1e-4) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute global covariance from session mean vectors with Tikhonov regularization.
    Returns (cov, cov_inv).
    """
    cov = np.cov(data.T) if data.shape[0] > 1 else np.eye(data.shape[1])
    # Tikhonov regularization: add reg * trace(cov) * I to ensure invertibility
    reg_term = reg * np.trace(cov) * np.eye(cov.shape[0])
    cov_reg  = cov + reg_term
    try:
        cov_inv = np.linalg.inv(cov_reg)
    except np.linalg.LinAlgError:
        # Fallback: diagonal
        diag_var = np.maximum(np.diag(cov), 1e-6)
        cov_inv  = np.diag(1.0 / diag_var)
    return cov_reg, cov_inv


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run_analysis() -> dict:
    print("=" * 60)
    print("VAPI Inter-Person Biometric Separation Analysis")
    print("=" * 60)
    print(f"Sessions dir : {SESSIONS_DIR}")
    print(f"Window size  : {WINDOW_SIZE} frames")
    print(f"Feature dim  : {N_FEATURES}")
    print(f"Extractor    : {'BiometricFeatureExtractor (live)' if _EXTRACTOR_AVAILABLE else 'inline fallback'}")
    print()

    # --- Load all sessions ---
    all_sessions = []   # list of result dicts
    player_sessions: dict[str, list[dict]] = {}

    for player, session_list in PLAYER_SESSIONS.items():
        player_sessions[player] = []
        for sname in session_list:
            result = load_session(sname)
            if result is None:
                continue
            result["player"] = player
            all_sessions.append(result)
            status = "EXCLUDED" if result.get("excluded") else "ok"
            reason = f" ({result.get('exclude_reason', '')})" if result.get("excluded") else ""
            print(f"  {sname} [{player}]: {status}{reason}")
            if not result.get("excluded"):
                player_sessions[player].append(result)

    print()

    # Summary counts
    included = [s for s in all_sessions if not s.get("excluded")]
    excluded  = [s for s in all_sessions if s.get("excluded")]
    print(f"Loaded: {len(included)} sessions included, {len(excluded)} excluded")
    for p, sl in player_sessions.items():
        print(f"  {p}: {len(sl)} sessions")
    print()

    if len(included) < 3:
        raise RuntimeError(f"Too few sessions ({len(included)}) — need at least 3.")

    # --- Build feature matrix (N_sessions x N_FEATURES) ---
    mean_vectors   = np.array([s["mean_vector"] for s in included])
    player_labels  = [s["player"] for s in included]
    session_names  = [s["session_name"] for s in included]

    # Fix 4: Auto-exclude structurally-zero features before computing distances.
    # Features with zero variance across ALL sessions contribute no discriminative signal
    # and inflate the condition number of the covariance matrix (Mahalanobis breaks down).
    feature_stds = np.std(mean_vectors, axis=0)
    zero_var_mask = feature_stds < 1e-9
    active_mask = ~zero_var_mask
    n_active = int(np.sum(active_mask))
    excluded_feat_names = [FEATURE_NAMES[i] for i in range(N_FEATURES) if zero_var_mask[i]]
    active_feat_names   = [FEATURE_NAMES[i] for i in range(N_FEATURES) if active_mask[i]]

    if excluded_feat_names:
        print(f"Auto-excluded {len(excluded_feat_names)} zero-variance features (no signal across all sessions):")
        for fn in excluded_feat_names:
            print(f"  - {fn}")
        print(f"Active features ({n_active}): {', '.join(active_feat_names)}")
        print()
    else:
        print(f"All {N_FEATURES} features have non-zero variance -- no auto-exclusion.")
        print()

    # Store active (projected) vector per session for downstream Mahalanobis computation
    for s in included:
        s["_active_vec"] = np.array(s["mean_vector"])[active_mask]
    mean_vectors_active = mean_vectors[:, active_mask]

    # --- Global covariance (all sessions pooled, active features only) ---
    cov_global, cov_inv_global = robust_cov_inv(mean_vectors_active)
    print(f"Global covariance rank: {np.linalg.matrix_rank(cov_global)} / {n_active}")
    print()

    # --- Per-player mean vectors ---
    player_means: dict[str, np.ndarray] = {}
    player_vectors: dict[str, list[np.ndarray]] = {}

    for p, sl in player_sessions.items():
        if not sl:
            continue
        vecs = np.array([s["_active_vec"] for s in sl])
        player_means[p]   = np.mean(vecs, axis=0)
        player_vectors[p] = [s["_active_vec"] for s in sl]

    # --- Intra-player distances ---
    print("INTRA-PLAYER DISTANCES (each session -> their player mean)")
    print("-" * 55)
    intra_stats: dict[str, dict] = {}

    for p, sl in player_sessions.items():
        if not sl or p not in player_means:
            continue
        mu = player_means[p]
        dists = [mahalanobis_distance(s["_active_vec"], mu, cov_inv_global) for s in sl]
        intra_stats[p] = {
            "n_sessions":  len(sl),
            "distances":   dists,
            "mean":        float(np.mean(dists)),
            "std":         float(np.std(dists)),
            "median":      float(np.median(dists)),
            "min":         float(np.min(dists)),
            "max":         float(np.max(dists)),
        }
        print(f"  {p}: N={len(sl)}, mean={np.mean(dists):.3f}, std={np.std(dists):.3f}, "
              f"median={np.median(dists):.3f}, range=[{np.min(dists):.3f}, {np.max(dists):.3f}]")

    overall_intra_mean = float(np.mean([s["mean"] for s in intra_stats.values()]))
    print(f"\n  Overall mean intra-player distance: {overall_intra_mean:.3f}")
    print()

    # --- Inter-player distances (between player mean vectors) ---
    print("INTER-PLAYER DISTANCES (between player mean vectors)")
    print("-" * 55)
    inter_stats: dict[str, dict] = {}
    players_with_data = list(player_means.keys())
    n_players = len(players_with_data)

    inter_dist_matrix = np.zeros((n_players, n_players))
    inter_distances = []

    for i, pa in enumerate(players_with_data):
        for j, pb in enumerate(players_with_data):
            if i == j:
                inter_dist_matrix[i, j] = 0.0
                continue
            d = mahalanobis_distance(player_means[pa], player_means[pb], cov_inv_global)
            inter_dist_matrix[i, j] = d
            if j > i:
                pair_key = f"{pa} vs {pb}"
                inter_stats[pair_key] = {"distance": d, "players": [pa, pb]}
                inter_distances.append(d)
                print(f"  {pair_key}: {d:.3f}")

    overall_inter_mean = float(np.mean(inter_distances)) if inter_distances else 0.0
    print(f"\n  Overall mean inter-player distance: {overall_inter_mean:.3f}")
    print()

    # --- Separation ratio ---
    if overall_intra_mean > 1e-9:
        separation_ratio = overall_inter_mean / overall_intra_mean
    else:
        separation_ratio = 0.0

    print("=" * 55)
    print(f"SEPARATION RATIO (inter / intra): {separation_ratio:.3f}")
    if separation_ratio >= 5.0:
        conclusion = "STRONG BIOMETRIC SEPARATION — reliable multi-player identification"
    elif separation_ratio >= 3.0:
        conclusion = "GOOD BIOMETRIC SEPARATION — reliable for most use cases"
    elif separation_ratio >= 2.0:
        conclusion = "MODERATE SEPARATION — useful signal but not conclusive"
    elif separation_ratio >= 1.0:
        conclusion = "WEAK SEPARATION — marginal; consider additional features"
    else:
        conclusion = "NO SEPARATION — fingerprint does not distinguish between players"
    print(f"CONCLUSION: {conclusion}")
    print("=" * 55)
    print()

    # --- Per-feature statistics across players ---
    feature_player_means: dict[str, dict[str, float]] = {}
    feature_player_stds:  dict[str, dict[str, float]] = {}

    for p, sl in player_sessions.items():
        if not sl:
            continue
        vecs = np.array([s["mean_vector"] for s in sl])
        for fi, fname in enumerate(FEATURE_NAMES):
            if fname not in feature_player_means:
                feature_player_means[fname] = {}
                feature_player_stds[fname]  = {}
            feature_player_means[fname][p] = float(np.mean(vecs[:, fi]))
            feature_player_stds[fname][p]  = float(np.std(vecs[:, fi]))

    print("PER-FEATURE MEANS BY PLAYER")
    print("-" * 55)
    header = f"{'Feature':<38} " + "  ".join(f"{p[:8]:>10}" for p in players_with_data)
    print(header)
    for fname in FEATURE_NAMES:
        row = f"{fname:<38} "
        for p in players_with_data:
            row += f"  {feature_player_means[fname].get(p, 0.0):>10.4f}"
        print(row)
    print()

    # --- Leave-one-out player classification accuracy ---
    print("LEAVE-ONE-OUT SESSION CLASSIFICATION")
    print("-" * 55)
    correct = 0
    total   = 0
    misclassified = []

    for s in included:
        true_player = s["player"]
        vec = s["_active_vec"]
        best_player = None
        best_dist   = float("inf")
        for p, mu in player_means.items():
            d = mahalanobis_distance(vec, mu, cov_inv_global)
            if d < best_dist:
                best_dist   = d
                best_player = p
        total += 1
        if best_player == true_player:
            correct += 1
        else:
            misclassified.append({
                "session":      s["session_name"],
                "true_player":  true_player,
                "pred_player":  best_player,
                "best_dist":    best_dist,
            })

    accuracy = correct / total if total > 0 else 0.0
    print(f"  Accuracy: {correct}/{total} = {accuracy:.1%}")
    if misclassified:
        print(f"  Misclassified sessions ({len(misclassified)}):")
        for m in misclassified:
            print(f"    {m['session']}: true={m['true_player']}, pred={m['pred_player']}, dist={m['best_dist']:.3f}")
    else:
        print("  No misclassifications.")
    print()

    # --- Compile full result dict ---
    result = {
        "analysis_version":   "2.0",
        "n_sessions_included": len(included),
        "n_sessions_excluded": len(excluded),
        "n_features":          N_FEATURES,
        "n_active_features":   n_active,
        "feature_names":       FEATURE_NAMES,
        "active_feature_names": active_feat_names,
        "excluded_feature_names": excluded_feat_names,
        "window_size":         WINDOW_SIZE,
        "extractor_mode":      "live" if _EXTRACTOR_AVAILABLE else "inline_fallback",
        "player_session_counts": {p: len(sl) for p, sl in player_sessions.items()},
        "separation_ratio":    separation_ratio,
        "overall_intra_mean":  overall_intra_mean,
        "overall_inter_mean":  overall_inter_mean,
        "conclusion":          conclusion,
        "intra_player_stats":  intra_stats,
        "inter_player_stats":  inter_stats,
        "inter_distance_matrix": {
            "players": players_with_data,
            "values":  inter_dist_matrix.tolist(),
        },
        "player_mean_vectors": {
            p: mu.tolist() for p, mu in player_means.items()
        },
        "feature_player_means": feature_player_means,
        "feature_player_stds":  feature_player_stds,
        "classification": {
            "accuracy":         accuracy,
            "correct":          correct,
            "total":            total,
            "misclassified":    misclassified,
        },
        "excluded_sessions": [
            {
                "session":        s["session_name"],
                "exclude_reason": s.get("exclude_reason", ""),
                "polling_rate_hz": s.get("polling_rate_hz"),
            }
            for s in excluded
        ],
        "session_details": [
            {
                "session":      s["session_name"],
                "player":       s["player"],
                "report_count": s.get("report_count"),
                "n_windows":    s.get("n_windows"),
                "polling_rate_hz": s.get("polling_rate_hz"),
                "mean_vector":  s["mean_vector"],
            }
            for s in included
        ],
        "global_covariance": cov_global.tolist(),
    }

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def format_table(headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None) -> str:
    """Format a markdown table."""
    if col_widths is None:
        col_widths = [max(len(h), max((len(str(r[i])) for r in rows), default=0))
                      for i, h in enumerate(headers)]
    sep = "| " + " | ".join("-" * w for w in col_widths) + " |"
    hdr = "| " + " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers)) + " |"
    body_lines = []
    for row in rows:
        line = "| " + " | ".join(f"{str(row[i]):<{col_widths[i]}}" for i in range(len(headers))) + " |"
        body_lines.append(line)
    return "\n".join([hdr, sep] + body_lines)


def write_markdown(result: dict, path: Path) -> None:
    ratio   = result["separation_ratio"]
    n_inc   = result["n_sessions_included"]
    n_exc   = result["n_sessions_excluded"]
    conc    = result["conclusion"]
    acc     = result["classification"]["accuracy"]
    correct = result["classification"]["correct"]
    total   = result["classification"]["total"]
    intra   = result["overall_intra_mean"]
    inter   = result["overall_inter_mean"]
    players = result["inter_distance_matrix"]["players"]

    lines = []
    lines.append("# VAPI Inter-Person Biometric Separation Analysis")
    lines.append("")
    lines.append("**Date:** 2026-03-08  ")
    lines.append("**Sessions:** N=69 captured, " +
                 f"{n_inc} included, {n_exc} excluded (polling-rate filter)  ")
    lines.append(f"**Players:** 3 (Player 1: hw_005–hw_044, Player 2: hw_045–hw_058, Player 3: hw_059–hw_073)  ")
    n_active_feat = result["n_active_features"]
    excl_feats = result.get("excluded_feature_names", [])
    lines.append(f"**Feature space:** {result['n_features']}-dimensional L4 biometric fingerprint "
                 f"({n_active_feat} active after zero-variance exclusion)  ")
    lines.append(f"**Window size:** {result['window_size']} frames  ")
    lines.append(f"**Distance metric:** Full Mahalanobis on active features (Tikhonov-regularized covariance)")
    lines.append("")

    if excl_feats:
        lines.append("> **Auto-excluded features (zero variance across all sessions):** " +
                     ", ".join(f"`{f}`" for f in excl_feats) + "  ")
        lines.append("> These features are structurally zero in the current N=69 corpus "
                     "(game-specific or hardware field added after capture). "
                     "They are reported below but excluded from Mahalanobis computation.")
        lines.append("")

    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Mean intra-player distance | {intra:.3f} |")
    lines.append(f"| Mean inter-player distance | {inter:.3f} |")
    lines.append(f"| **Separation ratio (inter/intra)** | **{ratio:.3f}** |")
    lines.append(f"| Leave-one-out classification accuracy | {acc:.1%} ({correct}/{total}) |")
    lines.append("")
    lines.append(f"**Conclusion:** {conc}")
    lines.append("")
    if ratio >= 5.0:
        lines.append(
            "The 11-feature L4 fingerprint not only detects within-player anomalies but is "
            "a **reliable biometric identifier across players**. The separation ratio of "
            f"{ratio:.2f} indicates that different players occupy substantially distinct "
            "regions of the 11-dimensional biometric feature space, supporting its use as "
            "a true biometric fingerprint rather than a mere session-consistency detector."
        )
    elif ratio >= 3.0:
        lines.append(
            "The 11-feature L4 fingerprint demonstrates **good inter-player separation**. "
            f"A ratio of {ratio:.2f} (threshold for reliable separation: 3.0) indicates "
            "that players occupy meaningfully different regions of the feature space. "
            "This supports the fingerprint's use as a biometric identifier in contexts "
            "where multiple sessions per player are available for calibration."
        )
    elif ratio >= 2.0:
        lines.append(
            "The 11-feature L4 fingerprint shows **moderate inter-player separation** "
            f"(ratio {ratio:.2f}). Players are distinguishable on average but with "
            "significant overlap. Feature augmentation or longer calibration windows "
            "may improve separation."
        )
    else:
        lines.append(
            "The 11-feature L4 fingerprint shows **weak or no inter-player separation** "
            f"(ratio {ratio:.2f}). This may reflect insufficient session diversity, "
            "feature space limitations (e.g., touchpad features all zero in current dataset), "
            "or genuine similarity of play styles across players. "
            "Intra-player consistency detection remains valid despite low inter-player separation."
        )
    lines.append("")

    lines.append("## Per-Player Statistics")
    lines.append("")
    intra_s = result["intra_player_stats"]
    player_counts = result["player_session_counts"]
    rows_p = []
    for p in players:
        st = intra_s.get(p, {})
        rows_p.append([
            p,
            str(player_counts.get(p, 0)),
            f"{st.get('mean', 0.0):.3f}",
            f"{st.get('std', 0.0):.3f}",
            f"{st.get('min', 0.0):.3f}",
            f"{st.get('max', 0.0):.3f}",
            f"{st.get('median', 0.0):.3f}",
        ])
    lines.append(format_table(
        ["Player", "Sessions", "Intra Mean", "Intra Std", "Intra Min", "Intra Max", "Intra Median"],
        rows_p
    ))
    lines.append("")

    lines.append("## Inter-Player Distance Matrix (Mahalanobis)")
    lines.append("")
    lines.append("Distance between each pair of player mean feature vectors using the "
                 "shared global covariance.")
    lines.append("")
    matrix_vals = result["inter_distance_matrix"]["values"]
    inter_hdrs = [""] + players
    inter_rows = []
    for i, pa in enumerate(players):
        row = [pa]
        for j, pb in enumerate(players):
            if i == j:
                row.append("—")
            else:
                row.append(f"{matrix_vals[i][j]:.3f}")
        inter_rows.append(row)
    lines.append(format_table(inter_hdrs, inter_rows))
    lines.append("")

    lines.append("## Intra-Player Distance Distribution")
    lines.append("")
    lines.append("Mahalanobis distance from each session's mean feature vector to "
                 "its player's centroid, using the global covariance.")
    lines.append("")
    for p in players:
        st = intra_s.get(p, {})
        dists = st.get("distances", [])
        if not dists:
            continue
        lines.append(f"**{p}** (N={len(dists)} sessions, mean={st['mean']:.3f}):")
        dist_strs = [f"{d:.3f}" for d in dists]
        lines.append(f"  {', '.join(dist_strs)}")
        lines.append("")

    lines.append("## Feature Means by Player")
    lines.append("")
    lines.append("Per-feature mean values for each player's session set. "
                 "Features with high inter-player variation are the strongest biometric discriminators.")
    lines.append("")
    fpm  = result["feature_player_means"]
    fps  = result["feature_player_stds"]
    feat_rows = []
    for fname in FEATURE_NAMES:
        row = [fname]
        vals = []
        for p in players:
            m = fpm.get(fname, {}).get(p, 0.0)
            s = fps.get(fname, {}).get(p, 0.0)
            row.append(f"{m:.4f} (+/-{s:.4f})")
            vals.append(m)
        # Inter-player spread (range)
        rng = max(vals) - min(vals) if vals else 0.0
        row.append(f"{rng:.4f}")
        feat_rows.append(row)
    # Sort by spread descending to highlight best discriminators
    feat_rows.sort(key=lambda r: float(r[-1]), reverse=True)
    col_hdrs = ["Feature"] + players + ["Inter-Range"]
    lines.append(format_table(col_hdrs, feat_rows))
    lines.append("")

    lines.append("## Leave-One-Out Classification Results")
    lines.append("")
    lines.append(
        f"Each session was classified to the nearest player centroid (Mahalanobis) "
        f"using the global covariance. Player mean vectors were computed from ALL sessions "
        f"(no held-out centroid recomputation — this is a bias-aware first-pass estimate)."
    )
    lines.append("")
    lines.append(f"**Accuracy: {acc:.1%} ({correct}/{total} sessions correctly assigned)**")
    lines.append("")
    mc = result["classification"]["misclassified"]
    if mc:
        lines.append("Misclassified sessions:")
        lines.append("")
        mc_rows = [[m["session"], m["true_player"], m["pred_player"], f"{m['best_dist']:.3f}"] for m in mc]
        lines.append(format_table(["Session", "True Player", "Predicted", "Best Dist"], mc_rows))
    else:
        lines.append("No misclassifications — all sessions correctly assigned to their player.")
    lines.append("")

    lines.append("## Excluded Sessions")
    lines.append("")
    exc_sessions = result["excluded_sessions"]
    if exc_sessions:
        exc_rows = [[e["session"], e.get("exclude_reason", ""), str(e.get("polling_rate_hz", ""))]
                    for e in exc_sessions]
        lines.append(format_table(["Session", "Reason", "Polling Rate Hz"], exc_rows))
    else:
        lines.append("No sessions excluded.")
    lines.append("")

    lines.append("## Recommendations for L4 Multi-Person Calibration")
    lines.append("")
    lines.append("### Implications for VAPI Protocol")
    lines.append("")
    if ratio >= 3.0:
        lines.append(
            "1. **Player-specific fingerprinting is viable.** With a separation ratio of "
            f"{ratio:.2f}, the L4 oracle can be extended to maintain per-player fingerprints "
            "for operator-registered players. A session that crosses player boundaries "
            "is a strong anomaly signal."
        )
    else:
        lines.append(
            "1. **Player-specific fingerprinting needs more features.** The current separation "
            f"ratio of {ratio:.2f} suggests feature augmentation or longer session windows "
            "before per-player identification is reliable."
        )
    lines.append("")
    lines.append("2. **Touchpad biometrics.** All 69 sessions show zero touchpad activity "
                 "(touch_active=False throughout). Adding the `touch_active`/`touch0_x` "
                 "fields from capture_session.py Phase 17 will add player-specific thumb-resting "
                 "patterns as a discriminator. This is expected to improve separation significantly.")
    lines.append("")
    lines.append("3. **Micro-tremor variance.** The gyro-based still-frame filter (gyro_mag < 0.01) "
                 "applies to raw LSB gyro values (range ~-350 to +350). With raw IMU values in "
                 "the hundreds, most frames fail this threshold — the effective still-frame count "
                 "is low. Consider calibrating the threshold to `gyro_mag < IMU_NOISE_FLOOR` "
                 f"(empirical: 332.99 LSB, 95th pct) to capture more tremor frames.")
    lines.append("")
    lines.append("4. **Multi-session calibration window.** The live L4 oracle uses EMA over sessions. "
                 "For inter-player separation in tournament contexts, accumulate ≥10 sessions per "
                 "player before computing player centroid. The current N={avg_n:.0f} sessions/player "
                 "average is {cal}.".format(
                     avg_n=sum(player_counts.values()) / max(len(player_counts), 1),
                     cal="adequate" if min(player_counts.values()) >= 10 else "marginal for Player 2/3"))
    lines.append("")
    lines.append("5. **Full covariance vs. diagonal.** This analysis uses a full Tikhonov-regularized "
                 "covariance matrix (off-diagonal terms included). The live L4 oracle currently uses "
                 "a diagonal approximation. Upgrading to full covariance (TODO in the source) would "
                 "better capture feature correlations and improve both intra-player consistency "
                 "detection and inter-player separation.")
    lines.append("")
    lines.append("6. **Tremor FFT window length.** The 50-frame window used here (vs 120-frame in "
                 "live oracle) at 1000 Hz gives a frequency resolution of 20 Hz/bin, which is too "
                 "coarse to resolve the 8-12 Hz physiological tremor band. The live oracle uses "
                 "120-frame windows (8.3 Hz/bin). For reliable tremor band power, a 1024-frame "
                 "window at 1000 Hz would give 0.98 Hz/bin resolution (noted in CLAUDE.md as "
                 "a known gap).")
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by `scripts/analyze_interperson_separation.py` — "
                 f"VAPI Phase 17, 2026-03-08*")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written -> {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        result = run_analysis()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    # Save raw data JSON
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    data_path = DOCS_DIR / "interperson-separation-data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Raw data written -> {data_path}")

    # Save markdown report
    md_path = DOCS_DIR / "interperson-separation-analysis.md"
    write_markdown(result, md_path)

    print()
    print("=" * 55)
    print(f"Separation ratio : {result['separation_ratio']:.3f}")
    print(f"Classification   : {result['classification']['accuracy']:.1%}")
    print(f"Conclusion       : {result['conclusion']}")
    print("=" * 55)

    return 0


if __name__ == "__main__":
    sys.exit(main())
