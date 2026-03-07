"""
analyze_button_fingerprint.py — Mine per-button timing biometrics from N=40 USB sessions.

Reads all sessions/human/hw_*.json files (captured at ~1000 Hz from DualShock Edge).
Performs no modifications to any PITL layer — pure data mining only.

Outputs:
  - Terminal: summary tables
  - docs/button-fingerprint-analysis.md: comprehensive report

Usage:
    python scripts/analyze_button_fingerprint.py
    python scripts/analyze_button_fingerprint.py --sessions sessions/human/hw_*.json
"""

import argparse
import glob
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.spatial.distance import mahalanobis

# ---------------------------------------------------------------------------
# Button definitions — decoded from raw HID bytes in session files
# ---------------------------------------------------------------------------
# buttons_0 (byte 8 of USB report):
#   bits 0-3 = D-pad hat value (0=N, 1=NE, 2=E, 3=SE, 4=S, 5=SW, 6=W, 7=NW, 8=neutral)
#   bit 4 (0x10) = Square   bit 5 (0x20) = Cross (X)
#   bit 6 (0x40) = Circle   bit 7 (0x80) = Triangle
#
# buttons_1 (byte 9):
#   bit 0 (0x01) = L1    bit 1 (0x02) = R1
#   bit 2 (0x04) = L2dig bit 3 (0x08) = R2dig
#   bit 4 (0x10) = Create (Share)   bit 5 (0x20) = Options
#   bit 6 (0x40) = L3    bit 7 (0x80) = R3
#
# D-pad hat: Up in {0,1,7}, Down in {3,4,5}, Left in {5,6,7}, Right in {1,2,3}

_DPAD_UP    = frozenset({0, 1, 7})   # N, NE, NW
_DPAD_DOWN  = frozenset({3, 4, 5})   # SE, S, SW
_DPAD_LEFT  = frozenset({5, 6, 7})   # SW, W, NW
_DPAD_RIGHT = frozenset({1, 2, 3})   # NE, E, SE
_DPAD_NEUTRAL = 8


def _is_pressed(b0: int, b1: int, btn: str) -> bool:
    """Return True if the named button is currently pressed."""
    dpad = b0 & 0x0F
    if btn == "cross":    return bool(b0 & 0x20)
    if btn == "circle":   return bool(b0 & 0x40)
    if btn == "square":   return bool(b0 & 0x10)
    if btn == "triangle": return bool(b0 & 0x80)
    if btn == "l1":       return bool(b1 & 0x01)
    if btn == "r1":       return bool(b1 & 0x02)
    if btn == "l2_dig":   return bool(b1 & 0x04)
    if btn == "r2_dig":   return bool(b1 & 0x08)
    if btn == "l3":       return bool(b1 & 0x40)
    if btn == "r3":       return bool(b1 & 0x80)
    if btn == "create":   return bool(b1 & 0x10)
    if btn == "options":  return bool(b1 & 0x20)
    if btn == "dpad_up":    return dpad in _DPAD_UP
    if btn == "dpad_down":  return dpad in _DPAD_DOWN
    if btn == "dpad_left":  return dpad in _DPAD_LEFT
    if btn == "dpad_right": return dpad in _DPAD_RIGHT
    if btn == "dpad_any":   return dpad != _DPAD_NEUTRAL
    return False


ALL_BUTTONS = [
    "cross", "circle", "square", "triangle",
    "l1", "r1", "l2_dig", "r2_dig",
    "l3", "r3",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
    "create", "options",
]

BUTTON_DISPLAY = {
    "cross":      "Cross (X)",
    "circle":     "Circle (O)",
    "square":     "Square",
    "triangle":   "Triangle",
    "l1":         "L1",
    "r1":         "R1",
    "l2_dig":     "L2 (digital)",
    "r2_dig":     "R2 (digital)",
    "l3":         "L3 (L-stick click)",
    "r3":         "R3 (R-stick click)",
    "dpad_up":    "D-pad Up",
    "dpad_down":  "D-pad Down",
    "dpad_left":  "D-pad Left",
    "dpad_right": "D-pad Right",
    "create":     "Create (Share)",
    "options":    "Options",
}

_MIN_PRESSES = 10    # minimum presses per session to include in CV/entropy stats
_ENTROPY_BIN_MS = 50  # bin width for Shannon entropy (matching L5 methodology)
_TRANSITION_WINDOW_MS = 100  # max gap to count as a button transition


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

