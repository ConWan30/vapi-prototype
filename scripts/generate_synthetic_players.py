"""
generate_synthetic_players.py — Synthetic inter-player separation validator.

Generates 5 virtual players with distinct biometric profiles and 20 sessions
each (100 total), then computes inter-player Mahalanobis separation to validate
that L4 transplant detection (catching one player's sessions submitted under
another's device ID) is feasible before real multi-player hardware capture.

Separation metric:
    ratio = mean_inter_player_distance / mean_intra_player_distance

    ratio >> 1  → players are separable → L4 can detect transplants
    ratio ≈ 1   → players overlap → L4 blind to transplants

Also reports LOO (leave-one-session-out) nearest-centroid classification
accuracy to give an upper-bound on transplant detection rate.

Usage:
    python scripts/generate_synthetic_players.py [--seed N] [--output PATH]

Output:
    JSON summary + printed report.  If --output is given, also writes a JSON
    file compatible with analyze_interperson_separation.py --input-json.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import pathlib
import random
import sys

import numpy as np

# ---------------------------------------------------------------------------
# Add controller/ to path for tinyml_biometric_fusion
# ---------------------------------------------------------------------------
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "controller"))

try:
    from tinyml_biometric_fusion import (
        BiometricFeatureExtractor,
        BiometricFeatureFrame,
        BiometricFusionClassifier,
        CALIBRATION_WINDOW_FRAMES,
    )
    _REAL_EXTRACTOR = True
except ImportError as e:
    print(f"WARNING: Could not import tinyml_biometric_fusion: {e}")
    print("         Falling back to numpy-only feature vector generation.")
    _REAL_EXTRACTOR = False
    CALIBRATION_WINDOW_FRAMES = 1024

# ---------------------------------------------------------------------------
# Player biometric profiles
# Each dict defines (mean, std) for each of the 11 BiometricFeatureFrame fields.
# Values are chosen to reflect plausible real-world variation between players.
# ---------------------------------------------------------------------------

# Field order must match BiometricFeatureFrame.to_vector()
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

# Players: (mean_vector, std_vector)
# Variation between players is the key metric; intra-player noise is the baseline.
PLAYER_PROFILES: list[dict] = [
    {
        "name": "P1_aggressive",
        "description": "Fast trigger pulls, low grip asymmetry, low tremor",
        # means
        "means": np.array([
            0.0,   # trigger_resistance_change_rate (static-trigger game)
            0.18,  # trigger_onset_velocity_l2 (fast onset → low value)
            0.20,  # trigger_onset_velocity_r2
            800.0, # micro_tremor_accel_variance
            1.05,  # grip_asymmetry (balanced)
            0.62,  # stick_autocorr_lag1
            0.28,  # stick_autocorr_lag5
            1.2,   # tremor_peak_hz (low tremor — steady hands)
            0.04,  # tremor_band_power
            0.0,   # touchpad_active_fraction
            0.0,   # touch_position_variance
        ], dtype=np.float64),
        # intra-player std per feature (session-to-session noise)
        "stds": np.array([
            0.0, 0.03, 0.03, 120.0, 0.08, 0.05, 0.04, 0.3, 0.01, 0.0, 0.0
        ], dtype=np.float64),
    },
    {
        "name": "P2_casual",
        "description": "Slow trigger onset, high grip asymmetry, moderate tremor",
        "means": np.array([
            0.0, 0.52, 0.61, 1200.0, 1.45, 0.48, 0.19, 8.7, 0.22, 0.0, 0.0
        ], dtype=np.float64),
        "stds": np.array([
            0.0, 0.06, 0.07, 200.0, 0.12, 0.06, 0.05, 0.8, 0.03, 0.0, 0.0
        ], dtype=np.float64),
    },
    {
        "name": "P3_shaky",
        "description": "High physiological tremor, low R2 use, distinct grip ratio",
        "means": np.array([
            0.0, 0.35, 0.44, 2800.0, 0.78, 0.55, 0.24, 9.8, 0.38, 0.0, 0.0
        ], dtype=np.float64),
        "stds": np.array([
            0.0, 0.05, 0.05, 400.0, 0.10, 0.06, 0.05, 1.1, 0.04, 0.0, 0.0
        ], dtype=np.float64),
    },
    {
        "name": "P4_precise",
        "description": "Very consistent trigger velocity, high autocorrelation, minimal tremor",
        "means": np.array([
            0.0, 0.25, 0.27, 550.0, 1.18, 0.74, 0.41, 0.8, 0.02, 0.0, 0.0
        ], dtype=np.float64),
        "stds": np.array([
            0.0, 0.02, 0.02, 80.0, 0.06, 0.03, 0.03, 0.2, 0.01, 0.0, 0.0
        ], dtype=np.float64),
    },
    {
        "name": "P5_touchpad_user",
        "description": "Active touchpad use, left-dominant grip, moderate tremor",
        "means": np.array([
            0.0, 0.40, 0.31, 1000.0, 0.62, 0.51, 0.22, 7.3, 0.15, 0.18, 0.04
        ], dtype=np.float64),
        "stds": np.array([
            0.0, 0.05, 0.04, 150.0, 0.09, 0.05, 0.04, 0.6, 0.02, 0.03, 0.01
        ], dtype=np.float64),
    },
]

N_PLAYERS = len(PLAYER_PROFILES)
N_SESSIONS = 20  # sessions per player


# ---------------------------------------------------------------------------
# Synthetic session generation
# ---------------------------------------------------------------------------

def _generate_sessions(rng: np.random.Generator, profile: dict) -> np.ndarray:
    """
    Generate N_SESSIONS feature vectors for a player profile by sampling
    from N(mean, std) for each feature, clipping to non-negative values.

    Returns shape (N_SESSIONS, N_FEATURES).
    """
    sessions = []
    for _ in range(N_SESSIONS):
        vec = rng.normal(profile["means"], profile["stds"])
        vec = np.clip(vec, 0.0, None)  # biometric features are non-negative
        sessions.append(vec)
    return np.array(sessions, dtype=np.float64)


# ---------------------------------------------------------------------------
# Mahalanobis distance with diagonal covariance (mirrors L4 production path)
# ---------------------------------------------------------------------------

def _mahalanobis_diag(x: np.ndarray, mean: np.ndarray, var: np.ndarray) -> float:
    """Diagonal Mahalanobis distance, excluding near-zero variance features."""
    active = var > 1e-4
    if not np.any(active):
        return 0.0
    diff = x[active] - mean[active]
    return float(np.sqrt(np.sum(diff ** 2 / np.maximum(var[active], 1e-6))))


def _compute_fingerprint(sessions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compute EMA fingerprint (mean, var) from N_SESSIONS vectors."""
    alpha = 0.1
    mean = sessions[0].copy()
    var  = np.ones(N_FEATURES, dtype=np.float64) * 0.1
    for v in sessions[1:]:
        delta = v - mean
        mean += alpha * delta
        var   = (1 - alpha) * var + alpha * delta ** 2
    return mean, var


