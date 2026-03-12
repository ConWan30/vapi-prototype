"""
L6 Active Physical Challenge-Response — Response Curve Analyzer

After sending a trigger challenge, we collect a sliding window of HID reports
and compute motor-response metrics. These metrics distinguish human motor
behavior (involuntary grip compensation with natural onset and settle) from
software injection (instantaneous onset, zero accel variance, no settling).

L6ResponseMetrics fields:
  onset_ms      — ms from challenge_ts until first r2/l2 ADC delta > ONSET_DELTA_LSB
  peak_delta    — max |r2_post - r2_pre_mean| during the response window
  settle_ms     — ms from onset until r2 returns within SETTLE_FRACTION of pre_mean
  grip_variance — variance of accel_magnitude samples in the response window
  profile_id    — which challenge profile was active
  nonce_bytes   — 4-byte anti-replay nonce from ChallengeSequencer
  valid         — False when window expired with no trigger press at all

classify() output:
  0.0  — strong injection signal (zeroed accel; onset < 5 ms)
  0.1  — never pressed (peak_delta < 5)
  0.5  — null / no signal (valid=False; conservative, non-penalising default)
  0.6+ — realistic human response
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge.controller.l6_challenge_profiles import TriggerChallengeProfile

# Constants
ONSET_DELTA_LSB   = 5     # r2/l2 ADC change that counts as "press started"
SETTLE_FRACTION   = 0.10  # must return within 10% of pre_mean to count as settled
REPORTS_PER_MS    = 1.0   # at 1000 Hz polling, 1 report ≈ 1 ms


@dataclass
class L6ResponseMetrics:
    """Motor response metrics derived from pre/post challenge HID reports."""
    onset_ms:      float
    peak_delta:    float
    settle_ms:     float
    grip_variance: float
    profile_id:    int
    nonce_bytes:   bytes
    valid:         bool


class L6ResponseAnalyzer:
    """Computes and classifies human motor response to a trigger challenge."""

    # ------------------------------------------------------------------
    # Metric computation
    # ------------------------------------------------------------------

    def compute_metrics(
        self,
        pre_reports:  list[dict],
        post_reports: list[dict],
        profile:      "TriggerChallengeProfile",
        challenge_ts: float,
    ) -> L6ResponseMetrics:
        """Compute L6ResponseMetrics from pre-challenge and post-challenge reports.

        Args:
            pre_reports:  Last N reports before the challenge was sent (from _l6_pre_buffer).
            post_reports: Reports collected during the response window (may be empty if
                          the window expired before response was detected — that is
                          handled by valid=False).
            profile:      The TriggerChallengeProfile that was active.
            challenge_ts: monotonic timestamp when the challenge was sent.
        """
        nonce = getattr(profile, "_nonce_bytes", b"\x00" * 4)

        # If no post reports, window expired with no response
        if not post_reports:
            return L6ResponseMetrics(
                onset_ms=0.0,
                peak_delta=0.0,
                settle_ms=0.0,
                grip_variance=0.0,
                profile_id=profile.profile_id,
                nonce_bytes=nonce,
                valid=False,
            )

        # Compute pre-challenge baseline for r2
        pre_r2_vals = [
            float(r.get("features", r).get("r2", 0))
            for r in pre_reports
        ]
        pre_r2_mean = (sum(pre_r2_vals) / len(pre_r2_vals)) if pre_r2_vals else 0.0

        # Find onset: first post report where |r2 - pre_r2_mean| > ONSET_DELTA_LSB
        onset_idx = None
        for idx, r in enumerate(post_reports):
            feats = r.get("features", r)
            r2_val = float(feats.get("r2", 0))
            if abs(r2_val - pre_r2_mean) > ONSET_DELTA_LSB:
                onset_idx = idx
                break

        # If press never detected
        if onset_idx is None:
            return L6ResponseMetrics(
                onset_ms=0.0,
                peak_delta=0.0,
                settle_ms=0.0,
                grip_variance=_grip_variance(post_reports),
                profile_id=profile.profile_id,
                nonce_bytes=nonce,
                valid=True,
            )

        onset_ms = onset_idx * REPORTS_PER_MS

        # Peak delta after onset
        peak_delta = 0.0
        for r in post_reports[onset_idx:]:
            feats = r.get("features", r)
            r2_val = float(feats.get("r2", 0))
            delta = abs(r2_val - pre_r2_mean)
            if delta > peak_delta:
                peak_delta = delta

        # Settle: first report after onset where r2 returns within SETTLE_FRACTION of pre_mean
        settle_idx = None
        settle_band = abs(pre_r2_mean) * SETTLE_FRACTION + 1.0  # +1 to handle near-zero means
        for idx, r in enumerate(post_reports[onset_idx:]):
            feats = r.get("features", r)
            r2_val = float(feats.get("r2", 0))
            if abs(r2_val - pre_r2_mean) <= settle_band:
                settle_idx = onset_idx + idx
                break

        settle_ms = ((settle_idx - onset_idx) * REPORTS_PER_MS) if settle_idx is not None else float(len(post_reports)) * REPORTS_PER_MS

        grip_var = _grip_variance(post_reports)

        return L6ResponseMetrics(
            onset_ms=onset_ms,
            peak_delta=peak_delta,
            settle_ms=settle_ms,
            grip_variance=grip_var,
            profile_id=profile.profile_id,
            nonce_bytes=nonce,
            valid=True,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self, metrics: L6ResponseMetrics) -> float:
        """Classify the response metrics as p_human in [0.0, 1.0].

        Returns:
            0.5  — valid=False (null signal; conservative non-penalising default)
            0.0  — grip_variance == 0.0 (zeroed accelerometer = injected hardware)
            <=0.2 — onset_ms < 5 (sub-human onset = software injection)
            0.1  — peak_delta < 5 (trigger never pressed at all)
            0.6+ — realistic human response
        """
        if not metrics.valid:
            return 0.5

        # Zeroed accel — injected hardware cannot simulate gravity / grip
        if metrics.grip_variance == 0.0:
            return 0.0

        # Sub-human onset — software latency is effectively 0
        if metrics.onset_ms < 5.0:
            return 0.2

        # Never pressed (but window was valid)
        if metrics.peak_delta < ONSET_DELTA_LSB:
            return 0.1

        score = 0.8  # baseline for a detected press

        # Look up calibrated per-profile thresholds
        from bridge.controller.l6_challenge_profiles import CHALLENGE_PROFILES
        _profile = CHALLENGE_PROFILES.get(metrics.profile_id)
        onset_threshold  = _profile.onset_threshold_ms  if _profile else 300.0
        settle_threshold = _profile.settle_threshold_ms if _profile else 2000.0

        # Penalise for onset too slow (above calibrated human-response ceiling for this profile)
        if metrics.onset_ms > onset_threshold:
            score -= 0.3

        # Penalise for no natural settling (sustaining full compression throughout window)
        if metrics.settle_ms > settle_threshold:
            score -= 0.2

        return max(0.0, min(1.0, score))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _grip_variance(reports: list[dict]) -> float:
    """Return variance of accel_magnitude across the given reports."""
    mags = []
    for r in reports:
        feats = r.get("features", r)
        ax = float(feats.get("accel_x", 0))
        ay = float(feats.get("accel_y", 0))
        az = float(feats.get("accel_z", 0))
        mags.append(math.sqrt(ax * ax + ay * ay + az * az))

    if len(mags) < 2:
        return 0.0

    mean = sum(mags) / len(mags)
    variance = sum((m - mean) ** 2 for m in mags) / len(mags)
    return variance