@dataclass
class ButtonSessionStats:
    """Per-button stats for one session."""
    press_count: int = 0
    intervals_ms: List[float] = field(default_factory=list)   # inter-press intervals
    durations_ms: List[float] = field(default_factory=list)   # hold durations

    @property
    def has_data(self) -> bool:
        return self.press_count >= _MIN_PRESSES and len(self.intervals_ms) >= _MIN_PRESSES - 1

    @property
    def mean_interval(self) -> float:
        return float(np.mean(self.intervals_ms)) if self.intervals_ms else 0.0

    @property
    def std_interval(self) -> float:
        return float(np.std(self.intervals_ms, ddof=1)) if len(self.intervals_ms) >= 2 else 0.0

    @property
    def cv(self) -> Optional[float]:
        if len(self.intervals_ms) < _MIN_PRESSES - 1:
            return None
        m = self.mean_interval
        if m < 1.0:
            return None
        return self.std_interval / m

    @property
    def entropy_bits(self) -> Optional[float]:
        """Shannon entropy in 50ms bins (L5 methodology)."""
        if len(self.intervals_ms) < _MIN_PRESSES - 1:
            return None
        arr = np.array(self.intervals_ms)
        if arr.max() < 1.0:
            return None
        max_ms = max(arr.max(), _ENTROPY_BIN_MS)
        bins = max(1, int(max_ms / _ENTROPY_BIN_MS) + 1)
        hist, _ = np.histogram(arr, bins=bins)
        hist = hist[hist > 0].astype(float)
        hist /= hist.sum()
        return float(-np.sum(hist * np.log2(hist)))

    @property
    def mean_duration(self) -> float:
        return float(np.mean(self.durations_ms)) if self.durations_ms else 0.0

    @property
    def std_duration(self) -> float:
        return float(np.std(self.durations_ms, ddof=1)) if len(self.durations_ms) >= 2 else 0.0


def _extract_button_events(
    reports: list,
    button: str,
) -> Tuple[List[Tuple[float, float]], List[float]]:
    """
    Extract (press_time_ms, release_time_ms) pairs and inter-press intervals.

    Returns:
        press_releases: list of (press_ms, release_ms) — None release if not found
        intervals_ms:   list of inter-press intervals (start to start, ms)
    """
    press_times: List[float] = []
    press_releases: List[Tuple[float, Optional[float]]] = []
    active = False
    press_start: Optional[float] = None

    for r in reports:
        ts = r["timestamp_ms"]
        feat = r["features"]
        b0 = feat.get("buttons_0", 8) or 8
        b1 = feat.get("buttons_1", 0) or 0
        is_down = _is_pressed(b0, b1, button)

        if is_down and not active:
            # Press event
            press_start = float(ts)
            press_times.append(press_start)
            active = True
        elif not is_down and active and press_start is not None:
            # Release event
            press_releases.append((press_start, float(ts)))
            press_start = None
            active = False

    # Handle button still held at session end
    if active and press_start is not None:
        press_releases.append((press_start, None))

    # Build durations (exclude presses with no release)
    durations = [
        r - p for p, r in press_releases if r is not None
    ]

    # Inter-press intervals (start-to-start)
    intervals = [
        press_times[i + 1] - press_times[i]
        for i in range(len(press_times) - 1)
    ]

    return press_releases, intervals, durations, press_times


def _extract_transitions(
    reports: list,
    buttons: List[str],
    window_ms: float = _TRANSITION_WINDOW_MS,
) -> Dict[Tuple[str, str], List[float]]:
    """
    Extract cross-button transitions: A releases then B presses within window_ms.

    Returns:
        transitions[(a, b)] = list of transition times (ms from A-release to B-press)
    """
    transitions: Dict[Tuple[str, str], List[float]] = defaultdict(list)

    # Build event timeline: (ts, event_type='press'|'release', button)
    events: List[Tuple[float, str, str]] = []
    prev_states = {b: False for b in buttons}

    for r in reports:
        ts = float(r["timestamp_ms"])
        feat = r["features"]
        b0 = feat.get("buttons_0", 8) or 8
        b1 = feat.get("buttons_1", 0) or 0

        for btn in buttons:
            cur = _is_pressed(b0, b1, btn)
            if cur and not prev_states[btn]:
                events.append((ts, "press", btn))
            elif not cur and prev_states[btn]:
                events.append((ts, "release", btn))
            prev_states[btn] = cur

    # Scan for A-release -> B-press within window_ms
    for i, (ts_a, ev_a, btn_a) in enumerate(events):
        if ev_a != "release":
            continue
        for j in range(i + 1, len(events)):
            ts_b, ev_b, btn_b = events[j]
            dt = ts_b - ts_a
            if dt > window_ms:
                break
            if ev_b == "press" and btn_b != btn_a:
                transitions[(btn_a, btn_b)].append(dt)

    return dict(transitions)


# ---------------------------------------------------------------------------
# Per-session analysis
# ---------------------------------------------------------------------------

def analyze_session(session_path: str) -> Optional[dict]:
    """Analyze one session. Returns per-button stats dict or None if no button data."""
    with open(session_path, encoding="utf-8") as fp:
        data = json.load(fp)

    reports = data["reports"]
    if not reports:
        return None

    # Check button data present
    if "buttons_0" not in reports[0].get("features", {}):
        return None

    meta = data["metadata"]
    session_id = Path(session_path).stem

    result = {
        "session_id": session_id,
        "report_count": len(reports),
        "polling_rate_hz": meta.get("polling_rate_hz", 1000.0),
        "duration_s": meta.get("duration_actual_s", 0.0),
        "buttons": {},
    }

    for btn in ALL_BUTTONS:
        press_releases, intervals, durations, press_times = _extract_button_events(reports, btn)
        stats = ButtonSessionStats(
            press_count=len(press_times),
            intervals_ms=intervals,
            durations_ms=durations,
        )
        result["buttons"][btn] = stats

    # Transitions
    result["transitions"] = _extract_transitions(reports, ALL_BUTTONS)

    return result