# ---------------------------------------------------------------------------
# Separation analysis
# ---------------------------------------------------------------------------

def run_separation_analysis(rng: np.random.Generator) -> dict:
    """
    Generate synthetic sessions and compute inter-player vs intra-player
    Mahalanobis distance ratio.

    Returns a dict with all statistics.
    """
    # Generate all sessions
    all_sessions: list[np.ndarray] = []  # shape (N_PLAYERS, N_SESSIONS, N_FEATURES)
    for prof in PLAYER_PROFILES:
        sessions = _generate_sessions(rng, prof)
        all_sessions.append(sessions)

    # Compute per-player fingerprints from first 10 sessions (warm-up half)
    fingerprints: list[tuple] = []
    for sessions in all_sessions:
        mean, var = _compute_fingerprint(sessions[:10])
        fingerprints.append((mean, var))

    # --- Intra-player distances ---
    # For each player, measure the remaining 10 sessions against their fingerprint.
    intra_distances: list[float] = []
    per_player_intra: list[list[float]] = []
    for p_idx, sessions in enumerate(all_sessions):
        mean, var = fingerprints[p_idx]
        dists = [_mahalanobis_diag(s, mean, var) for s in sessions[10:]]
        per_player_intra.append(dists)
        intra_distances.extend(dists)

    # --- Inter-player distances ---
    # For each pair (p_ref, p_other), measure p_other's sessions against p_ref's fingerprint.
    inter_distances: list[float] = []
    per_pair_inter: dict[tuple, list[float]] = {}
    for p_ref in range(N_PLAYERS):
        mean_ref, var_ref = fingerprints[p_ref]
        for p_other in range(N_PLAYERS):
            if p_other == p_ref:
                continue
            dists = [_mahalanobis_diag(s, mean_ref, var_ref)
                     for s in all_sessions[p_other][10:]]
            per_pair_inter[(p_ref, p_other)] = dists
            inter_distances.extend(dists)

    mean_intra = float(np.mean(intra_distances))
    mean_inter = float(np.mean(inter_distances))
    ratio       = mean_inter / max(mean_intra, 1e-9)

    # --- LOO nearest-centroid classification ---
    # For each session i of player p: remove it, classify using centroid distances.
    correct = 0
    total   = 0
    for p_true in range(N_PLAYERS):
        sessions = all_sessions[p_true]
        mean_p, var_p = fingerprints[p_true]
        for s in sessions[10:]:
            # Distance to each player's fingerprint
            dists_to = [
                _mahalanobis_diag(s, fingerprints[q][0], fingerprints[q][1])
                for q in range(N_PLAYERS)
            ]
            predicted = int(np.argmin(dists_to))
            if predicted == p_true:
                correct += 1
            total += 1

    loo_accuracy = correct / max(total, 1)

    return {
        "n_players": N_PLAYERS,
        "n_sessions_per_player": N_SESSIONS,
        "n_features": N_FEATURES,
        "mean_intra_distance": round(mean_intra, 4),
        "mean_inter_distance": round(mean_inter, 4),
        "separation_ratio": round(ratio, 4),
        "loo_classification_accuracy": round(loo_accuracy, 4),
        "loo_classification_accuracy_pct": round(loo_accuracy * 100, 1),
        "chance_level_pct": round(100.0 / N_PLAYERS, 1),
        "players": [
            {
                "name": prof["name"],
                "description": prof["description"],
                "mean_intra_distance": round(float(np.mean(per_player_intra[i])), 4),
                "std_intra_distance":  round(float(np.std(per_player_intra[i])), 4),
            }
            for i, prof in enumerate(PLAYER_PROFILES)
        ],
        "inter_player_matrix": {
            f"P{p_ref+1}_ref_vs_P{p_other+1}": round(float(np.mean(dists)), 4)
            for (p_ref, p_other), dists in per_pair_inter.items()
        },
    }


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

