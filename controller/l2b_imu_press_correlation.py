"""
VAPI Phase 17 — IMU-Button Press Cross-Modal Latency Oracle (Layer 2B)

Physical button presses cause a wrist/hand micro-impulse recorded by the IMU 5-80ms
BEFORE the digital rising edge closes. This is a hard physical causality constraint:
the hand must accelerate before the finger circuit closes. Software injection has zero
IMU precursor (dt=0) or adds noise AFTER the digital edge — physically impossible.

Advisory inference code:
    0x31: IMU_BUTTON_DECOUPLED — IMU precursor absent before button presses (advisory)

Fires when < 55% of button presses are preceded by an IMU micro-impulse in the
5-80ms window. Below threshold = bot-like decoupling between IMU and button.

Integration into dualshock_integration.py (Layer 2B, after L5):
    for snap in frames:
        imu_press_oracle.push_snapshot(snap)
    result = imu_press_oracle.classify()
    if result is not None and inference not in CHEAT_CODES:
        inference, confidence = result  # 0x31 advisory override
"""

from __future__ import annotations

import math
import os as _os
import statistics
import time as _time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Inference code
# ---------------------------------------------------------------------------

INFER_IMU_BUTTON_DECOUPLED = 0x31
"""
IMU micro-disturbance absent before button press — advisory signal.
Outside hard cheat range [0x28, 0x2A]; advisory use only.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRECURSOR_WINDOW_MS: float = float(_os.getenv("L2B_PRECURSOR_WINDOW_MS", "80.0"))
"""Look-back window for IMU spike before button rising edge (ms)."""

_PRECURSOR_MIN_MS: float = float(_os.getenv("L2B_PRECURSOR_MIN_MS", "5.0"))
"""Minimum precursor lag (ms) — same-frame coincidences excluded."""

_IMU_SPIKE_THRESH: float = float(_os.getenv("L2B_IMU_SPIKE_THRESH", "30.0"))
"""
gyro_mag (LSB) delta above rolling baseline to count as an IMU micro-impulse.
Hardware-calibrated: human hand micro-impulse at 1000Hz = 50-200 LSB peak.
Baseline (resting gyro_mag) ~20-40 LSB. Threshold set conservatively at +30 LSB.
"""

_MIN_PRESS_EVENTS: int = 15
"""Minimum press events before oracle fires."""

_COUPLED_FRACTION: float = float(_os.getenv("L2B_COUPLED_FRACTION", "0.55"))
"""
Fraction of presses that must have an IMU precursor to classify as human.
Below this → bot-like decoupling → fires 0x31.
Human baseline (N=69): ~0.70-0.90. Threshold 0.55 gives 2× safety margin.
"""

_HISTORY_MAXLEN: int = 2000
"""IMU history ring buffer size. At 1000 Hz = ~2 seconds of history."""

_BASELINE_MAXLEN: int = 200
"""Rolling gyro baseline window for adaptive threshold (last 200ms at 1000Hz)."""

_PRESS_MAXLEN: int = 500
"""Maximum press events retained (rolling window)."""

_BASE_CONFIDENCE: int = 190
"""Base confidence when oracle fires."""

CROSS_BIT: int = 1 << 0
"""InputSnapshot.buttons bit 0 = Cross (X) button."""

_R2_PRESS_THRESH: int = 64
"""r2_trigger ADC value for rising edge (mirrors threshold_calibrator.py)."""

_R2_RELEASE_THRESH: int = 30
"""r2_trigger ADC value for falling edge."""


# ---------------------------------------------------------------------------
# Feature dataclass
# ---------------------------------------------------------------------------

@dataclass
class ImuPressFeatures:
    """
    Features from the IMU-button press correlation oracle.

    Populated by ImuPressCorrelationOracle.extract_features().
    """
    press_count: int
    """Number of button press events analyzed."""

    coupled_fraction: float
    """
    Fraction of press events that had an IMU micro-impulse precursor in the
    [_PRECURSOR_MIN_MS, _PRECURSOR_WINDOW_MS] window before the digital edge.
    Human play: ~0.70-0.90. Bot (zero precursor): 0.0.
    """

    anomaly: bool
    """True when coupled_fraction < _COUPLED_FRACTION."""


# ---------------------------------------------------------------------------
# Oracle class
# ---------------------------------------------------------------------------

class ImuPressCorrelationOracle:
    """
    Layer 2B PITL — IMU-button press cross-modal latency oracle.

    Detects the physical causal signature of human button presses: a wrist/hand
    micro-impulse in the IMU that precedes the digital rising edge by 5-80ms.
    Software injection produces no IMU precursor (decoupled signal).

    Timestamp handling:
        Live pipeline: uses _time.monotonic() * 1000.0 (ms)
        Test/replay:   uses snap.timestamp_ms if present (deterministic)

    Usage (from dualshock_integration.py Layer 2B block):
        for snap in frames:
            oracle.push_snapshot(snap)
        result = oracle.classify()
        if result is not None and inference not in CHEAT_CODES:
            inference, confidence = result
    """

    def __init__(self) -> None:
        # IMU history: (timestamp_ms, gyro_mag) pairs
        self._imu_history: deque = deque(maxlen=_HISTORY_MAXLEN)
        # Rolling gyro baseline for adaptive threshold
        self._imu_baseline: deque = deque(maxlen=_BASELINE_MAXLEN)
        # Press events: {"ts": float, "has_precursor": bool}
        self._press_events: deque = deque(maxlen=_PRESS_MAXLEN)
        # Rising-edge state
        self._cross_above: bool = False
        self._r2_above: bool = False

    # ------------------------------------------------------------------
    # Snapshot ingestion
    # ------------------------------------------------------------------

    def push_snapshot(self, snap: object) -> None:
        """
        Process one InputSnapshot frame.

        Computes gyro_mag, updates IMU history, detects rising edges on
        Cross (X) and R2, and records whether each press had an IMU precursor.

        Timestamp resolution (for deterministic replay of session JSONs):
            Uses snap.timestamp_ms if present; otherwise wall clock.
        """
        # Resolve timestamp (deterministic replay support)
        ts = getattr(snap, "timestamp_ms", None)
        if ts is None:
            ts = _time.monotonic() * 1000.0
        now_ms = float(ts)

        # IMU magnitude
        gx = float(getattr(snap, "gyro_x", 0.0))
        gy = float(getattr(snap, "gyro_y", 0.0))
        gz = float(getattr(snap, "gyro_z", 0.0))
        gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)

        self._imu_history.append((now_ms, gyro_mag))
        self._imu_baseline.append(gyro_mag)

        # Cross (X) rising edge
        buttons = int(getattr(snap, "buttons", 0))
        cross_pressed = bool(buttons & CROSS_BIT)
        if cross_pressed and not self._cross_above:
            self._cross_above = True
            self._record_press(now_ms)
        elif not cross_pressed:
            self._cross_above = False

        # R2 rising edge (hysteresis)
        r2 = int(getattr(snap, "r2_trigger", 0))
        if not self._r2_above and r2 >= _R2_PRESS_THRESH:
            self._r2_above = True
            self._record_press(now_ms)
        elif self._r2_above and r2 < _R2_RELEASE_THRESH:
            self._r2_above = False

    def _record_press(self, now_ms: float) -> None:
        """Check IMU history window for a precursor and record the press event."""
        window_start = now_ms - _PRECURSOR_WINDOW_MS
        window_end   = now_ms - _PRECURSOR_MIN_MS

        # Adaptive threshold: median baseline + fixed spike threshold
        if self._imu_baseline:
            baseline = statistics.median(self._imu_baseline)
        else:
            baseline = 0.0
        adaptive_thresh = baseline + _IMU_SPIKE_THRESH

        has_precursor = any(
            mag > adaptive_thresh
            for (t, mag) in self._imu_history
            if window_start <= t <= window_end
        )
        self._press_events.append({"ts": now_ms, "has_precursor": has_precursor})

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def extract_features(self) -> Optional[ImuPressFeatures]:
        """
        Compute coupled_fraction from current press event window.

        Returns None if fewer than _MIN_PRESS_EVENTS have been recorded.
        """
        events = list(self._press_events)
        if len(events) < _MIN_PRESS_EVENTS:
            return None
        coupled = sum(1 for e in events if e["has_precursor"])
        coupled_fraction = coupled / len(events)
        return ImuPressFeatures(
            press_count=len(events),
            coupled_fraction=coupled_fraction,
            anomaly=coupled_fraction < _COUPLED_FRACTION,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self) -> Optional[Tuple[int, int]]:
        """
        Return (INFER_IMU_BUTTON_DECOUPLED, confidence) if coupled_fraction
        is below _COUPLED_FRACTION; otherwise return None.

        Confidence range: 190 (barely anomalous) → 230 (100% decoupled, capped).
        """
        features = self.extract_features()
        if features is None or not features.anomaly:
            return None
        confidence = min(230, _BASE_CONFIDENCE + int((1.0 - features.coupled_fraction) * 50))
        return (INFER_IMU_BUTTON_DECOUPLED, confidence)

    def humanity_score(self) -> float:
        """
        Positive humanity signal ∈ [0,1].

        coupled_fraction / 0.75: at 75% coupling → score 1.0 (above human baseline).
        Returns 0.5 (neutral) if insufficient data.
        """
        features = self.extract_features()
        if features is None:
            return 0.5
        return min(1.0, features.coupled_fraction / 0.75)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all state. classify() returns None until refilled."""
        self._imu_history.clear()
        self._imu_baseline.clear()
        self._press_events.clear()
        self._cross_above = False
        self._r2_above = False
