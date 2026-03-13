"""
accel_entropy_calibration.py — Phase 46 Calibration Script

Computes per-player entropy distribution for the accel_magnitude_spectral_entropy
feature (index 9, replaces touchpad_active_fraction) across N=69 calibration sessions.

Outputs:
  docs/accel-entropy-calibration.md  — distribution table, thresholds, separation ratio

Usage:
    python scripts/accel_entropy_calibration.py sessions/human/hw_*.json

CALIBRATION PAUSE GATE:
    This script prints all calibration metrics and halts. Do NOT update
    ANOMALY_THRESHOLD / CONTINUITY_THRESHOLD until the user reviews the output.
    If population entropy std > 2.0 bits, a low-discriminative warning is printed.
"""

from __future__ import annotations

import json
import math
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_DIR = PROJECT_ROOT / "controller"
DOCS_DIR = PROJECT_ROOT / "docs"

if str(CONTROLLER_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_DIR))

try:
    from tinyml_biometric_fusion import (
        BiometricFeatureExtractor,
        CALIBRATION_WINDOW_FRAMES,
    )
    _EXTRACTOR_AVAILABLE = True
    WINDOW_SIZE = CALIBRATION_WINDOW_FRAMES  # 1024
except ImportError as e:
    warnings.warn(f"Cannot import BiometricFeatureExtractor: {e}. Inline fallback active.")
    _EXTRACTOR_AVAILABLE = False
    WINDOW_SIZE = 1024

POLLING_RATE_MIN = 800.0
POLLING_RATE_MAX = 1100.0

PLAYER_MAP: dict[str, str] = {}
for _i in range(5, 45):
    PLAYER_MAP[f"hw_{_i:03d}"] = "Player 1"
for _i in range(45, 59):
    PLAYER_MAP[f"hw_{_i:03d}"] = "Player 2"
for _i in range(59, 74):
    PLAYER_MAP[f"hw_{_i:03d}"] = "Player 3"

LOW_DISCRIMINATIVE_STD = 2.0  # bits — flag if population std exceeds this


# ---------------------------------------------------------------------------
# Snap proxy
# ---------------------------------------------------------------------------

class _SnapProxy:
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
# Inline entropy computation (mirrors extract() ring path)
# ---------------------------------------------------------------------------

def _compute_entropy_inline(snaps: list[_SnapProxy]) -> float:
    """Compute accel_magnitude_spectral_entropy from a list of snap proxies."""
    if len(snaps) < 1024:
        return 0.0
    ax = np.array([float(getattr(s, "accel_x", 0.0)) for s in snaps], dtype=np.float64)
    ay = np.array([float(getattr(s, "accel_y", 0.0)) for s in snaps], dtype=np.float64)
    az = np.array([float(getattr(s, "accel_z", 0.0)) for s in snaps], dtype=np.float64)
    mag = np.sqrt(ax * ax + ay * ay + az * az)
    if float(np.var(mag)) < 4.0:
        return 0.0
    dc = mag - float(np.mean(mag))
    power = np.abs(np.fft.rfft(dc)) ** 2
    total = float(np.sum(power))
    if total < 1e-12:
        return 0.0
    p = power / total
    p = p[p > 1e-12]
    return float(-np.sum(p * np.log2(p)))


def _estimate_inter_frame_us(reports: list[dict]) -> int:
    tss = [r.get("timestamp_ms", 0) for r in reports[:200] if r.get("timestamp_ms", 0) > 0]
    if len(tss) >= 2:
        diffs = [tss[i] - tss[i-1] for i in range(1, len(tss)) if tss[i] > tss[i-1]]
        if diffs:
            return max(int(float(np.median(diffs)) * 1000), 100)
    return 1000


# ---------------------------------------------------------------------------
# Session processing
# ---------------------------------------------------------------------------

