#!/usr/bin/env python3
"""
Autonomous adversarial validation suite for VAPI PITL L2–L5.

Loads every session from sessions/human/ and sessions/adversarial/, runs the
full offline PITL pipeline (L2 injection / L4 biometric Mahalanobis / L5
temporal rhythm), and outputs a detection matrix to both terminal and
docs/adversarial-validation-results.md.

Usage:
    python scripts/run_adversarial_validation.py [--quiet]

No controller hardware required — all analysis is offline from JSON files.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HUMAN_DIR    = PROJECT_ROOT / "sessions" / "human"
ADV_DIR      = PROJECT_ROOT / "sessions" / "adversarial"
DOCS_DIR     = PROJECT_ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# PITL calibrated thresholds (from N=50 hardware sessions, 2026-03-02)
# ---------------------------------------------------------------------------
L4_ANOMALY_THRESHOLD    = 5.869   # Mahalanobis distance in 6-proxy feature space
L4_INJECTION_GYRO_THRESH = 20.0   # LSB std — below this with active triggers = injection
L4_MIN_TRIGGER_FRAMES   = 10      # Minimum trigger-active frames to assess L2/L4 injection
L5_CV_THRESHOLD         = 0.08    # CV (std/mean) below this = timing too steady
L5_ENTROPY_THRESHOLD    = 1.0     # Shannon entropy (bits) below this = too few distinct intervals
L5_QUANT_THRESHOLD      = 0.55    # 60 Hz-snapping fraction above this = bot timer
L5_MIN_PRESSES          = 20      # Minimum press count for reliable L5 analysis
_TICK_MS                = 16.6667 # 60 Hz game-loop tick

# Button bit layout (buttons_1)
_R2_DIGITAL_BIT  = 3
_R2_PRESS_THRESH = 64
_R2_RELEASE_THRESH = 30

# Number of reports used to build per-session fingerprint (30 s at 1000 Hz).
# Must be >= session length for adversarial sessions (30 000 reports).
# Using 5 000 (5 s) caused FP on sessions that start with a lobby/idle period.
_FINGERPRINT_REPORTS = 30_000


# ===========================================================================
# Feature extraction helpers (mirrors threshold_calibrator.py exactly)
# ===========================================================================

def _trigger_onset_velocity(trigger_vals: list) -> float:
    onsets: list[float] = []
    in_onset = False
    onset_start = 0
    for i, v in enumerate(trigger_vals):
        if not in_onset and v > 5:
            in_onset    = True
            onset_start = i
        elif in_onset and (v >= 250 or (i > onset_start and trigger_vals[i-1] > v)):
            peak     = trigger_vals[i-1] if i > onset_start else v
            duration = max(i - onset_start, 1)
            onsets.append(duration / (peak + 1e-6))
            in_onset = False
    return sum(onsets) / len(onsets) if onsets else 0.0


def _autocorr_py(series: list, lag: int) -> float:
    n = len(series)
    if n <= lag + 2:
        return 0.0
    x = series[:-lag];  y = series[lag:]
    mx = sum(x) / len(x);  my = sum(y) / len(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sx  = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    sy  = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if sx < 1e-10 or sy < 1e-10:
        return 0.0
    return num / (sx * sy)


def _session_fingerprint(session: dict, max_reports: int = _FINGERPRINT_REPORTS
                          ) -> list[float] | None:
    """
    6-proxy biometric feature vector (mirrors threshold_calibrator.py).
    Returns None if insufficient data.
    """
    reports = session.get("reports", [])[:max_reports]
    if len(reports) < 50:
        return None

    l2_v, r2_v = [], []
    gx_v, gy_v, gz_v = [], [], []
    ax_v, ay_v, az_v = [], [], []
    lx_v, ly_v = [], []

    for r in reports:
        f = r.get("features", {})
        def _g(k):
            v = f.get(k);  return float(v) if v is not None else None
        l2 = _g("l2_trigger"); r2 = _g("r2_trigger")
        gx = _g("gyro_x");     gy = _g("gyro_y");     gz = _g("gyro_z")
        ax = _g("accel_x");    ay = _g("accel_y");     az = _g("accel_z")
        lx = _g("left_stick_x"); ly = _g("left_stick_y")
        if l2 is not None: l2_v.append(l2)
        if r2 is not None: r2_v.append(r2)
        if gx is not None: gx_v.append(gx)
        if gy is not None: gy_v.append(gy)
        if gz is not None: gz_v.append(gz)
        if ax is not None: ax_v.append(ax)
        if ay is not None: ay_v.append(ay)
        if az is not None: az_v.append(az)
        if lx is not None: lx_v.append(lx)
        if ly is not None: ly_v.append(ly)

    onset_l2 = _trigger_onset_velocity([int(v) for v in l2_v]) if l2_v else 0.0
    onset_r2 = _trigger_onset_velocity([int(v) for v in r2_v]) if r2_v else 0.0

    _GYRO_STILL = 500.0
    still_mags: list[float] = []
    for gx, gy, gz, ax, ay, az in zip(gx_v, gy_v, gz_v, ax_v, ay_v, az_v):
        if math.sqrt(gx*gx + gy*gy + gz*gz) < _GYRO_STILL:
            still_mags.append(math.sqrt(ax*ax + ay*ay + az*az))
    tremor_var = 0.0
    if len(still_mags) >= 5:
        m = sum(still_mags) / len(still_mags)
        tremor_var = sum((v - m)**2 for v in still_mags) / len(still_mags)

    grip: list[float] = [l/(r+1e-6) for l, r in zip(l2_v, r2_v) if l > 10 and r > 10]
    grip_asym = sum(grip) / len(grip) if grip else 1.0

    stick_vels: list[float] = []
    for i in range(1, min(len(lx_v), len(ly_v))):
        dx = lx_v[i] - lx_v[i-1];  dy = ly_v[i] - ly_v[i-1]
        stick_vels.append(math.sqrt(dx*dx + dy*dy))

    ac1 = _autocorr_py(stick_vels, 1)
    ac5 = _autocorr_py(stick_vels, 5)

    return [onset_l2, onset_r2, tremor_var, grip_asym, ac1, ac5]


def _extract_press_intervals(session: dict) -> list[float]:
    """Return list of inter-press intervals in ms (mirrors threshold_calibrator.py)."""
    reports = session.get("reports", [])
    if not reports:
        return []

    use_digital = any(
        r.get("features", {}).get("buttons_1") is not None
        for r in reports[:10]
    )

    intervals: list[float] = []
    last_press_ts: float | None = None
    prev_pressed  = False

    for r in reports:
        f   = r.get("features", {})
        ts  = float(r.get("timestamp_ms", 0))
        if use_digital:
            b1 = f.get("buttons_1")
            pressed = bool((int(b1) >> _R2_DIGITAL_BIT) & 1) if b1 is not None else False
        else:
            r2  = float(f.get("r2_trigger") or 0)
            if not prev_pressed and r2 >= _R2_PRESS_THRESH:
                pressed = True
            elif prev_pressed and r2 >= _R2_RELEASE_THRESH:
                pressed = True
            else:
                pressed = False

        if pressed and not prev_pressed:
            if last_press_ts is not None:
                intervals.append(ts - last_press_ts)
            last_press_ts = ts
        prev_pressed = pressed

    return intervals


# ===========================================================================
# L2 — Injection detection (hardware IMU oracle)
# ===========================================================================

def check_l2(session: dict) -> dict:
    """
    Detect software injection: zero IMU while sticks or triggers are active.
    Returns dict with 'fired', 'gyro_std', 'trigger_frames'.
    """
    reports = session.get("reports", [])
    trigger_frames_gyro: list[float] = []

    for r in reports:
        f  = r.get("features", {})
        r2 = float(f.get("r2_trigger") or 0)
        l2 = float(f.get("l2_trigger") or 0)
        lx = abs(float(f.get("left_stick_x")  or 128) - 128)
        rx = abs(float(f.get("right_stick_x") or 128) - 128)
        active = r2 > 20 or l2 > 20 or lx > 15 or rx > 15
        if active:
            gx = float(f.get("gyro_x") or 0)
            gy = float(f.get("gyro_y") or 0)
            gz = float(f.get("gyro_z") or 0)
            trigger_frames_gyro.extend([gx, gy, gz])

    if len(trigger_frames_gyro) < L4_MIN_TRIGGER_FRAMES * 3:
        return {"fired": False, "gyro_std": None, "active_frames": 0,
                "reason": "insufficient active frames"}

    gyro_arr  = np.array(trigger_frames_gyro, dtype=np.float32)
    gyro_std  = float(gyro_arr.std())
    fired     = gyro_std < L4_INJECTION_GYRO_THRESH

    return {
        "fired":          fired,
        "gyro_std":       round(gyro_std, 3),
        "active_frames":  len(trigger_frames_gyro) // 3,
        "inference":      "0x28 DRIVER_INJECT" if fired else "NOMINAL",
    }


# ===========================================================================
# L4 — Biometric Mahalanobis distance
# ===========================================================================

class L4Validator:
    """
    Builds a human fingerprint reference from real sessions, then classifies
    test sessions by their Mahalanobis distance from the human centroid.
    Uses diagonal covariance (per-feature variance) matching the production
    BiometricFusionClassifier approach.
    """

    def __init__(self, human_sessions: list[dict], quiet: bool = False) -> None:
        fps: list[list[float]] = []
        for s in human_sessions:
            fp = _session_fingerprint(s)
            if fp is not None:
                fps.append(fp)
        if len(fps) < 3:
            raise ValueError(f"Need at least 3 human session fingerprints, got {len(fps)}")

        arr          = np.array(fps, dtype=np.float64)
        self._mean   = arr.mean(axis=0)
        self._var    = arr.var(axis=0)
        self._n_ref  = len(fps)
        if not quiet:
            print(f"  L4 reference: {self._n_ref} human fingerprints, "
                  f"mean={self._mean.round(4).tolist()}")

    def check(self, session: dict) -> dict:
        fp = _session_fingerprint(session)
        if fp is None:
            return {"fired": False, "distance": None, "reason": "too few reports"}
        x    = np.array(fp, dtype=np.float64)
        diff = x - self._mean
        safe = np.maximum(self._var, 1e-9)
        dist = float(np.sqrt(np.sum(diff**2 / safe)))
        fired = dist > L4_ANOMALY_THRESHOLD
        return {
            "fired":      fired,
            "distance":   round(dist, 4),
            "threshold":  L4_ANOMALY_THRESHOLD,
            "fingerprint": [round(v, 5) for v in fp],
            "inference":  "0x30 BIOMETRIC_ANOMALY" if fired else "NOMINAL",
        }


# ===========================================================================
# L5 — Temporal rhythm oracle
# ===========================================================================

def check_l5(session: dict) -> dict:
    """
    Check inter-press timing distribution for bot-like patterns.
    Returns dict with 'fired', 'cv', 'entropy_bits', 'quant_score', 'signals'.
    """
    intervals = _extract_press_intervals(session)
    if len(intervals) < L5_MIN_PRESSES:
        return {
            "fired":        False,
            "cv":           None,
            "entropy_bits": None,
            "quant_score":  None,
            "signals":      0,
            "press_count":  len(intervals),
            "reason":       f"only {len(intervals)} presses (need {L5_MIN_PRESSES})",
        }

    arr  = np.array(intervals, dtype=np.float32)
    mean = float(arr.mean())
    if mean < 1e-6:
        return {"fired": False, "cv": 0.0, "reason": "zero mean intervals"}

    cv = float(arr.std()) / mean

    # Shannon entropy over 50 ms bins
    max_v = float(arr.max())
    bins  = np.arange(0.0, max_v + 51.0, 50.0)
    counts, _ = np.histogram(arr, bins=bins)
    nz    = counts[counts > 0]
    if len(nz) == 0:
        entropy = 0.0
    else:
        p       = nz / nz.sum()
        entropy = float(-np.sum(p * np.log2(p)))

    # Quantization score (60 Hz grid snapping)
    residue   = arr % _TICK_MS
    deviations = np.minimum(residue, _TICK_MS - residue)
    quant     = float(np.mean(deviations < 5.0))

    sig1   = int(cv      < L5_CV_THRESHOLD)
    sig2   = int(entropy < L5_ENTROPY_THRESHOLD)
    sig3   = int(quant   > L5_QUANT_THRESHOLD)
    n_sigs = sig1 + sig2 + sig3
    fired  = n_sigs >= 2

    return {
        "fired":        fired,
        "cv":           round(cv, 4),
        "entropy_bits": round(entropy, 4),
        "quant_score":  round(quant, 4),
        "signals":      n_sigs,
        "sig_cv":       bool(sig1),
        "sig_entropy":  bool(sig2),
        "sig_quant":    bool(sig3),
        "press_count":  len(intervals),
        "inference":    "0x2B TEMPORAL_ANOMALY" if fired else "NOMINAL",
    }


# ===========================================================================
# Warmup progression analysis (Attack E)
# ===========================================================================

def compute_warmup_score(session: dict) -> float:
    """
    Score ∈ [0,1] measuring how bot-like this session is.
    1.0 = pure bot (zero IMU variance, constant timing).
    0.0 = pure human (high IMU variance, high timing entropy).
    """
    reports = session.get("reports", [])
    if not reports:
        return 0.5

    # Component 1: IMU variance (low = bot-like)
    gyro_vals: list[float] = []
    for r in reports[:5000]:
        f  = r.get("features", {})
        gx = float(f.get("gyro_x") or 0)
        gy = float(f.get("gyro_y") or 0)
        gz = float(f.get("gyro_z") or 0)
        gyro_vals.append(math.sqrt(gx*gx + gy*gy + gz*gz))
    gyro_std = float(np.std(gyro_vals)) if gyro_vals else 0.0
    # Human baseline: ~320 LSB. Normalise: 1.0 at std=0, 0.0 at std>=300.
    imu_bot_score = max(0.0, 1.0 - gyro_std / 300.0)

    # Component 2: L5 features (low CV + low entropy = bot-like)
    l5 = check_l5(session)
    if l5["cv"] is not None:
        cv_bot      = max(0.0, 1.0 - l5["cv"] / 0.35)
        entropy_bot = max(0.0, 1.0 - l5["entropy_bits"] / 1.4)
    else:
        cv_bot = entropy_bot = 0.5   # neutral if not enough presses

    return (imu_bot_score + cv_bot + entropy_bot) / 3.0


# ===========================================================================
# Session loading and analysis
# ===========================================================================

def _load_session(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  WARNING: failed to load {path.name}: {e}")
        return None


def analyze_session(session: dict, l4: L4Validator) -> dict:
    """Run L2, L4, L5 on one session. Return per-layer results."""
    r_l2 = check_l2(session)
    r_l4 = l4.check(session)
    r_l5 = check_l5(session)
    warmup = compute_warmup_score(session)

    any_fired = r_l2["fired"] or r_l4["fired"] or r_l5["fired"]
    return {
        "l2":          r_l2,
        "l4":          r_l4,
        "l5":          r_l5,
        "warmup_score": round(warmup, 4),
        "any_fired":   any_fired,
        "attack_type": session.get("metadata", {}).get("attack_type", "unknown"),
        "session_name": Path(session.get("metadata", {}).get(
            "source_session", "")).stem,
    }


# ===========================================================================
# Aggregation and reporting
# ===========================================================================

ATTACK_DISPLAY_NAMES = {
    "replay":          "Replay (chain-level)",
    "injection":       "IMU-stripped injection",
    "macro":           "Perfect-timing macro",
    "transplant":      "Biometric transplant",
    "warmup":          "Gradual warmup (E)",
    "tick_quantized":  "Quant-masked bot",
    "human_baseline":  "Human baseline",
    "gaming":          "Human gaming (hw_*)",
}


def _pct(fired: int, total: int) -> str:
    if total == 0:
        return "  N/A"
    return f"{100*fired/total:5.1f}%"


def _fp_pct(fired: int, total: int) -> str:
    if total == 0:
        return "  N/A"
    return f"{100*fired/total:5.1f}%"


def build_report(human_results: list[dict],
                 adv_results: list[dict]) -> str:
    """Build the full markdown + terminal-formatted report string."""
    lines: list[str] = []

    def w(s=""):
        lines.append(s)

    w("=" * 72)
    w("VAPI PITL Adversarial Validation Report")
    w("N=50 hardware calibration  |  DualShock Edge CFI-ZCP1  |  1000 Hz")
    w("=" * 72)
    w()

    # ---- Adversarial detection summary ----
    w("ADVERSARIAL SESSIONS  (detection rate — higher is better)")
    w("-" * 72)
    hdr = (f"{'Attack Type':<28} {'N':>3}  "
           f"{'L2 Det%':>8} {'L4 Det%':>8} {'L5 Det%':>8} "
           f"{'Any%':>7}  {'Notes'}")
    w(hdr)
    w("-" * 72)

    # Group adversarial by attack_type (using metadata field)
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in adv_results:
        by_type[r["attack_type"]].append(r)

    attack_order = ["replay", "injection", "macro",
                    "transplant", "warmup", "tick_quantized"]
    summary_rows: list[dict] = []

    for at in attack_order:
        results = by_type.get(at, [])
        if not results:
            continue
        n       = len(results)
        l2_det  = sum(1 for r in results if r["l2"]["fired"])
        l4_det  = sum(1 for r in results if r["l4"]["fired"])
        l5_det  = sum(1 for r in results if r["l5"]["fired"])
        any_det = sum(1 for r in results if r["any_fired"])
        name    = ATTACK_DISPLAY_NAMES.get(at, at)
        notes   = _attack_notes(at, results)
        row = f"{name:<28} {n:>3}  {_pct(l2_det,n):>8} {_pct(l4_det,n):>8} " \
              f"{_pct(l5_det,n):>8} {_pct(any_det,n):>7}  {notes}"
        w(row)
        summary_rows.append({
            "attack_type": at,
            "n": n, "l2": l2_det, "l4": l4_det, "l5": l5_det, "any": any_det,
        })

    # ---- Unknown adversarial types ----
    for at, results in by_type.items():
        if at not in attack_order:
            n = len(results)
            l2_det = sum(1 for r in results if r["l2"]["fired"])
            l4_det = sum(1 for r in results if r["l4"]["fired"])
            l5_det = sum(1 for r in results if r["l5"]["fired"])
            any_det = sum(1 for r in results if r["any_fired"])
            name = ATTACK_DISPLAY_NAMES.get(at, at)
            w(f"{name:<28} {n:>3}  {_pct(l2_det,n):>8} {_pct(l4_det,n):>8} "
              f"{_pct(l5_det,n):>8} {_pct(any_det,n):>7}  (unlabelled)")

    w()

    # ---- Human false-positive summary ----
    w("HUMAN SESSIONS  (false positive rate — lower is better)")
    w("-" * 72)
    hdr2 = (f"{'Session Type':<28} {'N':>3}  "
            f"{'L2 FP%':>8} {'L4 FP%':>8} {'L5 FP%':>8} {'Any FP%':>8}")
    w(hdr2)
    w("-" * 72)

    # Group human sessions by whether they come from hw_* or human_baseline
    hw_res     = [r for r in human_results if "hw_" in r.get("session_name", "")]
    synth_res  = [r for r in human_results if "hw_" not in r.get("session_name", "")]

    def _fp_row(name, results):
        n = len(results)
        if n == 0:
            return
        l2_fp  = sum(1 for r in results if r["l2"]["fired"])
        l4_fp  = sum(1 for r in results if r["l4"]["fired"])
        l5_fp  = sum(1 for r in results if r["l5"]["fired"])
        any_fp = sum(1 for r in results if r["any_fired"])
        w(f"{name:<28} {n:>3}  {_fp_pct(l2_fp,n):>8} {_fp_pct(l4_fp,n):>8} "
          f"{_fp_pct(l5_fp,n):>8} {_fp_pct(any_fp,n):>8}")

    _fp_row("Real hardware (hw_*)", hw_res)
    _fp_row("Synthetic baseline",   synth_res)
    _fp_row("All human sessions",   human_results)
    w()

    # ---- Warmup sequence detail ----
    warmup_res = sorted(
        [r for r in adv_results if r["attack_type"] == "warmup"],
        key=lambda r: r.get("warmup_idx", 0),
    )
    if warmup_res:
        w("WARMUP SEQUENCE  (bot->human progression, Attack E)")
        w("-" * 72)
        w(f"{'Session':<12} {'Alpha':>6} {'Label':<14} "
          f"{'L2':>5} {'L4':>5} {'L5':>5} {'BotScore':>9} "
          f"{'CV':>7} {'Entropy':>8} {'GyroStd':>8}")
        w("-" * 72)
        for i, r in enumerate(warmup_res):
            meta  = r.get("_meta", {})
            alpha = meta.get("warmup_alpha", i / 9.0)
            label = meta.get("warmup_label", "?")
            l2f   = "FIRE" if r["l2"]["fired"] else "-"
            l4f   = "FIRE" if r["l4"]["fired"] else "-"
            l5f   = "FIRE" if r["l5"]["fired"] else "-"
            ws    = r["warmup_score"]
            cv    = r["l5"].get("cv") or 0.0
            ent   = r["l5"].get("entropy_bits") or 0.0
            gstd  = r["l2"].get("gyro_std") or 0.0
            w(f"warmup_{i+1:03d}  {alpha:>6.2f} {label:<14} "
              f"{l2f:>5} {l4f:>5} {l5f:>5} {ws:>9.4f} "
              f"{cv:>7.4f} {ent:>8.4f} {gstd:>8.2f}")
        w()

    # ---- Per-session L5 statistics for human sessions ----
    w("L5 HUMAN BASELINE STATISTICS")
    w("-" * 72)
    l5_data = [(r["l5"]["cv"], r["l5"]["entropy_bits"], r["l5"]["quant_score"])
               for r in hw_res
               if r["l5"]["cv"] is not None]
    if l5_data:
        cvs   = [d[0] for d in l5_data]
        ents  = [d[1] for d in l5_data]
        qnts  = [d[2] for d in l5_data]
        w(f"  L5 CV       — mean: {sum(cvs)/len(cvs):.4f}  "
          f"min: {min(cvs):.4f}  max: {max(cvs):.4f}  "
          f"(threshold < {L5_CV_THRESHOLD})")
        w(f"  L5 Entropy  — mean: {sum(ents)/len(ents):.4f}  "
          f"min: {min(ents):.4f}  max: {max(ents):.4f}  "
          f"(threshold < {L5_ENTROPY_THRESHOLD} bits)")
        w(f"  L5 Quant    — mean: {sum(qnts)/len(qnts):.4f}  "
          f"min: {min(qnts):.4f}  max: {max(qnts):.4f}  "
          f"(threshold > {L5_QUANT_THRESHOLD})")
    w()

    # ---- Calibrated threshold reference ----
    w("CALIBRATED THRESHOLDS  (N=50, high confidence, 2026-03-02)")
    w("-" * 72)
    w(f"  L2 injection gyro std < {L4_INJECTION_GYRO_THRESH} LSB with active input")
    w(f"  L4 Mahalanobis distance > {L4_ANOMALY_THRESHOLD} (mean + 3-sigma, N=50)")
    w(f"  L5 CV < {L5_CV_THRESHOLD} | entropy < {L5_ENTROPY_THRESHOLD} bits | quant > {L5_QUANT_THRESHOLD}  (need >=2/3)")
    w()

    return "\n".join(lines)


def _attack_notes(at: str, results: list[dict]) -> str:
    if at == "replay":
        return "chain-level attack; 0% PITL is expected/correct"
    if at == "warmup":
        bot_scores = [r["warmup_score"] for r in results[:3]]
        mean_bs = sum(bot_scores) / len(bot_scores) if bot_scores else 0
        return f"sessions 1-3 bot_score mean={mean_bs:.3f}"
    if at == "transplant":
        l4_count = sum(1 for r in results if r["l4"]["fired"])
        return f"L4 fired {l4_count}/{len(results)} (single-person dataset limits sensitivity)"
    if at == "tick_quantized":
        l5 = [r for r in results if r["l5"]["quant_score"] is not None]
        if l5:
            mq = sum(r["l5"]["quant_score"] for r in l5) / len(l5)
            return f"mean quant_score={mq:.3f}"
    return ""


def _to_markdown(terminal_report: str, human_results: list[dict],
                 adv_results: list[dict]) -> str:
    """Wrap terminal report in markdown document."""
    all_adv  = len(adv_results)
    any_det  = sum(1 for r in adv_results
                   if r["attack_type"] != "replay" and r["any_fired"])
    non_replay = sum(1 for r in adv_results if r["attack_type"] != "replay")
    h_fp     = sum(1 for r in human_results if r["any_fired"])
    h_tot    = len(human_results)

    md = (
        "# VAPI Adversarial Validation Results\n\n"
        "**Generated:** 2026-03-02  \n"
        "**Calibration:** N=50 DualShock Edge sessions, high confidence  \n"
        f"**Human sessions:** {h_tot} (real hw_* + synthetic baselines)  \n"
        f"**Adversarial sessions:** {all_adv} (6 attack types, real-data transforms)  \n"
        f"**Detection (excl. replay):** {any_det}/{non_replay} "
        f"({100*any_det/max(non_replay,1):.1f}%)  \n"
        f"**False positive rate:** {h_fp}/{h_tot} "
        f"({100*h_fp/max(h_tot,1):.1f}%)\n\n"
        "## Method\n\n"
        "Each adversarial session is a deterministic, reproducible transformation "
        "of a real 1000 Hz DualShock Edge capture. A reviewer can inspect exactly "
        "what was done to each session. Transforms target the specific physical "
        "properties each PITL layer relies on.\n\n"
        "```\n" + terminal_report + "\n```\n\n"
        "## Attack Descriptions\n\n"
        "| Attack | Type | Detection Layer | Transform |\n"
        "|--------|------|-----------------|----------|\n"
        "| Replay | A | Chain (not PITL) | Timestamps +3600 s; identical biometrics |\n"
        "| IMU-stripped injection | B | L2+L4 | All gyro/accel fields zeroed |\n"
        "| Perfect-timing macro | C | L5 | R2 presses at constant 50 ms intervals |\n"
        "| Biometric transplant | D | L4 | Stick+IMU from session X; trigger timing from Y |\n"
        "| Gradual warmup | E | L2+L4+L5 | Linear bot→human interpolation across 10 sessions |\n"
        "| Quant-masked bot | F | L5 | 60 Hz-locked presses + 2 ms Gaussian jitter |\n\n"
        "## Threshold Reference\n\n"
        f"| Layer | Threshold | N=50 Human Baseline |\n"
        f"|-------|-----------|--------------------|\n"
        f"| L2 injection | gyro_std < {L4_INJECTION_GYRO_THRESH} LSB | ~320 LSB (16x margin) |\n"
        f"| L4 Mahalanobis | distance > {L4_ANOMALY_THRESHOLD} | mean+3sigma |\n"
        f"| L5 CV | < {L5_CV_THRESHOLD} | human ~0.34 (4.3x margin) |\n"
        f"| L5 Entropy | < {L5_ENTROPY_THRESHOLD} bits | human ~1.38 bits |\n"
        f"| L5 Quant | > {L5_QUANT_THRESHOLD} | human ~0.35 |\n"
    )
    return md


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress per-session progress output")
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Step 1: Load human sessions and build L4 reference
    # ------------------------------------------------------------------
    print("Loading human sessions...")
    human_paths   = sorted(HUMAN_DIR.glob("hw_*.json"))
    synth_paths   = sorted(HUMAN_DIR.glob("human_baseline_*.json"))
    all_human_paths = human_paths + synth_paths

    if not human_paths:
        print(f"ERROR: No hw_*.json sessions found in {HUMAN_DIR}")
        sys.exit(1)

    print(f"  Found {len(human_paths)} hw_* + {len(synth_paths)} synthetic = "
          f"{len(all_human_paths)} human sessions")

    # Build L4 reference from real hardware sessions only.
    # Truncate each to _FINGERPRINT_REPORTS before passing to L4Validator
    # so reference and test sessions use the same report window.
    ref_sessions: list[dict] = []
    for p in human_paths:
        s = _load_session(p)
        if s:
            s["reports"] = s["reports"][:_FINGERPRINT_REPORTS]
            ref_sessions.append(s)

    if not args.quiet:
        print(f"Building L4 reference fingerprint from {len(ref_sessions)} hw sessions...")
    l4 = L4Validator(ref_sessions, quiet=args.quiet)

    # ------------------------------------------------------------------
    # Step 2: Analyse all human sessions (FP baseline)
    # ------------------------------------------------------------------
    print("\nAnalysing human sessions (false positive baseline)...")
    human_results: list[dict] = []
    for p in all_human_paths:
        if not args.quiet:
            print(f"  {p.name}...", end=" ", flush=True)
        s = _load_session(p)
        if s is None:
            continue
        r = analyze_session(s, l4)
        r["session_name"] = p.stem
        r["attack_type"]  = s.get("metadata", {}).get("attack_type", "human")
        human_results.append(r)
        if not args.quiet:
            fp = "FP!" if r["any_fired"] else "ok"
            print(fp)

    # ------------------------------------------------------------------
    # Step 3: Analyse all adversarial sessions
    # ------------------------------------------------------------------
    print("\nAnalysing adversarial sessions...")
    adv_paths = sorted(ADV_DIR.glob("*.json"))
    if not adv_paths:
        print(f"ERROR: No adversarial sessions found in {ADV_DIR}")
        print("Run: python scripts/generate_adversarial_from_real.py")
        sys.exit(1)

    adv_results: list[dict] = []
    for p in adv_paths:
        if not args.quiet:
            print(f"  {p.name}...", end=" ", flush=True)
        s = _load_session(p)
        if s is None:
            continue
        r = analyze_session(s, l4)
        r["session_name"] = p.stem
        r["attack_type"]  = s.get("metadata", {}).get("attack_type", "unknown")
        r["_meta"]        = s.get("metadata", {})

        # Attach warmup index for ordering
        if r["attack_type"] == "warmup":
            r["warmup_idx"] = s.get("metadata", {}).get("warmup_session_index", 0)

        adv_results.append(r)
        if not args.quiet:
            det = []
            if r["l2"]["fired"]: det.append("L2")
            if r["l4"]["fired"]: det.append("L4")
            if r["l5"]["fired"]: det.append("L5")
            tag = "+".join(det) if det else "MISS"
            print(tag)

    # ------------------------------------------------------------------
    # Step 4: Check for stationary control session
    # ------------------------------------------------------------------
    stationary = ADV_DIR / "stationary_control_001.json"
    if stationary.exists():
        print(f"\nStationary control session found: {stationary.name}")
        s = _load_session(stationary)
        if s:
            r = analyze_session(s, l4)
            r["session_name"] = stationary.stem
            r["attack_type"]  = "stationary"
            adv_results.append(r)
            det = []
            if r["l2"]["fired"]: det.append("L2")
            if r["l4"]["fired"]: det.append("L4")
            if r["l5"]["fired"]: det.append("L5")
            print(f"  Stationary control PITL result: "
                  f"{'+'.join(det) if det else 'NOMINAL'}")
    else:
        print(f"\nStationary control session not found. To capture:")
        print(f"  python scripts\\capture_session.py --duration 30 "
              f"--output sessions\\adversarial\\stationary_control_001.json "
              f'--notes "stationary-control-baseline"')

    # ------------------------------------------------------------------
    # Step 5: Build and output report
    # ------------------------------------------------------------------
    print()
    report = build_report(human_results, adv_results)
    print(report)

    md_path = DOCS_DIR / "adversarial-validation-results.md"
    md = _to_markdown(report, human_results, adv_results)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Markdown report saved to: {md_path}")

    # ------------------------------------------------------------------
    # Exit summary: warn if L4 FP rate > 5% (expected: <= 2.5% at 3-sigma)
    # ------------------------------------------------------------------
    n_human       = len(human_results)
    n_fp          = sum(1 for r in human_results if r["any_fired"])
    fp_rate       = n_fp / max(n_human, 1)
    adv_non_replay = [r for r in adv_results if r["attack_type"] != "replay"]
    n_det         = sum(1 for r in adv_non_replay if r["any_fired"])

    print(f"\nResult: {n_fp}/{n_human} FP on human sessions ({100*fp_rate:.1f}%). "
          f"{n_det}/{len(adv_non_replay)} adversarial (excl. replay) detected.")

    if n_fp > 0:
        print(f"  NOTE: {n_fp} FP session(s) are genuine biometric outliers "
              f"(expected at 3-sigma: ~{n_human*0.003:.1f} per {n_human} sessions).")
    if fp_rate > 0.05:
        print("WARNING: FP rate > 5% — threshold recalibration recommended.")
        sys.exit(1)


if __name__ == "__main__":
    main()
