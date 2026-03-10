"""
l6_threshold_calibrator.py — Empirical L6 active challenge-response threshold calibrator.

Reads session files produced by scripts/capture_session.py (with L6 enabled) and
computes recommended per-profile threshold values for the L6 challenge-response stack.

HARDWARE REQUIREMENT
--------------------
This script requires real L6 session data captured with a DualShock Edge controller
while L6_CHALLENGES_ENABLED=true in the bridge configuration. The session files must
include an `l6_responses` list inside the `metadata` block. Pre-Phase-17 sessions
and any sessions captured without L6 enabled will not contribute data.

MINIMUM DATA REQUIREMENT
-------------------------
N >= 50 valid responses PER PROFILE are required for production thresholds.
Fewer than 10 responses per profile will emit a warning and produce unreliable estimates.
Thresholds derived from N < 10 sessions must not be used in production.

SESSION JSON SCHEMA (l6_responses)
------------------------------------
Each session file's `metadata.l6_responses` is a list of response dicts:

  {
    "profile_id":    int,    # which TriggerChallengeProfile was active (0-7)
    "onset_ms":      float,  # ms from challenge to first ADC delta > ONSET_DELTA_LSB
    "peak_delta":    float,  # max |r2_post - r2_pre_mean| during response window
    "settle_ms":     float,  # ms from onset to trigger returning to within 10% of pre_mean
    "grip_variance": float,  # variance of accel_magnitude during response window
    "valid":         bool    # False if window expired with no response detected
  }

Only entries with valid=True and peak_delta >= 5 are used for calibration.

CALIBRATED THRESHOLDS (per profile_id)
----------------------------------------
  onset_threshold_ms    — human 3-sigma upper bound of onset_ms
  settle_threshold_ms   — human 3-sigma upper bound of settle_ms
  grip_variance_floor   — human mean - 2-sigma (responses below this suspected injection)

OUTPUT
------
  l6_calibration_profile.json  — thresholds by profile_id + raw stats
  Prints CHALLENGE_PROFILES replacement Python literal to stdout for copy-paste into
  bridge/controller/l6_challenge_profiles.py.

USAGE
-----
  python scripts/l6_threshold_calibrator.py sessions/hw_*.json
  python scripts/l6_threshold_calibrator.py sessions/hw_*.json --output l6_profile_v2.json
  python scripts/l6_threshold_calibrator.py --help

SEE ALSO
--------
  bridge/controller/l6_challenge_profiles.py — CHALLENGE_PROFILES definition
  bridge/vapi_bridge/l6_response_analyzer.py — L6ResponseMetrics, L6ResponseAnalyzer
  tests/hardware/test_l6_human_response.py   — hardware validation tests
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Profile metadata (mirrors l6_challenge_profiles.py — no import needed)
# ---------------------------------------------------------------------------

_PROFILE_NAMES: dict[int, str] = {
    0: "BASELINE_OFF",
    1: "RIGID_LIGHT",
    2: "RIGID_HEAVY",
    3: "PULSE_SLOW",
    4: "PULSE_FAST",
    5: "RIGID_ASYM",
    6: "PULSE_ASYM",
    7: "RIGID_SEQUENTIAL",
}

_MIN_PEAK_DELTA = 5.0   # minimum peak_delta to count as a real response (mirrors L6ResponseAnalyzer)
_MIN_SESSIONS_WARN = 10
_MIN_SESSIONS_PRODUCTION = 50


# ---------------------------------------------------------------------------
# Statistics helpers (no numpy dependency — mirrors threshold_calibrator.py)
# ---------------------------------------------------------------------------

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    sv = sorted(vals)
    idx = (len(sv) - 1) * pct / 100.0
    lo, hi = int(idx), min(int(idx) + 1, len(sv) - 1)
    frac = idx - lo
    return sv[lo] * (1.0 - frac) + sv[hi] * frac


# ---------------------------------------------------------------------------
# Session loading
# ---------------------------------------------------------------------------

def load_l6_responses(path: str) -> list[dict]:
    """
    Load L6 response dicts from a session JSON file.

    Returns a (possibly empty) list of response dicts from
    session["metadata"]["l6_responses"]. Returns [] if the key is absent
    (pre-L6 sessions silently contribute no data).
    """
    try:
        with open(path, encoding="utf-8") as f:
            session = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  WARNING: skipping {path}: {exc}", file=sys.stderr)
        return []

    responses = session.get("metadata", {}).get("l6_responses", [])
    if not isinstance(responses, list):
        return []
    return responses


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def calibrate(session_files: list[str]) -> dict:
    """
    Load all session files, accumulate valid L6 responses by profile_id, and
    compute per-profile threshold statistics.

    Returns a dict with keys:
      "profiles": {profile_id_str: {stats}},
      "total_sessions": int,
      "total_responses": int,
      "window_size_note": str
    """
    # Accumulate per profile
    by_profile: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: {"onset_ms": [], "settle_ms": [], "peak_delta": [], "grip_variance": []}
    )

    total_sessions = 0
    total_responses = 0
    skipped_invalid = 0

    for path in session_files:
        responses = load_l6_responses(path)
        if not responses:
            continue
        total_sessions += 1
        for r in responses:
            if not r.get("valid", False):
                skipped_invalid += 1
                continue
            if r.get("peak_delta", 0.0) < _MIN_PEAK_DELTA:
                skipped_invalid += 1
                continue
            pid = int(r.get("profile_id", -1))
            if pid < 0:
                continue
            by_profile[pid]["onset_ms"].append(float(r["onset_ms"]))
            by_profile[pid]["settle_ms"].append(float(r["settle_ms"]))
            by_profile[pid]["peak_delta"].append(float(r["peak_delta"]))
            by_profile[pid]["grip_variance"].append(float(r["grip_variance"]))
            total_responses += 1

    print(f"Loaded {total_sessions} sessions with L6 data")
    print(f"  Valid responses : {total_responses}")
    print(f"  Skipped (invalid/no-press): {skipped_invalid}")
    print()

    profiles_out: dict[str, dict] = {}

    for pid in sorted(by_profile.keys()):
        data = by_profile[pid]
        n = len(data["onset_ms"])
        name = _PROFILE_NAMES.get(pid, f"UNKNOWN_{pid}")

        if n < _MIN_SESSIONS_WARN:
            print(f"  WARNING: profile {pid} ({name}): only {n} responses "
                  f"(need >= {_MIN_SESSIONS_WARN} for any estimate, "
                  f">= {_MIN_SESSIONS_PRODUCTION} for production).")

        onset_vals  = data["onset_ms"]
        settle_vals = data["settle_ms"]
        gvar_vals   = data["grip_variance"]
        pdelta_vals = data["peak_delta"]

        onset_mean  = _mean(onset_vals)
        onset_std   = _std(onset_vals)
        settle_mean = _mean(settle_vals)
        settle_std  = _std(settle_vals)
        gvar_mean   = _mean(gvar_vals)
        gvar_std    = _std(gvar_vals)

        # Recommended thresholds (3-sigma upper bounds for onset/settle; 2-sigma floor for grip)
        rec_onset_thresh   = round(onset_mean  + 3.0 * onset_std,  1)
        rec_settle_thresh  = round(settle_mean + 3.0 * settle_std, 1)
        rec_gvar_floor     = round(max(0.0, gvar_mean - 2.0 * gvar_std), 2)

        profiles_out[str(pid)] = {
            "profile_id":              pid,
            "name":                    name,
            "n_responses":             n,
            "sufficient_for_production": n >= _MIN_SESSIONS_PRODUCTION,
            "onset_ms": {
                "mean": round(onset_mean, 2), "std": round(onset_std, 2),
                "p95": round(_percentile(onset_vals, 95), 2),
                "recommended_threshold_ms": rec_onset_thresh,
            },
            "settle_ms": {
                "mean": round(settle_mean, 2), "std": round(settle_std, 2),
                "p95": round(_percentile(settle_vals, 95), 2),
                "recommended_threshold_ms": rec_settle_thresh,
            },
            "grip_variance": {
                "mean": round(gvar_mean, 4), "std": round(gvar_std, 4),
                "recommended_floor": rec_gvar_floor,
            },
            "peak_delta": {
                "mean": round(_mean(pdelta_vals), 2), "std": round(_std(pdelta_vals), 2),
            },
        }

        status = "PRODUCTION" if n >= _MIN_SESSIONS_PRODUCTION else (
            "PRELIMINARY" if n >= _MIN_SESSIONS_WARN else "INSUFFICIENT"
        )
        print(f"  Profile {pid} ({name}): N={n} [{status}]")
        print(f"    onset_ms:    mean={onset_mean:.1f}, std={onset_std:.1f}, "
              f"-> threshold={rec_onset_thresh:.1f} ms")
        print(f"    settle_ms:   mean={settle_mean:.1f}, std={settle_std:.1f}, "
              f"-> threshold={rec_settle_thresh:.1f} ms")
        print(f"    grip_var:    mean={gvar_mean:.2f}, std={gvar_std:.2f}, "
              f"-> floor={rec_gvar_floor:.2f}")

    return {
        "profiles": profiles_out,
        "total_sessions": total_sessions,
        "total_responses": total_responses,
        "window_size_note": (
            "Thresholds are mean + 3*std for onset/settle (upper bound for human responses), "
            "and mean - 2*std for grip_variance (lower bound — injection suspected below floor). "
            f"N >= {_MIN_SESSIONS_PRODUCTION} per profile required for production use."
        ),
    }


# ---------------------------------------------------------------------------
# CHALLENGE_PROFILES replacement block printer
# ---------------------------------------------------------------------------

def print_profiles_replacement(profiles_out: dict[str, dict]) -> None:
    """Print a Python literal replacement block for CHALLENGE_PROFILES."""
    if not profiles_out:
        return

    print()
    print("=" * 60)
    print("# Paste into bridge/controller/l6_challenge_profiles.py")
    print("# Replace the CHALLENGE_PROFILES dict with these thresholds.")
    print("# WARNING: Only replace entries with sufficient_for_production=True.")
    print("=" * 60)
    print()

    for pid_str, stats in sorted(profiles_out.items(), key=lambda x: int(x[0])):
        pid  = stats["profile_id"]
        name = stats["name"]
        n    = stats["n_responses"]
        ok   = stats["sufficient_for_production"]
        ont  = stats["onset_ms"]["recommended_threshold_ms"]
        sett = stats["settle_ms"]["recommended_threshold_ms"]
        note = "# PRODUCTION" if ok else f"# PRELIMINARY (N={n} < {_MIN_SESSIONS_PRODUCTION})"
        print(f"    # {name}  {note}")
        print(f"    # onset_threshold_ms={ont}, settle_threshold_ms={sett}")
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "session_files",
        nargs="*",
        help="Session JSON file(s) produced by scripts/capture_session.py with L6 enabled. "
             "Glob patterns are accepted on shells that expand them.",
    )
    parser.add_argument(
        "--output",
        default="l6_calibration_profile.json",
        help="Output JSON path (default: l6_calibration_profile.json).",
    )
    args = parser.parse_args()

    # Expand globs manually (Windows cmd.exe does not expand * in args)
    paths: list[str] = []
    for pattern in args.session_files:
        expanded = glob.glob(pattern)
        paths.extend(expanded if expanded else [pattern])

    if not paths:
        parser.print_help()
        print(
            "\nERROR: No session files provided.\n"
            "Capture L6 sessions first:\n"
            "  1. Set L6_CHALLENGES_ENABLED=true in bridge config\n"
            "  2. Run scripts/capture_session.py for N >= 50 sessions\n"
            "  3. Re-run this calibrator on the captured files",
            file=sys.stderr,
        )
        return 1

    print(f"L6 Threshold Calibrator — {len(paths)} file(s)")
    print()

    result = calibrate(paths)

    # Write JSON
    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print()
    print(f"Profile written -> {output_path}")

    # Print replacement block
    print_profiles_replacement(result["profiles"])

    total_prod = sum(
        1 for s in result["profiles"].values()
        if s["sufficient_for_production"]
    )
    total_profiles = len(result["profiles"])

    print(f"Summary: {total_prod}/{total_profiles} profiles have production-grade data "
          f"(N >= {_MIN_SESSIONS_PRODUCTION} responses).")

    if total_prod < total_profiles:
        print(
            f"  Capture more L6 sessions and re-run to reach production quality for "
            f"all {total_profiles} profiles."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
