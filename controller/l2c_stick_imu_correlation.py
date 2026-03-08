"""
VAPI Phase 17 — Stick-IMU Temporal Cross-Correlation Oracle (Layer 2C)

Physical right-stick deflection twists the controller in-hand, producing a
measurable gyro_z response ~15–50ms AFTER the stick velocity peak (grip momentum
transfer). Software injectors that add synthetic stick deltas with independent IMU
produce either zero cross-correlation at the causal lag, or correlation at lag=0
(simultaneous) which is physically impossible for genuine human movement.

Advisory inference code:
    0x32: STICK_IMU_DECOUPLED — Stick-IMU temporal correlation absent (advisory)

Fires when the maximum ABSOLUTE Pearson cross-correlation between right_stick_x
velocity and gyro_z at causal lags [10, 60] frames is below _CORR_THRESHOLD
(0.15). Negative correlation (anti-correlation) is still physical coupling and
does NOT trigger the advisory — only correlation near zero fires 0x32.

Integration into dualshock_integration.py (Layer 2C, after L2B):
    for snap in frames:
        stick_imu_oracle.push_snapshot(snap)
    result = stick_imu_oracle.classify()
    if result is not None and inference not in CHEAT_CODES:
        inference, confidence = result  # 0x32 advisory override
"""

from __future__ import annotations

import os as _os
import time as _time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Inference code
# ---------------------------------------------------------------------------