def process_session(path: Path) -> dict[str, Any]:
    """Load a session file and extract per-window entropy values."""
    sname = path.stem
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"session": sname, "excluded": True, "reason": f"load_error: {e}"}

    metadata = data.get("metadata", {})
    reports  = data.get("reports", [])
    polling  = float(metadata.get("polling_rate_hz", 0.0))

    if polling < POLLING_RATE_MIN or polling > POLLING_RATE_MAX:
        return {"session": sname, "excluded": True,
                "reason": f"polling_rate={polling:.1f} outside [{POLLING_RATE_MIN},{POLLING_RATE_MAX}]"}

    if len(reports) < WINDOW_SIZE:
        return {"session": sname, "excluded": True,
                "reason": f"too_few_reports ({len(reports)} < {WINDOW_SIZE})"}

    ift_us = _estimate_inter_frame_us(reports)
    proxies = [_SnapProxy(r["features"], inter_frame_us=ift_us) for r in reports]

    entropy_values = []
    n_windows = 0

    for start in range(0, len(proxies) - WINDOW_SIZE + 1, WINDOW_SIZE):
        window = proxies[start : start + WINDOW_SIZE]
        n_windows += 1
        if _EXTRACTOR_AVAILABLE:
            ext = BiometricFeatureExtractor()
            feat = ext.extract(window, window_frames=WINDOW_SIZE)
            entropy_val = feat.accel_magnitude_spectral_entropy
        else:
            entropy_val = _compute_entropy_inline(window)
        entropy_values.append(entropy_val)

    if not entropy_values:
        return {"session": sname, "excluded": True, "reason": "no_valid_windows"}

    return {
        "session":        sname,
        "excluded":       False,
        "reason":         None,
        "player":         PLAYER_MAP.get(sname, "Unknown"),
        "polling_hz":     polling,
        "n_reports":      len(reports),
        "n_windows":      n_windows,
        "entropy_values": entropy_values,
        "mean_entropy":   float(np.mean(entropy_values)),
        "std_entropy":    float(np.std(entropy_values)),
    }


# ---------------------------------------------------------------------------
# Mahalanobis threshold computation (replicates threshold_calibrator logic)
# ---------------------------------------------------------------------------

