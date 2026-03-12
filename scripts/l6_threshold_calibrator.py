"""
l6_threshold_calibrator.py — Empirical L6 active challenge-response threshold calibrator.

DATA SOURCES (choose one)
--------------------------
  --from-db [DB_PATH]     Read from l6_capture_sessions SQLite table (Phase 42 default).
                          DB_PATH defaults to bridge/vapi_bridge/vapi_bridge.db
  session_files ...       Read from session JSON files' metadata.l6_responses (legacy mode).

MINIMUM DATA REQUIREMENT
-------------------------
  N >= 50 valid responses PER PROFILE required for production thresholds (--min-sessions).
  Fewer than 10 responses per profile emit a WARNING and produce unreliable estimates.
  Thresholds from N < 10 must not be used in production.

CALIBRATED THRESHOLDS (per profile_id)
----------------------------------------
  onset_threshold_ms    — human 3-sigma upper bound of onset_ms
  settle_threshold_ms   — human 3-sigma upper bound of settle_ms
  grip_variance_floor   — human mean - 2-sigma (responses below this suspected injection)

INJECTION WINDOW CONFIRMED
---------------------------
  When the -3σ floor of onset_ms > 5 ms, the injection detection window is confirmed:
  any response with onset_ms < floor is sub-neurological and should be flagged.

OUTPUT
------
  calibration/l6_calibration_profile.json  — thresholds by profile_id + raw stats
  Formatted table printed to stdout.
  CHALLENGE_PROFILES replacement block printed for copy-paste.

USAGE
-----
  # From SQLite DB (Phase 42 default — requires L6 capture sessions):
  python scripts/l6_threshold_calibrator.py --from-db
  python scripts/l6_threshold_calibrator.py --from-db bridge/vapi_bridge/vapi_bridge.db
  python scripts/l6_threshold_calibrator.py --from-db --player P1 --min-sessions 10

  # From JSON session files (legacy):
  python scripts/l6_threshold_calibrator.py sessions/hw_*.json
  python scripts/l6_threshold_calibrator.py sessions/hw_*.json --output l6_profile_v2.json

SEE ALSO
--------
  bridge/controller/l6_challenge_profiles.py — CHALLENGE_PROFILES definition
  bridge/vapi_bridge/l6_response_analyzer.py — L6ResponseMetrics, L6ResponseAnalyzer
  scripts/l6_capture_session.py              — operator capture tool (Phase 42)
  scripts/l6_hardware_check.py               — hardware pre-flight check (Phase 42)
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sqlite3
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
    6: "PULSE_BUILDUP",
    7: "RIGID_MAX",
}

_MIN_PEAK_DELTA = 5.0   # minimum peak_delta to count as a real response
_MIN_SESSIONS_WARN = 10
_MIN_SESSIONS_PRODUCTION = 50  # overridden by --min-sessions


# ---------------------------------------------------------------------------
# Statistics helpers (no numpy dependency)
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
# Data loading — SQLite (Phase 42 primary)
# ---------------------------------------------------------------------------

def _default_db_path() -> str:
    """Return the default bridge SQLite DB path relative to the project root."""
    here = Path(__file__).parent
    candidates = [
        here.parent / "bridge" / "vapi_bridge" / "vapi_bridge.db",
        here.parent / "bridge" / "vapi_bridge.db",
        here.parent / "vapi_bridge.db",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[0])


def load_from_db(
    db_path: str,
    player_id: str = "",
) -> tuple[list[dict], int]:
    """Load l6 responses from l6_capture_sessions SQLite table.

    Returns (responses_list, total_rows) where each response dict has:
      profile_id, onset_ms, settle_ms, peak_delta, grip_variance
    Filters to valid responses (peak_delta >= _MIN_PEAK_DELTA).
    """
    if not os.path.exists(db_path):
        print(f"  ERROR: DB not found: {db_path}", file=sys.stderr)
        return [], 0

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        print(f"  ERROR: Cannot open DB {db_path}: {exc}", file=sys.stderr)
        return [], 0

    try:
        # Check table exists
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='l6_capture_sessions'"
        ).fetchone()
        if not tbl:
            print("  ERROR: l6_capture_sessions table not found. "
                  "Run l6_capture_session.py first.", file=sys.stderr)
            conn.close()
            return [], 0

        params: list = []
        where = ""
        if player_id:
            where = "WHERE player_id = ?"
            params.append(player_id)

        rows = conn.execute(
            f"SELECT * FROM l6_capture_sessions {where} ORDER BY created_at ASC",
            params,
        ).fetchall()
        total_rows = len(rows)
        conn.close()
    except Exception as exc:
        print(f"  ERROR: DB query failed: {exc}", file=sys.stderr)
        return [], 0

    responses = []
    for r in rows:
        responses.append({
            "profile_id":    r["profile_id"],
            "onset_ms":      r["onset_ms"],
            "settle_ms":     r["settle_ms"],
            "peak_delta":    r["peak_delta"],
            "grip_variance": r["grip_variance"],
            "valid":         True,  # all DB rows represent completed response windows
            "player_id":     r["player_id"],
            "game_title":    r["game_title"],
        })

    return responses, total_rows


# ---------------------------------------------------------------------------
# Data loading — session JSON files (legacy)
# ---------------------------------------------------------------------------

def load_l6_responses(path: str) -> list[dict]:
    """Load L6 response dicts from a session JSON file's metadata.l6_responses."""
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
# Core calibration (shared between DB and JSON modes)
# ---------------------------------------------------------------------------