INFER_STICK_IMU_DECOUPLED = 0x32
"""
Stick-IMU temporal cross-correlation absent — advisory signal.
Outside hard cheat range [0x28, 0x2A]; advisory use only.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LAG_MIN_FRAMES: int = 10
"""
Minimum lag in frames to scan for causal correlation.
At 1000 Hz (DualShock Edge): 10ms — start of physical grip-momentum transfer window.
At 120 Hz game input: ~83ms — both cover the physical causality envelope.
"""

_LAG_MAX_FRAMES: int = 60
"""
Maximum lag in frames to scan.
At 1000 Hz: 60ms — upper bound of grip-momentum transfer to gyro.
At 120 Hz: ~500ms — wide enough to capture slow movements.
"""

_MIN_FRAMES: int = 80
"""Minimum buffered frames before oracle fires."""

_MIN_STICK_STD: float = 0.005
"""
Minimum standard deviation of stick velocity (normalized, units/s).
If stick is nearly static (player in dead zone), correlation is undefined.
Oracle returns None (neutral/insufficient data) when stick activity is below this.
"""

_CORR_THRESHOLD: float = float(_os.getenv("L2C_CORR_THRESHOLD", "0.15"))
"""
Maximum cross-correlation at causal lag below which the oracle fires.
Threshold conservatively set at 0.15: below the noise floor of uncorrelated signals
with sufficient stick movement, but above measurement noise when stick is active.
Override via L2C_CORR_THRESHOLD env var.
Note: oracle returns None when stick is static — active play is required for detection.
"""

_BASE_CONFIDENCE: int = 185
"""Base confidence when oracle fires."""

_BUFFER_MAXLEN: int = 300
"""Maximum frames retained in stick velocity and gyro_z buffers."""

_HUMAN_CORR_BASELINE: float = 0.40
"""
Expected human max_causal_corr used in humanity_score normalization.
Derived from physical expectation: moderate coupling between stick and gyro.
"""


# ---------------------------------------------------------------------------
# Feature dataclass
# ---------------------------------------------------------------------------

@dataclass
class StickImuFeatures:
    """
    Features from the stick-IMU cross-correlation oracle.

    Populated by StickImuCorrelationOracle.extract_features().
    """
    max_causal_corr: float
    """Maximum Pearson r between stick velocity and gyro_z at causal lags."""

    lag_at_max: int
    """Frame lag (frames) where max_causal_corr was observed."""

    frame_count: int
    """Number of frames in the current buffer."""

    anomaly: bool
    """True when abs(max_causal_corr) < _CORR_THRESHOLD (decoupled in both directions)."""


# ---------------------------------------------------------------------------
# Oracle class
# ---------------------------------------------------------------------------

class StickImuCorrelationOracle:
    """
    Layer 2C PITL — Stick-IMU temporal cross-correlation oracle.

    Maintains rolling buffers of right-stick X velocity and gyro_z.
    After _MIN_FRAMES are accumulated, computes Pearson cross-correlation
    at causal lags [_LAG_MIN_FRAMES, _LAG_MAX_FRAMES] and flags sessions
    where the maximum causal correlation is below _CORR_THRESHOLD.

    Physics:
        Physical hand deflects controller → gyro_z responds ~15-50ms later.
        Software injection: stick and gyro are independent → correlation ≈ 0.

    Timestamp handling (for deterministic replay of session JSONs):
        Uses snap.timestamp_ms if present; otherwise wall clock.

    Usage (from dualshock_integration.py Layer 2C block):
        for snap in frames:
            oracle.push_snapshot(snap)
        result = oracle.classify()
        if result is not None and inference not in CHEAT_CODES:
            inference, confidence = result
    """

    def __init__(self) -> None:
        self._stick_vx: deque = deque(maxlen=_BUFFER_MAXLEN)
        self._gyro_z: deque   = deque(maxlen=_BUFFER_MAXLEN)
        self._prev_rx: int    = 0
        self._prev_ts_ms: float = 0.0
        self._first_frame: bool = True

    # ------------------------------------------------------------------
    # Snapshot ingestion
    # ------------------------------------------------------------------

    def push_snapshot(self, snap: object) -> None:
        """
        Compute right-stick X velocity and append to rolling buffers.

        Velocity is normalized: (Δstick_x / 32768) / Δt_seconds.
        On the first frame, only initializes prev_rx and prev_ts without
        adding a velocity sample (no valid Δ yet).
        """
        # Resolve timestamp (deterministic replay support)
        ts = getattr(snap, "timestamp_ms", None)
        if ts is None:
            ts = _time.monotonic() * 1000.0
        now_ms = float(ts)

        rx = int(getattr(snap, "right_stick_x", 0))

        if self._first_frame:
            self._prev_rx = rx
            self._prev_ts_ms = now_ms
            self._first_frame = False
            return

        dt_ms = now_ms - self._prev_ts_ms
        dt_s = max(dt_ms / 1000.0, 1e-4)  # clamp to avoid division by zero

        vx = (rx - self._prev_rx) / 32768.0 / dt_s
        self._stick_vx.append(vx)
        self._gyro_z.append(float(getattr(snap, "gyro_z", 0.0)))

        self._prev_rx = rx
        self._prev_ts_ms = now_ms

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def extract_features(self) -> Optional[StickImuFeatures]:
        """
        Compute cross-correlation between stick velocity and gyro_z at
        causal lags [_LAG_MIN_FRAMES, _LAG_MAX_FRAMES].

        Returns None if fewer than _MIN_FRAMES have been buffered.
        """
        n = len(self._stick_vx)
        if n < _MIN_FRAMES:
            return None

        vx = np.array(self._stick_vx, dtype=np.float64)
        gz = np.array(self._gyro_z, dtype=np.float64)

        # Guard: if stick is nearly static, correlation is undefined (player in dead zone).
        # Return None (neutral) to avoid false positives when stick isn't being used.
        if float(vx.std()) < _MIN_STICK_STD:
            return None

        max_corr = 0.0
        best_lag = _LAG_MIN_FRAMES

        for lag in range(_LAG_MIN_FRAMES, _LAG_MAX_FRAMES + 1):
            if lag >= n:
                break
            a = vx[:-lag]
            b = gz[lag:]
            # Guard against constant arrays (zero std → NaN correlation)
            if a.std() < 1e-9 or b.std() < 1e-9:
                continue
            r = float(np.corrcoef(a, b)[0, 1])
            if np.isnan(r):
                continue
            if abs(r) > abs(max_corr):
                max_corr = r
                best_lag = lag

        return StickImuFeatures(
            max_causal_corr=max_corr,
            lag_at_max=best_lag,
            frame_count=n,
            # Use abs(max_corr): physical coupling can be positive or negative
            # depending on player grip orientation.  Anti-correlation is still
            # real coupling — only near-zero absolute correlation means decoupling.
            anomaly=abs(max_corr) < _CORR_THRESHOLD,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self) -> Optional[Tuple[int, int]]:
        """
        Return (INFER_STICK_IMU_DECOUPLED, confidence) if max_causal_corr
        is below _CORR_THRESHOLD; otherwise return None.

        Confidence range: 185 (near threshold) → 230 (zero correlation, capped).
        """
        features = self.extract_features()
        if features is None or not features.anomaly:
            return None
        confidence = min(230, _BASE_CONFIDENCE + int((1.0 - abs(features.max_causal_corr)) * 50))
        return (INFER_STICK_IMU_DECOUPLED, confidence)

    def humanity_score(self) -> float:
        """
        Positive humanity signal ∈ [0,1].

        max_causal_corr / _HUMAN_CORR_BASELINE: at expected human baseline → 1.0.
        Returns 0.5 (neutral) if insufficient data.
        """
        features = self.extract_features()
        if features is None:
            return 0.5
        return min(1.0, abs(features.max_causal_corr) / _HUMAN_CORR_BASELINE)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all buffers. classify() returns None until refilled."""
        self._stick_vx.clear()
        self._gyro_z.clear()
        self._prev_rx = 0
        self._prev_ts_ms = 0.0
        self._first_frame = True
