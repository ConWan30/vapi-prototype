"""
L6b Neuromuscular Reflex Analyzer — Phase 63.

Measures the involuntary grip-tightening reflex latency after a sub-perceptual
haptic pulse (profile L6B_PROBE, amplitude ~24%). Human neuromotor loop: 80–280ms.
Bot interrupt response: 0–15ms.

Unlike L6ResponseAnalyzer (which measures voluntary R2 press onset), this analyzer
measures ACCEL-MAGNITUDE delta — the involuntary IMU response to a tactile stimulus.
The player does not need to consciously press anything.

Detection logic:
  - Compute pre_accel_mean from pre_reports (baseline grip stillness)
  - Scan post_reports for first frame where |accel_mag - pre_accel_mean| > threshold
  - latency_ms = frame_index * REPORTS_PER_MS (1000 Hz → 1 report ≈ 1 ms)
  - Classify by latency bucket: BOT [0, 15ms), INCONCLUSIVE [15, 80ms), HUMAN [80, 280ms],
    INCONCLUSIVE (280, 350ms], NO_RESPONSE if no impulse detected in window

Physical grounding:
  - Human spinal reflex arc (stretch reflex): ~80–120ms
  - Human supra-spinal (cortical) loop: ~120–280ms
  - Interrupt-driven software bot: <5ms (OS interrupt latency)
  - Hardware loop-back bot: ~1–15ms (USB polling jitter)
  - Cannot be spoofed without physical hardware responding to the haptic stimulus
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# At 1000 Hz polling, 1 HID report ≈ 1 ms elapsed time.
REPORTS_PER_MS: float = 1.0
# Capture window after probe delivery — 350ms captures full human reflex range + margin.
CAPTURE_WINDOW_MS: float = 350.0


@dataclass
class L6bReflexResult:
    """Output of a single L6b probe analysis."""

    latency_ms: float
    """ms from probe delivery to first detected accel impulse. -1.0 if no response."""

    accel_delta_peak: float
    """Max |accel_mag - pre_mean| observed in the capture window (LSB). 0.0 if no response."""

    classification: str
    """One of: 'HUMAN', 'BOT', 'INCONCLUSIVE', 'NO_RESPONSE'."""

    confidence: float
    """[0.0, 1.0] — scales with accel_delta_peak relative to threshold. 0.5 when no response."""

    probe_ts: float
    """time.monotonic() timestamp at probe delivery."""

    valid: bool
    """False when no accel impulse was detected above threshold in the capture window."""


class L6bReflexAnalyzer:
    """Analyze IMU accel response after a sub-perceptual L6b haptic probe.

    Args:
        human_min_ms:              Minimum latency to classify as HUMAN (default 80ms).
        human_max_ms:              Maximum latency to classify as HUMAN (default 280ms).
        accel_delta_threshold_lsb: Min |accel_mag - pre_mean| to count as a reflex impulse.
                                   Default 500 LSB — well above sensor noise floor (332.99 LSB
                                   95th-pct gyro noise, Phase 57 N=74 calibration), conservative
                                   pending hardware-validated L6b sessions.
    """

    BOT_MAX_MS: float = 15.0

    def __init__(
        self,
        human_min_ms: float = 80.0,
        human_max_ms: float = 280.0,
        accel_delta_threshold_lsb: float = 500.0,
    ) -> None:
        self.human_min_ms = human_min_ms
        self.human_max_ms = human_max_ms
        self.accel_delta_threshold_lsb = accel_delta_threshold_lsb

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        pre_reports: list[dict],
        post_reports: list[dict],
        probe_ts: float,
    ) -> L6bReflexResult:
        """Detect first accel impulse above threshold in post_reports.

        Args:
            pre_reports:  HID report dicts captured before probe delivery.
                          Keys: 'ax', 'ay', 'az' (raw accel LSB values).
                          Same format as l6_response_analyzer._grip_variance() input.
            post_reports: HID report dicts captured after probe delivery (up to 350 frames).
            probe_ts:     time.monotonic() timestamp of probe delivery.

        Returns:
            L6bReflexResult with latency_ms=-1.0 and valid=False if no impulse detected.
        """
        pre_mean = self._accel_mean(pre_reports)
        peak = 0.0
        latency_ms = -1.0

        for i, report in enumerate(post_reports):
            mag = self._accel_mag(report)
            delta = abs(mag - pre_mean)
            if delta > peak:
                peak = delta
            if delta >= self.accel_delta_threshold_lsb and latency_ms < 0:
                latency_ms = float(i) * REPORTS_PER_MS

        valid = latency_ms >= 0.0
        classification = self._classify_latency(latency_ms) if valid else "NO_RESPONSE"
        confidence = self._confidence(peak) if valid else 0.5

        return L6bReflexResult(
            latency_ms=latency_ms,
            accel_delta_peak=peak,
            classification=classification,
            confidence=confidence,
            probe_ts=probe_ts,
            valid=valid,
        )

    def classify(self, result: L6bReflexResult) -> float:
        """Map L6bReflexResult to p_human [0.0, 1.0].

        Returns:
            0.5  — NO_RESPONSE or INCONCLUSIVE (neutral prior — conservative)
            0.05 — BOT (latency < 15ms, interrupt-driven)
            0.90 — HUMAN (latency 80–280ms, neuromotor loop)
        """
        if not result.valid or result.classification == "NO_RESPONSE":
            return 0.5
        if result.classification == "BOT":
            return 0.05
        if result.classification == "HUMAN":
            return 0.90
        return 0.5  # INCONCLUSIVE

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify_latency(self, latency_ms: float) -> str:
        """Assign classification bucket from latency value."""
        if latency_ms < self.BOT_MAX_MS:
            return "BOT"
        if latency_ms < self.human_min_ms:
            return "INCONCLUSIVE"
        if latency_ms <= self.human_max_ms:
            return "HUMAN"
        return "INCONCLUSIVE"

    def _confidence(self, peak_delta: float) -> float:
        """Scale confidence by how far above threshold the peak delta is.

        Clamped to [0.5, 1.0] — minimum 0.5 for any detected impulse.
        """
        if self.accel_delta_threshold_lsb <= 0:
            return 0.5
        ratio = peak_delta / self.accel_delta_threshold_lsb
        return min(1.0, 0.5 + 0.5 * min(ratio - 1.0, 1.0))

    @staticmethod
    def _accel_mag(report: dict) -> float:
        """Compute ||accel|| from a HID report dict (keys: 'ax', 'ay', 'az')."""
        ax = float(report.get("ax", 0.0))
        ay = float(report.get("ay", 0.0))
        az = float(report.get("az", 0.0))
        return math.sqrt(ax * ax + ay * ay + az * az)

    @staticmethod
    def _accel_mean(reports: list[dict]) -> float:
        """Mean ||accel|| across a list of HID report dicts. Returns 0.0 for empty list."""
        if not reports:
            return 0.0
        total = sum(
            math.sqrt(
                float(r.get("ax", 0.0)) ** 2
                + float(r.get("ay", 0.0)) ** 2
                + float(r.get("az", 0.0)) ** 2
            )
            for r in reports
        )
        return total / len(reports)