def _accumulate(
    all_responses: list[dict],
    source_label: str,
) -> tuple[dict[int, dict[str, list[float]]], int, int]:
    """Filter and accumulate responses into per-profile data dicts.

    Returns (by_profile, total_accepted, total_skipped).
    """
    by_profile: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: {"onset_ms": [], "settle_ms": [], "peak_delta": [], "grip_variance": []}
    )
    accepted = 0
    skipped = 0

    for r in all_responses:
        if not r.get("valid", True):
            skipped += 1
            continue
        if float(r.get("peak_delta", 0.0)) < _MIN_PEAK_DELTA:
            skipped += 1
            continue
        pid = int(r.get("profile_id", -1))
        if pid < 0 or pid == 0:
            skipped += 1
            continue
        by_profile[pid]["onset_ms"].append(float(r["onset_ms"]))
        by_profile[pid]["settle_ms"].append(float(r["settle_ms"]))
        by_profile[pid]["peak_delta"].append(float(r["peak_delta"]))
        by_profile[pid]["grip_variance"].append(float(r["grip_variance"]))
        accepted += 1

    print(f"Source: {source_label}")
    print(f"  Accepted responses : {accepted}")
    print(f"  Skipped (invalid/low-delta): {skipped}")
    print()

    return by_profile, accepted, skipped


def _compute_profile_stats(
    by_profile: dict[int, dict[str, list[float]]],
    min_sessions: int,
) -> dict[str, dict]:
    """Compute per-profile calibration statistics and thresholds."""
    profiles_out: dict[str, dict] = {}

    for pid in sorted(by_profile.keys()):
        data = by_profile[pid]
        n = len(data["onset_ms"])
        name = _PROFILE_NAMES.get(pid, f"UNKNOWN_{pid}")

        if n < _MIN_SESSIONS_WARN:
            print(f"  WARNING: profile {pid} ({name}): only {n} responses "
                  f"(need >= {_MIN_SESSIONS_WARN} for any estimate, "
                  f">= {min_sessions} for production).")

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

        # Recommended thresholds
        rec_onset_thresh  = round(onset_mean  + 3.0 * onset_std,  1)
        rec_settle_thresh = round(settle_mean + 3.0 * settle_std, 1)
        rec_gvar_floor    = round(max(0.0, gvar_mean - 2.0 * gvar_std), 2)

        # Injection window: -3σ floor of onset_ms > 5ms means window confirmed
        onset_neg3sigma = round(max(0.0, onset_mean - 3.0 * onset_std), 2)
        injection_window_confirmed = onset_neg3sigma > 5.0

        profiles_out[str(pid)] = {
            "profile_id":   pid,
            "name":         name,
            "n_responses":  n,
            "sufficient_for_production": n >= min_sessions,
            "injection_window_confirmed": injection_window_confirmed,
            "onset_ms": {
                "mean":  round(onset_mean,  2),
                "std":   round(onset_std,   2),
                "min":   round(min(onset_vals), 2) if onset_vals else 0.0,
                "max":   round(max(onset_vals), 2) if onset_vals else 0.0,
                "p10":   round(_percentile(onset_vals, 10),  2),
                "p90":   round(_percentile(onset_vals, 90),  2),
                "p95":   round(_percentile(onset_vals, 95),  2),
                "neg3sigma_floor":      onset_neg3sigma,
                "recommended_threshold_ms": rec_onset_thresh,
            },
            "settle_ms": {
                "mean":  round(settle_mean, 2),
                "std":   round(settle_std,  2),
                "min":   round(min(settle_vals), 2) if settle_vals else 0.0,
                "max":   round(max(settle_vals), 2) if settle_vals else 0.0,
                "p10":   round(_percentile(settle_vals, 10), 2),
                "p90":   round(_percentile(settle_vals, 90), 2),
                "p95":   round(_percentile(settle_vals, 95), 2),
                "recommended_threshold_ms": rec_settle_thresh,
            },
            "grip_variance": {
                "mean":  round(gvar_mean, 4),
                "std":   round(gvar_std,  4),
                "min":   round(min(gvar_vals), 4) if gvar_vals else 0.0,
                "max":   round(max(gvar_vals), 4) if gvar_vals else 0.0,
                "p10":   round(_percentile(gvar_vals, 10), 4),
                "p90":   round(_percentile(gvar_vals, 90), 4),
                "recommended_floor": rec_gvar_floor,
            },
            "peak_delta": {
                "mean": round(_mean(pdelta_vals), 2),
                "std":  round(_std(pdelta_vals),  2),
                "min":  round(min(pdelta_vals),   2) if pdelta_vals else 0.0,
                "max":  round(max(pdelta_vals),   2) if pdelta_vals else 0.0,
            },
        }

    return profiles_out