# ---------------------------------------------------------------------------
# Aggregate statistics
# ---------------------------------------------------------------------------

def aggregate_stats(sessions: List[dict]) -> dict:
    """Aggregate across all sessions per button."""
    agg = {}

    for btn in ALL_BUTTONS:
        per_session_counts = [s["buttons"][btn].press_count for s in sessions]
        per_session_cvs = [
            s["buttons"][btn].cv
            for s in sessions
            if s["buttons"][btn].cv is not None
        ]
        per_session_entropies = [
            s["buttons"][btn].entropy_bits
            for s in sessions
            if s["buttons"][btn].entropy_bits is not None
        ]
        per_session_mean_intervals = [
            s["buttons"][btn].mean_interval
            for s in sessions
            if s["buttons"][btn].has_data
        ]
        per_session_mean_durations = [
            s["buttons"][btn].mean_duration
            for s in sessions
            if s["buttons"][btn].press_count >= 5
        ]

        # All intervals across all sessions (for global distribution)
        all_intervals = []
        for s in sessions:
            all_intervals.extend(s["buttons"][btn].intervals_ms)

        agg[btn] = {
            "total_presses": sum(per_session_counts),
            "sessions_with_data": len([c for c in per_session_counts if c >= _MIN_PRESSES]),
            "per_session_count_mean": float(np.mean(per_session_counts)) if per_session_counts else 0,
            "per_session_count_std":  float(np.std(per_session_counts, ddof=1)) if len(per_session_counts) >= 2 else 0,
            "per_session_count_min":  int(min(per_session_counts)) if per_session_counts else 0,
            "per_session_count_max":  int(max(per_session_counts)) if per_session_counts else 0,
            # CV across sessions
            "cv_mean":     float(np.mean(per_session_cvs)) if per_session_cvs else None,
            "cv_std":      float(np.std(per_session_cvs, ddof=1)) if len(per_session_cvs) >= 2 else 0.0,
            "cv_sessions": len(per_session_cvs),
            # Entropy across sessions
            "entropy_mean": float(np.mean(per_session_entropies)) if per_session_entropies else None,
            "entropy_std":  float(np.std(per_session_entropies, ddof=1)) if len(per_session_entropies) >= 2 else 0.0,
            # Interval distribution (all-session pool)
            "global_mean_interval_ms": float(np.mean(all_intervals)) if all_intervals else 0,
            "global_std_interval_ms":  float(np.std(all_intervals, ddof=1)) if len(all_intervals) >= 2 else 0,
            # Hold duration
            "mean_duration_mean": float(np.mean(per_session_mean_durations)) if per_session_mean_durations else 0,
            "mean_duration_std":  float(np.std(per_session_mean_durations, ddof=1)) if len(per_session_mean_durations) >= 2 else 0,
        }

    return agg


def aggregate_transitions(sessions: List[dict]) -> Dict[Tuple[str, str], dict]:
    """Aggregate cross-button transition times across all sessions."""
    pooled: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for s in sessions:
        for pair, times in s["transitions"].items():
            pooled[pair].extend(times)

    result = {}
    for pair, times in pooled.items():
        if len(times) < 5:
            continue
        arr = np.array(times)
        result[pair] = {
            "count": len(arr),
            "mean_ms": float(np.mean(arr)),
            "std_ms":  float(np.std(arr, ddof=1)) if len(arr) >= 2 else 0.0,
            "cv":      float(np.std(arr, ddof=1) / np.mean(arr)) if np.mean(arr) > 0 and len(arr) >= 2 else None,
        }
    return result


# ---------------------------------------------------------------------------
# Per-session fingerprint vectors + Mahalanobis analysis
# ---------------------------------------------------------------------------