_VERDICT_THRESHOLD_RATIO = 2.0    # ratio > 2 → separable
_VERDICT_THRESHOLD_LOO   = 0.60   # LOO > 60% → viable

def _print_report(result: dict) -> None:
    sep = "=" * 65
    print(sep)
    print("VAPI Synthetic Inter-Player Separation Analysis")
    print(f"  Players: {result['n_players']}  |  "
          f"Sessions/player: {result['n_sessions_per_player']}  |  "
          f"Features: {result['n_features']}")
    print(sep)

    print("\n--- Per-player intra-distances (test sessions vs own fingerprint) ---")
    for p in result["players"]:
        print(f"  {p['name']:<28} mean={p['mean_intra_distance']:.4f}  "
              f"std={p['std_intra_distance']:.4f}  ({p['description']})")

    print(f"\n--- Global distances ---")
    print(f"  Mean intra-player distance : {result['mean_intra_distance']:.4f}")
    print(f"  Mean inter-player distance : {result['mean_inter_distance']:.4f}")
    print(f"  Separation ratio           : {result['separation_ratio']:.4f}  "
          f"(target > {_VERDICT_THRESHOLD_RATIO})")

    print(f"\n--- LOO nearest-centroid classification ---")
    print(f"  Accuracy: {result['loo_classification_accuracy_pct']:.1f}%  "
          f"(chance = {result['chance_level_pct']:.1f}%, "
          f"target > {_VERDICT_THRESHOLD_LOO * 100:.0f}%)")

    # Verdict
    ratio_ok = result["separation_ratio"] >= _VERDICT_THRESHOLD_RATIO
    loo_ok   = result["loo_classification_accuracy"] >= _VERDICT_THRESHOLD_LOO
    print(f"\n--- Verdict ---")
    op_ratio = ">=" if ratio_ok else "< "
    op_loo   = ">=" if loo_ok   else "< "
    print(f"  Separation ratio : {'PASS' if ratio_ok else 'FAIL'} "
          f"({result['separation_ratio']:.3f} {op_ratio} {_VERDICT_THRESHOLD_RATIO})")
    print(f"  LOO accuracy     : {'PASS' if loo_ok else 'FAIL'} "
          f"({result['loo_classification_accuracy_pct']:.1f}% "
          f"{op_loo} {_VERDICT_THRESHOLD_LOO * 100:.0f}%)")
    if ratio_ok and loo_ok:
        print("\n  L4 transplant detection FEASIBLE — proceed with real multi-player capture.")
    elif ratio_ok or loo_ok:
        print("\n  MARGINAL — profiles are partially separable. Collect more sessions.")
    else:
        print("\n  FAIL — profiles overlap. Revisit feature selection before hardware capture.")
    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--output", type=str, default=None,
                        help="Optional: write JSON results to this path")
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    result = run_separation_analysis(rng)
    _print_report(result)

    if args.output:
        out_path = pathlib.Path(args.output)
        out_path.write_text(json.dumps(result, indent=2))
        print(f"\nResults written to: {out_path}")


if __name__ == "__main__":
    main()