def compute_thresholds(all_entropy_values: list[float]) -> dict[str, float]:
    """Compute L4 Mahalanobis thresholds from the full entropy distribution."""
    arr = np.array(all_entropy_values, dtype=np.float64)
    mean = float(np.mean(arr))
    std  = float(np.std(arr))
    return {
        "mean":             mean,
        "std":              std,
        "anomaly":          mean + 3.0 * std,   # mean+3sigma
        "continuity":       mean + 2.0 * std,   # mean+2sigma
        "p10":              float(np.percentile(arr, 10)),
        "p90":              float(np.percentile(arr, 90)),
        "min":              float(np.min(arr)),
        "max":              float(np.max(arr)),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 46 accel entropy calibration.")
    parser.add_argument("sessions", nargs="+", help="Session JSON files (glob: sessions/human/hw_*.json)")
    args = parser.parse_args()

    paths = sorted(Path(p) for p in args.sessions if Path(p).exists())
    if not paths:
        print("ERROR: No session files found.", file=sys.stderr)
        return 1

    print("=" * 65)
    print("VAPI Phase 46 — accel_magnitude_spectral_entropy Calibration")
    print("=" * 65)
    print(f"Session files : {len(paths)}")
    print(f"Window size   : {WINDOW_SIZE} frames (1024 -> 513 FFT bins at 0.977 Hz/bin)")
    print(f"Extractor     : {'BiometricFeatureExtractor (live)' if _EXTRACTOR_AVAILABLE else 'inline fallback'}")
    print()

    results = []
    for path in paths:
        r = process_session(path)
        results.append(r)
        if r["excluded"]:
            print(f"  EXCLUDED  {r['session']}: {r['reason']}")
        else:
            mean_e = r["mean_entropy"]
            zero_flag = " [ZERO]" if mean_e == 0.0 else ""
            print(f"  OK        {r['session']} [{r['player']}]  entropy={mean_e:.3f}{zero_flag}  n_win={r['n_windows']}")

    included = [r for r in results if not r["excluded"]]
    excluded  = [r for r in results if r["excluded"]]

    print()
    print(f"Included: {len(included)} | Excluded: {len(excluded)}")
    print()

    if len(included) < 3:
        print("ERROR: Too few sessions to calibrate.", file=sys.stderr)
        return 1

    # Per-player distribution
    player_data: dict[str, list[float]] = {}
    for r in included:
        p = r["player"]
        if p not in player_data:
            player_data[p] = []
        player_data[p].extend(r["entropy_values"])

    all_entropy = [v for r in included for v in r["entropy_values"]]
    zero_count  = sum(1 for v in all_entropy if v == 0.0)
    zero_frac   = zero_count / len(all_entropy) if all_entropy else 0.0
    max_entropy = math.log2(513)  # theoretical max for 1024-sample rfft (513 bins)

    print("PER-PLAYER ENTROPY DISTRIBUTION")
    print("-" * 65)
    print(f"  {'Player':<10} {'N_vals':>6} {'min':>6} {'p10':>6} {'median':>7} {'mean':>6} {'p90':>6} {'max':>6} {'std':>6}")
    print(f"  {'-'*10} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")

    player_stats = {}
    for p in sorted(player_data):
        vals = np.array(player_data[p])
        st = {
            "n": len(vals), "min": float(np.min(vals)), "p10": float(np.percentile(vals, 10)),
            "median": float(np.median(vals)), "mean": float(np.mean(vals)),
            "p90": float(np.percentile(vals, 90)), "max": float(np.max(vals)),
            "std": float(np.std(vals)),
        }
        player_stats[p] = st
        print(f"  {p:<10} {st['n']:>6} {st['min']:>6.3f} {st['p10']:>6.3f} "
              f"{st['median']:>7.3f} {st['mean']:>6.3f} {st['p90']:>6.3f} "
              f"{st['max']:>6.3f} {st['std']:>6.3f}")

    all_arr = np.array(all_entropy)
    global_stats = {
        "n": len(all_arr), "min": float(np.min(all_arr)), "p10": float(np.percentile(all_arr, 10)),
        "median": float(np.median(all_arr)), "mean": float(np.mean(all_arr)),
        "p90": float(np.percentile(all_arr, 90)), "max": float(np.max(all_arr)),
        "std": float(np.std(all_arr)),
    }
    print(f"  {'GLOBAL':<10} {global_stats['n']:>6} {global_stats['min']:>6.3f} "
          f"{global_stats['p10']:>6.3f} {global_stats['median']:>7.3f} "
          f"{global_stats['mean']:>6.3f} {global_stats['p90']:>6.3f} "
          f"{global_stats['max']:>6.3f} {global_stats['std']:>6.3f}")
    print()
    print(f"  Theoretical max entropy = log2(513) = {max_entropy:.3f} bits")
    print(f"  Zero-fraction (warm-up guard): {zero_count}/{len(all_entropy)} = {zero_frac:.1%}")
    print(f"  (Expected 0% at WINDOW_SIZE=1024 — warm-up requires exactly 1024 samples)")
    print()

    thresholds = compute_thresholds(all_entropy)
    print("L4 MAHALANOBIS THRESHOLD CANDIDATES (this feature only)")
    print("-" * 65)
    print(f"  mean+3sigma (anomaly)   = {thresholds['mean']:.3f} + 3 * {thresholds['std']:.3f} = {thresholds['anomaly']:.3f}")
    print(f"  mean+2sigma (continuity)= {thresholds['mean']:.3f} + 2 * {thresholds['std']:.3f} = {thresholds['continuity']:.3f}")
    print()
    print("  NOTE: Full L4 threshold re-derivation (all 11 features jointly) requires")
    print("  running scripts/threshold_calibrator.py against N=69 post-replacement.")
    print()

    # Inter-person separation (simplified: per-player mean entropy)
    p_means = {p: player_stats[p]["mean"] for p in sorted(player_stats)}
    p_means_arr = np.array(list(p_means.values()))
    inter_spread = float(np.max(p_means_arr) - np.min(p_means_arr))
    pop_std = global_stats["std"]

    radar_score = round(global_stats["median"] / max_entropy * 100)

    print("INTER-PERSON DISCRIMINABILITY")
    print("-" * 65)
    print(f"  Per-player mean entropy: " + ", ".join(f"{p}={v:.3f}" for p, v in p_means.items()))
    print(f"  Inter-player spread (max-min of player means): {inter_spread:.3f} bits")
    print(f"  Population std (all windows, all players): {pop_std:.3f} bits")
    print(f"  Normalized radar score (median / max * 100): {radar_score}")
    print()

    # Gates
    print("=" * 65)
    print("CALIBRATION PAUSE — review output above before proceeding")
    print("=" * 65)
    print()

    low_disc = pop_std > LOW_DISCRIMINATIVE_STD
    if low_disc:
        print(f"WARNING: population std = {pop_std:.3f} bits > {LOW_DISCRIMINATIVE_STD} bits threshold.")
        print("         Feature may be LOW-DISCRIMINATIVE. Confirm before updating thresholds.")
        print()

    if zero_frac > 0.05:
        print(f"WARNING: {zero_frac:.1%} of windows returned 0.0 (warm-up guard or static injection).")
        print(f"         Expected ~0% at WINDOW_SIZE={WINDOW_SIZE}. Investigate session quality.")
        print()

    print("Next steps (after review):")
    print("  1. Run scripts/threshold_calibrator.py sessions/human/hw_*.json")
    print("     to derive full 11-feature L4 thresholds with new feature at index 9.")
    print("  2. Update ANOMALY_THRESHOLD / CONTINUITY_THRESHOLD in tinyml_biometric_fusion.py")
    print("     and calibration_profile.json after user approval.")
    print("  3. Update CLAUDE.md L4 structurally-zero feature list (remove touchpad_active_fraction).")
    print()

    # Write markdown report
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = DOCS_DIR / "accel-entropy-calibration.md"
    _write_markdown(
        md_path=md_path,
        included=included,
        excluded=excluded,
        player_stats=player_stats,
        global_stats=global_stats,
        thresholds=thresholds,
        zero_count=zero_count,
        zero_frac=zero_frac,
        max_entropy=max_entropy,
        inter_spread=inter_spread,
        pop_std=pop_std,
        radar_score=radar_score,
        low_discriminative=low_disc,
        p_means=p_means,
    )
    print(f"Report written -> {md_path}")
    print()

    if low_disc:
        return 2  # non-zero exit to signal gate failed
    return 0


def _write_markdown(
    md_path: Path,
    included: list, excluded: list,
    player_stats: dict, global_stats: dict,
    thresholds: dict,
    zero_count: int, zero_frac: float,
    max_entropy: float, inter_spread: float, pop_std: float,
    radar_score: int, low_discriminative: bool,
    p_means: dict,
) -> None:
    lines = [
        "# Phase 46 — accel_magnitude_spectral_entropy Calibration",
        "",
        "**Date:** 2026-03-13  ",
        f"**Sessions:** {len(included)} included, {len(excluded)} excluded  ",
        f"**Window size:** 1024 frames (513 FFT bins at 0.977 Hz/bin @ 1000 Hz)  ",
        "**Feature:** `accel_magnitude_spectral_entropy` (index 9)  ",
        "**Replaces:** `touchpad_active_fraction` (structurally zero across N=69)  ",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Sessions included | {len(included)} |",
        f"| Zero-fraction (warm-up guard) | {zero_frac:.1%} ({zero_count} windows) |",
        f"| Global mean entropy | {global_stats['mean']:.3f} bits |",
        f"| Global std entropy | {global_stats['std']:.3f} bits |",
        f"| Theoretical max (log2(513)) | {max_entropy:.3f} bits |",
        f"| Inter-player spread | {inter_spread:.3f} bits |",
        f"| Radar score (median/max*100) | {radar_score} |",
        f"| Low-discriminative warning | {'YES — std > 2.0 bits' if low_discriminative else 'No'} |",
        "",
        "## Per-Player Entropy Distribution",
        "",
        "| Player | N | min | p10 | median | mean | p90 | max | std |",
        "|--------|---|-----|-----|--------|------|-----|-----|-----|",
    ]
    for p, st in sorted(player_stats.items()):
        lines.append(f"| {p} | {st['n']} | {st['min']:.3f} | {st['p10']:.3f} | "
                     f"{st['median']:.3f} | {st['mean']:.3f} | {st['p90']:.3f} | "
                     f"{st['max']:.3f} | {st['std']:.3f} |")
    st = global_stats
    lines.append(f"| **GLOBAL** | {st['n']} | {st['min']:.3f} | {st['p10']:.3f} | "
                 f"{st['median']:.3f} | {st['mean']:.3f} | {st['p90']:.3f} | "
                 f"{st['max']:.3f} | {st['std']:.3f} |")
    lines += [
        "",
        "## L4 Threshold Candidates",
        "",
        "| Threshold | Formula | Value |",
        "|-----------|---------|-------|",
        f"| Anomaly (mean+3s) | {global_stats['mean']:.3f} + 3 * {global_stats['std']:.3f} | {thresholds['anomaly']:.3f} |",
        f"| Continuity (mean+2s) | {global_stats['mean']:.3f} + 2 * {global_stats['std']:.3f} | {thresholds['continuity']:.3f} |",
        "",
        "> **NOTE:** These are per-feature thresholds for reference only. Full 11-feature",
        "> L4 thresholds must be re-derived by running `scripts/threshold_calibrator.py`.",
        "",
        "## Inter-Person Discriminability",
        "",
        "| Player | Mean Entropy (bits) |",
        "|--------|---------------------|",
    ]
    for p, v in sorted(p_means.items()):
        lines.append(f"| {p} | {v:.3f} |")
    lines += [
        "",
        f"Inter-player spread (max - min of player means): **{inter_spread:.3f} bits**",
        "",
    ]
    if low_discriminative:
        lines += [
            "**WARNING:** Population std > 2.0 bits — this feature may be low-discriminative.",
            "Confirm separation is meaningful before updating production thresholds.",
            "",
        ]
    lines += [
        "## Excluded Sessions",
        "",
        "| Session | Reason |",
        "|---------|--------|",
    ]
    for r in excluded:
        lines.append(f"| {r['session']} | {r['reason']} |")
    lines += [
        "",
        "---",
        "*Generated by `scripts/accel_entropy_calibration.py` — VAPI Phase 46, 2026-03-13*",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