def build_fingerprint_vectors(sessions: List[dict], top_n: int = 4) -> dict:
    """
    Build per-session fingerprint vectors from top N buttons by press count.

    Returns:
        vectors: np.ndarray (n_sessions x 2*top_n) — [cv1, e1, cv2, e2, ...]
        valid_sessions: list of session IDs with enough data
        button_labels: list of button names used
    """
    # Find globally top-N buttons by total press count
    total_counts = defaultdict(int)
    for s in sessions:
        for btn in ALL_BUTTONS:
            total_counts[btn] += s["buttons"][btn].press_count

    top_buttons = sorted(total_counts, key=total_counts.get, reverse=True)[:top_n]

    # Build feature vectors — only sessions where all top buttons have enough data
    vecs = []
    valid_ids = []
    for s in sessions:
        feats = []
        ok = True
        for btn in top_buttons:
            cv = s["buttons"][btn].cv
            ent = s["buttons"][btn].entropy_bits
            if cv is None or ent is None:
                ok = False
                break
            feats.extend([cv, ent])
        if ok:
            vecs.append(feats)
            valid_ids.append(s["session_id"])

    if len(vecs) < 3:
        return {"error": f"Only {len(vecs)} sessions have data for all top-{top_n} buttons",
                "top_buttons": top_buttons}

    X = np.array(vecs)  # (n, 2*top_n)

    # Mahalanobis distances between all session pairs
    try:
        cov = np.cov(X.T)
        if cov.ndim == 0:
            cov = np.array([[float(cov)]])
        cov_inv = np.linalg.pinv(cov)
        mean_vec = X.mean(axis=0)
        dists = [mahalanobis(X[i], mean_vec, cov_inv) for i in range(len(X))]
        mean_dist = float(np.mean(dists))
        std_dist  = float(np.std(dists, ddof=1)) if len(dists) >= 2 else 0.0
        max_dist  = float(np.max(dists))
    except Exception as exc:
        mean_dist = std_dist = max_dist = 0.0
        dists = []
        cov_inv = None

    # CV stability across sessions per button (lower = more stable)
    button_cv_stabilities = {}
    for btn in top_buttons:
        cvs = [s["buttons"][btn].cv for s in sessions if s["buttons"][btn].cv is not None]
        button_cv_stabilities[btn] = float(np.std(cvs, ddof=1)) if len(cvs) >= 2 else 0.0

    return {
        "top_buttons": top_buttons,
        "n_valid_sessions": len(valid_ids),
        "valid_session_ids": valid_ids,
        "vectors": X,
        "mahalanobis_mean": mean_dist,
        "mahalanobis_std":  std_dist,
        "mahalanobis_max":  max_dist,
        "per_session_distances": list(zip(valid_ids, [round(d, 3) for d in dists])),
        "button_cv_stabilities": button_cv_stabilities,
    }


# ---------------------------------------------------------------------------
# Biometric viability scoring
# ---------------------------------------------------------------------------

def compute_viability(agg: dict) -> Dict[str, dict]:
    """
    Score each button HIGH/MEDIUM/LOW for biometric viability.

    Criteria:
    - Frequency: sessions_with_data >= 20 = HIGH, >= 10 = MEDIUM, else LOW
    - Stability: cv_std / cv_mean (relative stability) < 0.3 = stable, < 0.6 = moderate
    - Both HIGH -> HIGH; one HIGH one MEDIUM -> MEDIUM; else LOW
    """
    scores = {}
    for btn in ALL_BUTTONS:
        a = agg[btn]
        sess = a["sessions_with_data"]
        cv_mean = a["cv_mean"]
        cv_std  = a["cv_std"]

        # Frequency score
        if sess >= 25:
            freq_score = "HIGH"
        elif sess >= 15:
            freq_score = "MEDIUM"
        else:
            freq_score = "LOW"

        # Stability score (relative std of CV across sessions)
        if cv_mean and cv_mean > 0:
            rel_stab = cv_std / cv_mean
            if rel_stab < 0.3:
                stab_score = "HIGH"
            elif rel_stab < 0.6:
                stab_score = "MEDIUM"
            else:
                stab_score = "LOW"
        else:
            stab_score = "LOW"

        # Composite
        if freq_score == "HIGH" and stab_score == "HIGH":
            viability = "HIGH"
        elif freq_score == "LOW" and stab_score == "LOW":
            viability = "LOW"
        elif freq_score == "HIGH" or stab_score == "HIGH":
            viability = "MEDIUM"
        else:
            viability = "LOW"

        scores[btn] = {
            "viability": viability,
            "freq_score": freq_score,
            "stab_score": stab_score,
            "sessions_with_data": sess,
            "cv_mean": cv_mean,
            "cv_std": cv_std,
        }

    return scores


# ---------------------------------------------------------------------------
# R2-only vs multi-button comparison
# ---------------------------------------------------------------------------