# ---------------------------------------------------------------------------
# Formatted table output
# ---------------------------------------------------------------------------

def print_calibration_table(profiles_out: dict[str, dict], min_sessions: int) -> None:
    """Print a formatted calibration summary table."""
    col_w = [4, 20, 6, 12, 12, 12, 12, 12, 10, 10]
    header = (
        f"{'ID':<4} {'Profile':<20} {'N':>6} "
        f"{'onset_mean':>12} {'onset_3sig':>12} {'settle_mean':>12} {'settle_3sig':>12} "
        f"{'gvar_mean':>12} {'gvar_flr':>10} {'Status':>10}"
    )
    sep = "-" * len(header)

    print()
    print("L6 Calibration Summary")
    print(sep)
    print(header)
    print(sep)

    for pid_str, s in sorted(profiles_out.items(), key=lambda x: int(x[0])):
        pid   = s["profile_id"]
        name  = s["name"]
        n     = s["n_responses"]
        om    = s["onset_ms"]["mean"]
        ot    = s["onset_ms"]["recommended_threshold_ms"]
        sm    = s["settle_ms"]["mean"]
        st    = s["settle_ms"]["recommended_threshold_ms"]
        gm    = s["grip_variance"]["mean"]
        gf    = s["grip_variance"]["recommended_floor"]
        ok    = s["sufficient_for_production"]
        inj   = s["injection_window_confirmed"]

        status = "PRODUCTION" if ok else ("PRELIM" if n >= _MIN_SESSIONS_WARN else "INSUFF")
        inj_mark = " *INJ*" if inj else ""

        print(
            f"{pid:<4} {name:<20} {n:>6} "
            f"{om:>12.1f} {ot:>12.1f} {sm:>12.1f} {st:>12.1f} "
            f"{gm:>12.1f} {gf:>10.2f} {status + inj_mark:>10}"
        )

    print(sep)
    print("  onset_3sig = mean+3*std (upper bound — classify() threshold)")
    print("  gvar_flr   = mean-2*std (lower bound — injection suspected below)")
    print("  *INJ*      = injection window CONFIRMED: onset -3sig > 5 ms")
    print()


# ---------------------------------------------------------------------------
# CHALLENGE_PROFILES replacement block printer
# ---------------------------------------------------------------------------

