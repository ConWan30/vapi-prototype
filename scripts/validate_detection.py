"""
validate_detection.py — VAPI PITL detection rate validator.

Evaluates L5 (TemporalRhythmOracle) and L4 (injection detection) against
session files produced by scripts/capture_session.py or
scripts/generate_adversarial_sessions.py.

Detection logic mirrors the production oracles exactly:
  L5 — controller/temporal_rhythm_oracle.py (CV, entropy, quant_score)
  L4 — bridge/vapi_bridge/dualshock_integration.py (injection: near-zero IMU)

Usage
-----
    python scripts/validate_detection.py
    python scripts/validate_detection.py --sessions-dir sessions
    python scripts/validate_detection.py --sessions-dir sessions --report docs/detection-benchmarks.md
    python scripts/validate_detection.py --human-dir sessions/human --adversarial-dir sessions/adversarial
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

# ---------------------------------------------------------------------------
# L5 — Temporal Rhythm Oracle (mirrors controller/temporal_rhythm_oracle.py)
# ---------------------------------------------------------------------------

_L5_MIN_SAMPLES  = 20
_L5_CV_THRESH    = 0.08
_L5_ENTROPY_THRESH = 1.5
_L5_QUANT_THRESH = 0.55
_L5_TICK_MS      = 16.6667
_L5_SIGNALS_REQ  = 2


_R2_PRESS_THRESH   = 64    # Analog trigger: "pressed" when crossing this from below
_R2_RELEASE_THRESH = 30    # "released" when dropping below this (hysteresis)


def _l5_extract_intervals(reports: list) -> list[float]:
    """
    Extract inter-press intervals from R2 trigger threshold-crossing events.

    Uses hysteresis to handle the DualSense Edge analog trigger ramp
    (0→255 over ~20ms, not an instantaneous transition). A press event fires
    when R2 first crosses _R2_PRESS_THRESH (64) from below; a release fires
    when it drops below _R2_RELEASE_THRESH (30). This correctly handles both:
      - Real hardware: gradual ramp through intermediate values
      - Synthetic sessions: instant 0→255 transitions
    """
    intervals: list[float] = []
    prev_ts: float | None = None
    above_thresh = False
    for r in reports:
        r2 = r.get("features", {}).get("r2_trigger", 0) or 0
        if not above_thresh and r2 >= _R2_PRESS_THRESH:   # rising edge
            above_thresh = True
            if prev_ts is not None:
                dt = r["timestamp_ms"] - prev_ts
                if dt > 0:
                    intervals.append(float(dt))
            prev_ts = float(r["timestamp_ms"])
        elif above_thresh and r2 < _R2_RELEASE_THRESH:    # falling edge (reset)
            above_thresh = False
    return intervals


def _l5_features(intervals: list[float]) -> dict | None:
    """
    Compute CV, entropy, quant_score from inter-press intervals.
    Returns None if insufficient samples.
    """
    if len(intervals) < _L5_MIN_SAMPLES:
        return None

    if _NUMPY:
        arr = __import__("numpy").array(intervals, dtype=float)
        mean = float(arr.mean())
        if mean < 1e-6:
            return None
        cv = float(arr.std()) / mean

        max_val = float(arr.max())
        bins = __import__("numpy").arange(0.0, max_val + 51.0, 50.0)
        counts, _ = __import__("numpy").histogram(arr, bins=bins)
        nonzero = counts[counts > 0]
        probs = nonzero / nonzero.sum()
        entropy = float(-__import__("numpy").sum(probs * __import__("numpy").log2(probs)))

        residue  = arr % _L5_TICK_MS
        devs     = __import__("numpy").minimum(residue, _L5_TICK_MS - residue)
        quant    = float(__import__("numpy").mean(devs < 5.0))
    else:
        # Pure-Python fallback (slower)
        mean = sum(intervals) / len(intervals)
        if mean < 1e-6:
            return None
        variance = sum((x - mean) ** 2 for x in intervals) / len(intervals)
        cv = math.sqrt(variance) / mean

        max_val = max(intervals)
        bin_count = max(1, int((max_val + 50) / 50))
        counts = [0] * bin_count
        for v in intervals:
            idx = min(int(v / 50.0), bin_count - 1)
            counts[idx] += 1
        total = len(intervals)
        entropy = 0.0
        for c in counts:
            if c > 0:
                p = c / total
                entropy -= p * math.log2(p)

        quant_hits = 0
        for v in intervals:
            residue = v % _L5_TICK_MS
            dev = min(residue, _L5_TICK_MS - residue)
            if dev < 5.0:
                quant_hits += 1
        quant = quant_hits / len(intervals)

    signals = (
        int(cv < _L5_CV_THRESH) +
        int(entropy < _L5_ENTROPY_THRESH) +
        int(quant > _L5_QUANT_THRESH)
    )
    return {
        "sample_count": len(intervals),
        "cv":           round(cv, 4),
        "entropy_bits": round(entropy, 3),
        "quant_score":  round(quant, 3),
        "signals":      signals,
        "detected":     signals >= _L5_SIGNALS_REQ,
    }


# ---------------------------------------------------------------------------
# L4 — Injection detection (mirrors dualshock_integration.py L4 block)
# ---------------------------------------------------------------------------

# Real hardware measured ~200 LSB gyro std during active play (from hardware tests).
# Injection threshold: max_gyro_std < 20 LSB while trigger is active.
_L4_INJECTION_GYRO_THRESH = 20.0
_L4_MIN_TRIGGER_REPORTS   = 10


def _l4_injection_features(reports: list) -> dict:
    """
    Compute max per-axis gyro std during periods of active trigger use.
    Injection attack fingerprint: near-zero IMU while trigger active.
    """
    trigger_reports = [
        r for r in reports
        if (r.get("features", {}).get("r2_trigger", 0) or 0) > 50
    ]
    if len(trigger_reports) < _L4_MIN_TRIGGER_REPORTS:
        return {
            "max_gyro_std": None,
            "detected": False,
            "reason": f"insufficient_trigger_reports (n={len(trigger_reports)})",
        }

    axis_stds = []
    for axis in ("gyro_x", "gyro_y", "gyro_z"):
        vals = [float(r["features"].get(axis) or 0) for r in trigger_reports]
        if len(vals) >= 2:
            mean = sum(vals) / len(vals)
            std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
            axis_stds.append(std)

    if not axis_stds:
        return {"max_gyro_std": None, "detected": False, "reason": "no_imu_data"}

    max_std = max(axis_stds)
    return {
        "max_gyro_std":       round(max_std, 2),
        "trigger_reports_n":  len(trigger_reports),
        "detected":           max_std < _L4_INJECTION_GYRO_THRESH,
    }


# ---------------------------------------------------------------------------
# Per-session analysis
# ---------------------------------------------------------------------------

def _analyse_session(session: dict) -> dict:
    """Run L5 + L4 analysis on a single session. Returns analysis dict."""
    reports   = session.get("reports", [])
    meta      = session.get("metadata", {})
    intervals = _l5_extract_intervals(reports)

    l5 = _l5_features(intervals) or {
        "sample_count": len(intervals),
        "detected": False,
        "reason": f"insufficient_samples (n={len(intervals)}, need {_L5_MIN_SAMPLES})",
    }
    l4 = _l4_injection_features(reports)

    return {
        "attack_type":    meta.get("attack_type", "unknown"),
        "skill_tier":     meta.get("skill_tier", ""),
        "report_count":   len(reports),
        "polling_rate_hz": meta.get("polling_rate_hz", 0),
        "l5":             l5,
        "l4_injection":   l4,
        "detected_any":   l5.get("detected", False) or l4.get("detected", False),
    }


# ---------------------------------------------------------------------------
# Aggregate results
# ---------------------------------------------------------------------------

def _aggregate(results: list[dict]) -> dict:
    """Aggregate per-session analysis into detection statistics."""
    by_type: dict[str, list[dict]] = {}
    for r in results:
        at = r.get("attack_type", "unknown")
        by_type.setdefault(at, []).append(r)

    stats: dict[str, dict] = {}
    for at, rs in by_type.items():
        n = len(rs)
        l5_detected  = sum(1 for r in rs if r["l5"].get("detected", False))
        l4_detected  = sum(1 for r in rs if r["l4_injection"].get("detected", False))
        any_detected = sum(1 for r in rs if r["detected_any"])

        l5_cvs      = [r["l5"]["cv"]           for r in rs if "cv"           in r["l5"]]
        l5_entropies= [r["l5"]["entropy_bits"]  for r in rs if "entropy_bits" in r["l5"]]
        l5_quants   = [r["l5"]["quant_score"]   for r in rs if "quant_score"  in r["l5"]]
        l4_stds     = [r["l4_injection"]["max_gyro_std"]
                       for r in rs if r["l4_injection"].get("max_gyro_std") is not None]

        def _avg(lst):
            if not lst:
                return None
            return round(sum(lst) / len(lst), 4)

        stats[at] = {
            "n_sessions":        n,
            "l5_detection_rate": round(l5_detected  / n, 3),
            "l4_detection_rate": round(l4_detected  / n, 3),
            "any_detection_rate":round(any_detected / n, 3),
            "l5_detected":       l5_detected,
            "l4_detected":       l4_detected,
            "l5_cv_mean":        _avg(l5_cvs),
            "l5_entropy_mean":   _avg(l5_entropies),
            "l5_quant_mean":     _avg(l5_quants),
            "l4_gyro_std_mean":  _avg(l4_stds),
        }
    return stats


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _print_report(stats: dict, human_types: set[str]) -> None:
    """Print a formatted detection report to stdout."""
    print()
    print("=" * 70)
    print("VAPI PITL Detection Validation Report")
    print("=" * 70)

    # Adversarial results (detection rate — higher is better)
    adv_types = [t for t in stats if t not in human_types]
    if adv_types:
        print()
        print("ADVERSARIAL SESSIONS (detection rate — higher is better)")
        print("-" * 70)
        print(f"{'Attack Type':<22} {'N':>4} {'L5 Det%':>8} {'L4 Det%':>8} "
              f"{'Any%':>6} {'CV':>7} {'Entropy':>8} {'Quant':>6} {'GyroStd':>8}")
        print(f"{'-'*22} {'-'*4} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*6} {'-'*8}")
        for at in sorted(adv_types):
            s = stats[at]
            print(
                f"{at:<22} {s['n_sessions']:>4} "
                f"{s['l5_detection_rate']*100:>7.1f}% "
                f"{s['l4_detection_rate']*100:>7.1f}% "
                f"{s['any_detection_rate']*100:>5.1f}% "
                f"{str('N/A' if s['l5_cv_mean'] is None else s['l5_cv_mean']):>7} "
                f"{str('N/A' if s['l5_entropy_mean'] is None else s['l5_entropy_mean']):>8} "
                f"{str('N/A' if s['l5_quant_mean'] is None else s['l5_quant_mean']):>6} "
                f"{str('N/A' if s['l4_gyro_std_mean'] is None else s['l4_gyro_std_mean']):>8}"
            )

    # Human baseline results (FP rate — lower is better)
    hum_types = [t for t in stats if t in human_types]
    if hum_types:
        print()
        print("HUMAN BASELINE SESSIONS (false positive rate — lower is better)")
        print("-" * 70)
        print(f"{'Session Type':<22} {'N':>4} {'L5 FP%':>8} {'L4 FP%':>8} "
              f"{'Any FP%':>8} {'CV':>7} {'Entropy':>8} {'Quant':>6}")
        print(f"{'-'*22} {'-'*4} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*6}")
        for ht in sorted(hum_types):
            s = stats[ht]
            print(
                f"{ht:<22} {s['n_sessions']:>4} "
                f"{s['l5_detection_rate']*100:>7.1f}% "
                f"{s['l4_detection_rate']*100:>7.1f}% "
                f"{s['any_detection_rate']*100:>7.1f}% "
                f"{str('N/A' if s['l5_cv_mean'] is None else s['l5_cv_mean']):>7} "
                f"{str('N/A' if s['l5_entropy_mean'] is None else s['l5_entropy_mean']):>8} "
                f"{str('N/A' if s['l5_quant_mean'] is None else s['l5_quant_mean']):>6}"
            )

    print()
    print("L5 Oracle thresholds:  CV < 0.08 | entropy < 1.5 bits | quant > 0.55 (need >=2/3)")
    print("L4 Injection threshold: max_gyro_std < 20 LSB during active trigger use")
    print()


def _markdown_section(stats: dict, human_types: set[str], source_note: str) -> str:
    """Render an updated detection-benchmarks.md section for Table 2a/2b."""
    lines: list[str] = []
    lines.append("")
    lines.append("## 2a. L5 Temporal Rhythm Detection — Real Pipeline Results")
    lines.append("")
    lines.append(f"> {source_note}")
    lines.append("")
    lines.append("| Attack Type | N | L5 Det% | Mean CV | Mean Entropy | Mean Quant |")
    lines.append("|------------|---|---------|---------|--------------|------------|")

    for at in sorted(stats):
        if at in human_types:
            continue
        s = stats[at]
        lines.append(
            f"| {at} | {s['n_sessions']} "
            f"| **{s['l5_detection_rate']*100:.0f}%** "
            f"| {'N/A' if s['l5_cv_mean'] is None else s['l5_cv_mean']} "
            f"| {'N/A' if s['l5_entropy_mean'] is None else s['l5_entropy_mean']} "
            f"| {'N/A' if s['l5_quant_mean'] is None else s['l5_quant_mean']} |"
        )

    lines.append("")
    lines.append("## 2b. L4 Injection Detection — Real Pipeline Results")
    lines.append("")
    lines.append(f"> {source_note}")
    lines.append("")
    lines.append("| Attack Type | N | L4 Det% | Mean GyroStd (LSB) |")
    lines.append("|------------|---|---------|-------------------|")

    for at in sorted(stats):
        if at in human_types:
            continue
        s = stats[at]
        lines.append(
            f"| {at} | {s['n_sessions']} "
            f"| **{s['l4_detection_rate']*100:.0f}%** "
            f"| {'N/A' if s['l4_gyro_std_mean'] is None else s['l4_gyro_std_mean']} |"
        )

    lines.append("")
    lines.append("## 2c. Human Baseline False Positive Rates — Real Pipeline Results")
    lines.append("")
    lines.append(f"> {source_note}")
    lines.append("")
    lines.append("| Session Type | N | L5 FP% | L4 FP% | Mean CV | Mean Entropy |")
    lines.append("|-------------|---|--------|--------|---------|--------------|")

    for ht in sorted(human_types & set(stats.keys())):
        s = stats[ht]
        lines.append(
            f"| {ht} | {s['n_sessions']} "
            f"| **{s['l5_detection_rate']*100:.0f}%** "
            f"| **{s['l4_detection_rate']*100:.0f}%** "
            f"| {'N/A' if s['l5_cv_mean'] is None else s['l5_cv_mean']} "
            f"| {'N/A' if s['l5_entropy_mean'] is None else s['l5_entropy_mean']} |"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session loading
# ---------------------------------------------------------------------------

def _load_sessions(directory: str) -> list[dict]:
    """Load all JSON sessions from a directory."""
    paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    sessions = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "reports" in data and "metadata" in data:
                sessions.append(data)
            else:
                print(f"  WARNING: {p} missing required keys — skipped.")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: Cannot load {p}: {e} — skipped.")
    return sessions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="validate_detection.py",
        description=(
            "Validate VAPI PITL L5 and L4 detection rates against session files. "
            "Produces a formatted report and optional docs/detection-benchmarks.md update."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/validate_detection.py\n"
            "  python scripts/validate_detection.py --sessions-dir sessions\n"
            "  python scripts/validate_detection.py --report docs/detection-benchmarks.md\n"
        ),
    )
    p.add_argument("--sessions-dir", default="sessions",
                   help="Root directory with human/ and adversarial/ subdirs (default: sessions)")
    p.add_argument("--human-dir", default=None,
                   help="Override human sessions directory")
    p.add_argument("--adversarial-dir", default=None,
                   help="Override adversarial sessions directory")
    p.add_argument("--report", default=None,
                   help="Path to detection-benchmarks.md to append results to")
    p.add_argument("--json-output", default=None,
                   help="Save full analysis JSON to this path")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    human_dir = args.human_dir or os.path.join(args.sessions_dir, "human")
    adv_dir   = args.adversarial_dir or os.path.join(args.sessions_dir, "adversarial")

    all_sessions: list[dict] = []
    human_types: set[str]   = set()

    # Load adversarial sessions
    if os.path.isdir(adv_dir):
        adv_sessions = _load_sessions(adv_dir)
        print(f"Loaded {len(adv_sessions)} adversarial session(s) from {adv_dir}/")
        all_sessions.extend(adv_sessions)
    else:
        print(f"WARNING: Adversarial directory not found: {adv_dir}")

    # Load human sessions
    if os.path.isdir(human_dir):
        hum_sessions = _load_sessions(human_dir)
        print(f"Loaded {len(hum_sessions)} human session(s) from {human_dir}/")
        for s in hum_sessions:
            at = s.get("metadata", {}).get("attack_type", "human_baseline")
            human_types.add(at)
        all_sessions.extend(hum_sessions)
    else:
        print(f"WARNING: Human directory not found: {human_dir}")

    if not all_sessions:
        print("ERROR: No sessions found. Run scripts/generate_adversarial_sessions.py first.",
              file=sys.stderr)
        return 1

    # Analyse each session
    print(f"\nAnalysing {len(all_sessions)} session(s)...")
    results = []
    for s in all_sessions:
        r = _analyse_session(s)
        results.append(r)

    # Aggregate
    stats = _aggregate(results)

    # Print report
    _print_report(stats, human_types)

    # Optional: save JSON
    if args.json_output:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_output)), exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump({"stats": stats, "per_session": results}, f, indent=2)
        print(f"Full analysis saved to: {args.json_output}")

    # Optional: append to detection-benchmarks.md
    if args.report:
        source_note = (
            "Data source: synthetic adversarial sessions from "
            "`scripts/generate_adversarial_sessions.py`. "
            "Replace with real hardware captures for production validation."
        )
        md_block = _markdown_section(stats, human_types, source_note)
        with open(args.report, "a", encoding="utf-8") as f:
            f.write("\n\n---\n")
            f.write(md_block)
        print(f"Appended results to: {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