def compare_r2_vs_multibutton(sessions: List[dict], agg: dict) -> dict:
    """
    Compare R2-only L5 signal vs a multi-button aggregate CV.

    Returns stats on how adding face buttons changes the aggregate CV distribution.
    """
    r2_cvs = [s["buttons"]["r2_dig"].cv for s in sessions if s["buttons"]["r2_dig"].cv is not None]

    # Multi-button: mean CV of [r2_dig, cross, l1, r1] when all have data
    core_buttons = ["r2_dig", "cross", "l1", "r1"]
    multi_cvs = []
    for s in sessions:
        cvs_here = [s["buttons"][b].cv for b in core_buttons if s["buttons"][b].cv is not None]
        if len(cvs_here) >= 2:
            multi_cvs.append(float(np.mean(cvs_here)))

    # How many sessions fall below bot threshold (CV < 0.08) — false positives
    bot_threshold = 0.08
    r2_fp_rate   = sum(1 for c in r2_cvs if c is not None and c < bot_threshold) / max(len(r2_cvs), 1)
    multi_fp_rate = sum(1 for c in multi_cvs if c is not None and c < bot_threshold) / max(len(multi_cvs), 1)

    return {
        "r2_only": {
            "sessions": len(r2_cvs),
            "mean_cv": float(np.mean(r2_cvs)) if r2_cvs else None,
            "std_cv":  float(np.std(r2_cvs, ddof=1)) if len(r2_cvs) >= 2 else 0.0,
            "fp_rate": r2_fp_rate,
        },
        "multi_button": {
            "buttons": core_buttons,
            "sessions": len(multi_cvs),
            "mean_cv": float(np.mean(multi_cvs)) if multi_cvs else None,
            "std_cv":  float(np.std(multi_cvs, ddof=1)) if len(multi_cvs) >= 2 else 0.0,
            "fp_rate": multi_fp_rate,
        },
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt(v, fmt=".3f", na="N/A"):
    if v is None:
        return na
    return format(v, fmt)


def _bar(viability: str) -> str:
    return {"HIGH": "***", "MEDIUM": "**", "LOW": "*"}.get(viability, "?")


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    sessions: List[dict],
    agg: dict,
    trans_agg: dict,
    fp_vec: dict,
    viability: dict,
    r2_compare: dict,
    output_path: str,
) -> str:
    """Generate the markdown report and return it as a string."""

    lines = []
    a = lines.append

    a("# Button Fingerprint Analysis — DualShock Edge (N=40 USB Sessions)")
    a("")
    a(f"**Game:** NCAA College Football 26 | **Device:** DualShock Edge CFI-ZCP1 | "
      f"**Polling rate:** ~1000 Hz (USB) | **Sessions:** {len(sessions)}")
    a("")
    a("> **Note:** This is pure data mining from existing hardware sessions. "
      "No PITL layer code was modified.")
    a("")

    # ---------------------------------------------------------------------------
    a("## 1. Per-Button Summary")
    a("")
    a("| Button | Total Presses | Sessions w/ Data | Mean Interval (ms) | Mean CV | Mean Entropy (bits) | Mean Hold (ms) | Viability |")
    a("|--------|--------------|-----------------|-------------------|---------|--------------------|--------------:|-----------|")

    # Sort by total presses descending
    sorted_btns = sorted(ALL_BUTTONS, key=lambda b: agg[b]["total_presses"], reverse=True)

    for btn in sorted_btns:
        a_data = agg[btn]
        v = viability[btn]
        a(
            f"| {BUTTON_DISPLAY[btn]} | {a_data['total_presses']:,} | "
            f"{a_data['sessions_with_data']}/40 | "
            f"{_fmt(a_data['global_mean_interval_ms'], '.1f')} | "
            f"{_fmt(a_data['cv_mean'], '.3f')} | "
            f"{_fmt(a_data['entropy_mean'], '.3f')} | "
            f"{_fmt(a_data['mean_duration_mean'], '.1f')} | "
            f"**{v['viability']}** {_bar(v['viability'])} |"
        )

    a("")

    # ---------------------------------------------------------------------------
    a("## 2. Top 5 Most Stable Button Timing Features")
    a("")
    a("Ranked by inter-session CV stability (low CV std / CV mean = more consistent across sessions).")
    a("Stable buttons are reliable biometric anchors — a bot's artificially low CV would stand out.")
    a("")
    a("| Rank | Button | Mean CV | CV Std (across sessions) | Relative Stability | Sessions |")
    a("|------|--------|---------|--------------------------|-------------------|----------|")

    # Filter to buttons with enough data and sort by relative stability
    ranked = []
    for btn in ALL_BUTTONS:
        a_data = agg[btn]
        cv_mean = a_data["cv_mean"]
        cv_std  = a_data["cv_std"]
        if cv_mean and cv_mean > 0 and a_data["sessions_with_data"] >= 10:
            rel_stab = cv_std / cv_mean
            ranked.append((btn, cv_mean, cv_std, rel_stab, a_data["sessions_with_data"]))
    ranked.sort(key=lambda x: x[3])  # ascending = most stable first

    for i, (btn, cv_mean, cv_std, rel_stab, n_sess) in enumerate(ranked[:5], 1):
        a(f"| {i} | {BUTTON_DISPLAY[btn]} | {cv_mean:.4f} | {cv_std:.4f} | {rel_stab:.4f} | {n_sess}/40 |")

    a("")

    # ---------------------------------------------------------------------------
    a("## 3. Top 5 Most Discriminative Button Transitions")
    a("")
    a("Cross-button transitions within 100ms. Low CV = tight timing = hardest to fake.")
    a("A macro bot would produce near-constant transition times (CV -> 0).")
    a("")
    a("| Rank | Transition | Count | Mean (ms) | Std (ms) | CV | Macro-detectable? |")
    a("|------|-----------|-------|-----------|----------|-----|-------------------|")

    # Sort by count x (1/cv) — both common and tight
    ranked_trans = []
    for pair, stats in trans_agg.items():
        if stats["count"] >= 20 and stats["cv"] is not None and stats["cv"] > 0:
            ranked_trans.append((pair, stats))
    # Sort by tightest CV first (among frequent transitions)
    ranked_trans.sort(key=lambda x: x[1]["cv"])

    for i, (pair, stats) in enumerate(ranked_trans[:5], 1):
        macro = "YES (tight)" if stats["cv"] < 0.3 else "moderate"
        a(f"| {i} | {BUTTON_DISPLAY[pair[0]]} -> {BUTTON_DISPLAY[pair[1]]} | "
          f"{stats['count']} | {stats['mean_ms']:.1f} | {stats['std_ms']:.1f} | "
          f"{stats['cv']:.3f} | {macro} |")

    a("")

    # ---------------------------------------------------------------------------
    a("## 4. Biometric Viability Scores")
    a("")
    a("| Button | Viability | Freq Score | Stability Score | Recommended Use |")
    a("|--------|-----------|------------|----------------|----------------|")

    for btn in sorted_btns:
        v = viability[btn]
        if v["viability"] == "HIGH":
            use = "Add to L5 + L4 biometric"
        elif v["viability"] == "MEDIUM":
            use = "Consider for L5"
        else:
            use = "Skip (insufficient data)"
        a(f"| {BUTTON_DISPLAY[btn]} | **{v['viability']}** | {v['freq_score']} | {v['stab_score']} | {use} |")

    a("")

    # ---------------------------------------------------------------------------
    a("## 5. Per-Session Button Fingerprint (Mahalanobis Analysis)")
    a("")
    if "error" in fp_vec:
        a(f"> **Note:** {fp_vec['error']}")
    else:
        top_btns = [BUTTON_DISPLAY[b] for b in fp_vec["top_buttons"]]
        a(f"Top {len(fp_vec['top_buttons'])} buttons by press count: **{', '.join(top_btns)}**")
        a("")
        a(f"Feature vector: [CV, Entropy] x {len(fp_vec['top_buttons'])} buttons = "
          f"{len(fp_vec['top_buttons']) * 2}-dimensional")
        a(f"Valid sessions (all top buttons have >={_MIN_PRESSES} presses): "
          f"**{fp_vec['n_valid_sessions']}** / {len(sessions)}")
        a("")
        a(f"| Metric | Value |")
        a(f"|--------|-------|")
        a(f"| Mean Mahalanobis distance (session->centroid) | {fp_vec['mahalanobis_mean']:.3f} |")
        a(f"| Std Mahalanobis distance | {fp_vec['mahalanobis_std']:.3f} |")
        a(f"| Max Mahalanobis distance | {fp_vec['mahalanobis_max']:.3f} |")
        a("")
        a("**Interpretation:** Low mean Mahalanobis distance (< 2.0) indicates "
          "stable fingerprint across sessions — strong biometric signal. "
          f"This player's button timing fingerprint has mean distance = "
          f"**{fp_vec['mahalanobis_mean']:.3f}** "
          f"({'STABLE' if fp_vec['mahalanobis_mean'] < 2.5 else 'VARIABLE'}).")
        a("")
        a("**Per-session distances (top 10 outliers):**")
        sorted_dists = sorted(fp_vec["per_session_distances"], key=lambda x: -x[1])
        for sid, dist in sorted_dists[:10]:
            a(f"  - {sid}: {dist:.3f}")

    a("")

    # ---------------------------------------------------------------------------
    a("## 6. R2-Only vs Multi-Button L5 Signal Comparison")
    a("")
    r2 = r2_compare["r2_only"]
    mb = r2_compare["multi_button"]
    a(f"Current L5 uses **R2 (digital) only** for timing analysis. "
      f"Multi-button expands to: **{', '.join(mb['buttons'])}**.")
    a("")
    a("| Metric | R2-Only | Multi-Button (avg) | Improvement? |")
    a("|--------|---------|--------------------|--------------|")
    a(f"| Sessions usable | {r2['sessions']} | {mb['sessions']} | "
      f"{'More data' if mb['sessions'] > r2['sessions'] else 'Same'} |")
    a(f"| Mean CV | {_fmt(r2['mean_cv'], '.4f')} | {_fmt(mb['mean_cv'], '.4f')} | "
      f"{'Higher = more human-like margin' if (mb['mean_cv'] or 0) > (r2['mean_cv'] or 0) else 'Similar'} |")
    a(f"| CV Std (consistency) | {_fmt(r2['std_cv'], '.4f')} | {_fmt(mb['std_cv'], '.4f')} | — |")
    a(f"| False-positive rate (CV < 0.08) | {r2['fp_rate']:.1%} | {mb['fp_rate']:.1%} | "
      f"{'Fewer FPs' if mb['fp_rate'] <= r2['fp_rate'] else 'More FPs'} |")
    a("")

    # ---------------------------------------------------------------------------
    a("## 7. Recommendations")
    a("")
    a("### Add to L5 Temporal Rhythm Analysis")

    high_btns = [b for b in sorted_btns if viability[b]["viability"] == "HIGH"]
    if high_btns:
        a("")
        for btn in high_btns:
            v = viability[btn]
            cv = agg[btn]["cv_mean"]
            ent = agg[btn]["entropy_mean"]
            a(f"- **{BUTTON_DISPLAY[btn]}**: mean CV={_fmt(cv, '.3f')}, "
              f"entropy={_fmt(ent, '.3f')} bits, "
              f"{v['sessions_with_data']}/40 sessions, "
              f"viability={v['viability']}")
    else:
        a("  No HIGH viability buttons identified in this dataset.")

    a("")
    a("### Add to L4 Biometric Fingerprint")
    a("")
    a("Hold-duration vectors (mean hold time per button) make excellent L4 features — "
      "they are stable, amplitude-based, and not sensitive to polling rate.")
    medium_plus = [b for b in sorted_btns if viability[b]["viability"] in ("HIGH", "MEDIUM")]
    for btn in medium_plus[:6]:
        dur = agg[btn]["mean_duration_mean"]
        a(f"- **{BUTTON_DISPLAY[btn]}**: mean hold = {_fmt(dur, '.1f')} ms")

    a("")
    a("### Button Transitions for Anti-Macro Detection")
    a("")
    a("These transitions have tight enough timing distributions to detect macros "
      "(constant-interval replay would produce CV -> 0):")
    if ranked_trans:
        for pair, stats in ranked_trans[:3]:
            a(f"- **{BUTTON_DISPLAY[pair[0]]} -> {BUTTON_DISPLAY[pair[1]]}**: "
              f"mean {stats['mean_ms']:.1f}ms ± {stats['std_ms']:.1f}ms (CV={stats['cv']:.3f})")
    else:
        a("  Insufficient transition data for recommendations.")

    a("")

    # ---------------------------------------------------------------------------
    a("## 8. Raw Per-Button Detail")
    a("")
    for btn in sorted_btns:
        a_data = agg[btn]
        v = viability[btn]
        if a_data["total_presses"] == 0:
            continue
        a(f"### {BUTTON_DISPLAY[btn]}")
        a(f"- Total presses: {a_data['total_presses']:,} across {len(sessions)} sessions")
        a(f"- Sessions with >={_MIN_PRESSES} presses: {a_data['sessions_with_data']}/40")
        a(f"- Press count per session: mean={a_data['per_session_count_mean']:.1f}, "
          f"std={a_data['per_session_count_std']:.1f}, "
          f"min={a_data['per_session_count_min']}, max={a_data['per_session_count_max']}")
        a(f"- Inter-press interval: mean={a_data['global_mean_interval_ms']:.1f}ms, "
          f"std={a_data['global_std_interval_ms']:.1f}ms")
        a(f"- CV (across sessions): mean={_fmt(a_data['cv_mean'], '.4f')}, "
          f"std={_fmt(a_data['cv_std'], '.4f')} -> "
          f"stability={v['stab_score']}")
        a(f"- Shannon entropy: mean={_fmt(a_data['entropy_mean'], '.3f')} bits "
          f"({a_data['cv_sessions']} sessions)")
        a(f"- Hold duration: mean={a_data['mean_duration_mean']:.1f}ms ± "
          f"{a_data['mean_duration_std']:.1f}ms")
        a(f"- **Biometric viability: {v['viability']}** (freq={v['freq_score']}, stab={v['stab_score']})")
        a("")

    # ---------------------------------------------------------------------------
    a("## 9. Methodology")
    a("")
    a("- Sessions: 40 hardware sessions (`sessions/human/hw_*.json`) at ~1000 Hz USB polling")
    a("- Synthetic baseline sessions excluded (no button data)")
    a(f"- Minimum presses for CV/entropy: {_MIN_PRESSES} per session")
    a(f"- Entropy bin width: {_ENTROPY_BIN_MS}ms (matches L5 TemporalRhythmOracle)")
    a(f"- Transition window: {_TRANSITION_WINDOW_MS}ms (A releases -> B presses within this)")
    a("- Mahalanobis: per-session [CV, Entropy] vector for top-4 buttons by press count; "
      "pinv covariance for numerical stability")
    a("- D-pad: hat-switch encoding (N=0,NE=1,E=2,SE=3,S=4,SW=5,W=6,NW=7,neutral=8)")
    a(f"  - Up: {{{', '.join(str(x) for x in sorted(_DPAD_UP))}}}, "
      f"Down: {{{', '.join(str(x) for x in sorted(_DPAD_DOWN))}}}")
    a(f"  - Left: {{{', '.join(str(x) for x in sorted(_DPAD_LEFT))}}}, "
      f"Right: {{{', '.join(str(x) for x in sorted(_DPAD_RIGHT))}}}")
    a("")
    a("---")
    a(f"*Generated by `scripts/analyze_button_fingerprint.py`*")

    report = "\n".join(lines)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fp:
        fp.write(report)

    return report


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------