def print_profiles_replacement(
    profiles_out: dict[str, dict], min_sessions: int
) -> None:
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
        inj  = stats["injection_window_confirmed"]
        inj_note = " [INJECTION WINDOW CONFIRMED]" if inj else ""
        note = "# PRODUCTION" if ok else f"# PRELIMINARY (N={n} < {min_sessions})"
        print(f"    # {name}  {note}{inj_note}")
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

    # Input source
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--from-db",
        nargs="?",
        const="",
        metavar="DB_PATH",
        help="Read from l6_capture_sessions SQLite table. "
             "DB_PATH defaults to bridge/vapi_bridge/vapi_bridge.db.",
    )
    parser.add_argument(
        "session_files",
        nargs="*",
        help="Legacy: session JSON file(s) with metadata.l6_responses. "
             "Glob patterns accepted.",
    )

    # Filters
    parser.add_argument(
        "--player",
        default="",
        help="Filter to this player_id (DB mode only; '' = all players).",
    )

    # Output
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path. Defaults to calibration/l6_calibration_profile.json.",
    )
    parser.add_argument(
        "--min-sessions",
        type=int,
        default=_MIN_SESSIONS_PRODUCTION,
        help=f"Minimum responses per profile for production grade (default: {_MIN_SESSIONS_PRODUCTION}).",
    )

    args = parser.parse_args()

    min_sessions = args.min_sessions

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        cal_dir = Path(__file__).parent.parent / "calibration"
        cal_dir.mkdir(exist_ok=True)
        output_path = cal_dir / "l6_calibration_profile.json"

    print("L6 Threshold Calibrator (Phase 42)")
    print(f"Min sessions for production: {min_sessions}")
    print()

    # -----------------------------------------------------------------------
    # Load data
    # -----------------------------------------------------------------------
    all_responses: list[dict] = []
    source_label = ""

    if args.from_db is not None:
        # SQLite mode
        db_path = args.from_db if args.from_db else _default_db_path()
        print(f"DB mode: {db_path}")
        if args.player:
            print(f"Filter: player_id={args.player!r}")
        raw, total_rows = load_from_db(db_path, player_id=args.player)
        all_responses = raw
        source_label = f"SQLite:{db_path} (player={args.player or 'all'}, {total_rows} total rows)"
    else:
        # JSON session file mode (legacy)
        paths: list[str] = []
        for pattern in args.session_files:
            expanded = glob.glob(pattern)
            paths.extend(expanded if expanded else [pattern])

        if not paths:
            parser.print_help()
            print(
                "\nERROR: No input provided.\n"
                "Use --from-db to read from SQLite (Phase 42), or pass session JSON files.\n"
                "\nCapture L6 sessions first:\n"
                "  1. python scripts/l6_hardware_check.py\n"
                "  2. python scripts/l6_capture_session.py --player P1 --game Warzone --target 50\n"
                "  3. python scripts/l6_threshold_calibrator.py --from-db",
                file=sys.stderr,
            )
            return 1

        print(f"JSON mode: {len(paths)} file(s)")
        for path in paths:
            file_responses = load_l6_responses(path)
            for r in file_responses:
                r.setdefault("player_id", "")
                r.setdefault("game_title", "")
                all_responses.extend([r])
        source_label = f"{len(paths)} JSON session file(s)"

    if not all_responses:
        print("ERROR: No responses loaded. Check data source.", file=sys.stderr)
        return 1

    # -----------------------------------------------------------------------
    # Accumulate + compute
    # -----------------------------------------------------------------------
    by_profile, accepted, _skipped = _accumulate(all_responses, source_label)

    if not by_profile:
        print("ERROR: No valid responses after filtering (peak_delta >= 5).", file=sys.stderr)
        return 1

    profiles_out = _compute_profile_stats(by_profile, min_sessions)

    # -----------------------------------------------------------------------
    # Print formatted table
    # -----------------------------------------------------------------------
    print_calibration_table(profiles_out, min_sessions)

    # -----------------------------------------------------------------------
    # Save JSON
    # -----------------------------------------------------------------------
    result = {
        "profiles": profiles_out,
        "total_accepted_responses": accepted,
        "source": source_label,
        "min_sessions_for_production": min_sessions,
        "window_size_note": (
            "Thresholds: onset/settle = mean+3*std (upper bound for human responses); "
            "grip_variance floor = mean-2*std (lower bound — injection suspected below). "
            f"N >= {min_sessions} per profile required for production use."
        ),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Profile written -> {output_path}")

    # -----------------------------------------------------------------------
    # Print replacement block
    # -----------------------------------------------------------------------
    print_profiles_replacement(profiles_out, min_sessions)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    total_prod = sum(1 for s in profiles_out.values() if s["sufficient_for_production"])
    total_prof = len(profiles_out)
    n_inj = sum(1 for s in profiles_out.values() if s["injection_window_confirmed"])

    print(f"Summary: {total_prod}/{total_prof} profiles have production-grade data "
          f"(N >= {min_sessions} responses).")
    if n_inj:
        print(f"  Injection window CONFIRMED for {n_inj}/{total_prof} profiles "
              f"(onset -3sigma > 5 ms).")
    if total_prod < total_prof:
        print(f"  Capture more L6 sessions to reach production quality for all profiles.")
        print(f"  python scripts/l6_capture_session.py --player P1 --game Warzone --target {min_sessions}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