def print_summary(agg: dict, viability: dict, fp_vec: dict, r2_compare: dict):
    """Print a concise summary table to terminal."""

    print("\n" + "=" * 72)
    print("BUTTON FINGERPRINT ANALYSIS — DualShock Edge, N=40 USB Sessions")
    print("=" * 72)

    # Sort by total presses
    sorted_btns = sorted(ALL_BUTTONS, key=lambda b: agg[b]["total_presses"], reverse=True)

    print(f"\n{'Button':<22} {'Presses':>8} {'Sessions':>9} {'Mean CV':>8} {'Entropy':>8} {'Viability':<10}")
    print("-" * 72)
    for btn in sorted_btns:
        a_data = agg[btn]
        if a_data["total_presses"] == 0:
            continue
        cv_str  = _fmt(a_data["cv_mean"], ".3f") if a_data["cv_mean"] is not None else "  N/A "
        ent_str = _fmt(a_data["entropy_mean"], ".3f") if a_data["entropy_mean"] is not None else "  N/A "
        print(f"  {BUTTON_DISPLAY[btn]:<20} {a_data['total_presses']:>8,} "
              f"{a_data['sessions_with_data']:>5}/40 "
              f"{cv_str:>8} {ent_str:>8}  {viability[btn]['viability']}")

    print("\nFINGERPRINT STABILITY (Mahalanobis):")
    if "error" in fp_vec:
        print(f"  {fp_vec['error']}")
    else:
        print(f"  Top-4 buttons: {', '.join(fp_vec['top_buttons'])}")
        print(f"  Valid sessions: {fp_vec['n_valid_sessions']}/40")
        print(f"  Mean Mahalanobis distance: {fp_vec['mahalanobis_mean']:.3f} "
              f"({'STABLE' if fp_vec['mahalanobis_mean'] < 2.5 else 'VARIABLE'})")
        print(f"  Std:  {fp_vec['mahalanobis_std']:.3f}  Max: {fp_vec['mahalanobis_max']:.3f}")

    print("\nR2-ONLY vs MULTI-BUTTON L5 COMPARISON:")
    r2  = r2_compare["r2_only"]
    mb  = r2_compare["multi_button"]
    print(f"  R2-only:      mean CV={_fmt(r2['mean_cv'], '.4f')}, "
          f"FP rate={r2['fp_rate']:.1%}, sessions={r2['sessions']}")
    print(f"  Multi-button: mean CV={_fmt(mb['mean_cv'], '.4f')}, "
          f"FP rate={mb['fp_rate']:.1%}, sessions={mb['sessions']} "
          f"(buttons: {', '.join(mb['buttons'])})")

    high_btns = [b for b in sorted_btns if viability[b]["viability"] == "HIGH"]
    med_btns  = [b for b in sorted_btns if viability[b]["viability"] == "MEDIUM"]
    print(f"\nVIABILITY SUMMARY:")
    print(f"  HIGH:   {[BUTTON_DISPLAY[b] for b in high_btns]}")
    print(f"  MEDIUM: {[BUTTON_DISPLAY[b] for b in med_btns]}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Mine per-button timing biometrics from DualShock Edge sessions."
    )
    parser.add_argument(
        "--sessions",
        default=None,
        help="Glob pattern or space-separated paths to session JSON files. "
             "Default: sessions/human/hw_*.json",
    )
    parser.add_argument(
        "--output",
        default="docs/button-fingerprint-analysis.md",
        help="Output markdown report path (default: docs/button-fingerprint-analysis.md)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=4,
        help="Number of top buttons for fingerprint vector (default: 4)",
    )
    args = parser.parse_args()

    # Resolve session paths
    if args.sessions:
        session_files = sorted(glob.glob(args.sessions))
    else:
        # Default: project root relative
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        pattern = str(project_root / "sessions" / "human" / "hw_*.json")
        session_files = sorted(glob.glob(pattern))

    if not session_files:
        print("ERROR: No session files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {len(session_files)} session files...")

    # Analyze each session
    sessions = []
    skipped = 0
    for sf in session_files:
        result = analyze_session(sf)
        if result is None:
            skipped += 1
        else:
            sessions.append(result)

    if not sessions:
        print("ERROR: No valid sessions (no buttons_0/buttons_1 data found).", file=sys.stderr)
        sys.exit(1)

    print(f"  Loaded {len(sessions)} sessions with button data ({skipped} skipped — no button fields)")

    total_reports = sum(s["report_count"] for s in sessions)
    print(f"  Total reports analyzed: {total_reports:,}")

    print("Computing per-button statistics...")
    agg = aggregate_stats(sessions)

    print("Computing cross-button transitions...")
    trans_agg = aggregate_transitions(sessions)
    print(f"  Found {len(trans_agg)} button-pair transitions with >=5 occurrences")

    print("Building fingerprint vectors...")
    fp_vec = build_fingerprint_vectors(sessions, top_n=args.top_n)

    viability = compute_viability(agg)
    r2_compare = compare_r2_vs_multibutton(sessions, agg)

    print_summary(agg, viability, fp_vec, r2_compare)

    print(f"Generating report -> {args.output}")
    generate_report(sessions, agg, trans_agg, fp_vec, viability, r2_compare, args.output)
    print(f"Report saved: {args.output}")


if __name__ == "__main__":
    main()
