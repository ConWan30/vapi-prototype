"""
VAPI Phase 16B — Temporal Rhythm Oracle (Layer 5 PITL)

Script/macro bots produce artificially precise inter-press timing — low variance,
quantized intervals, low Shannon entropy. This lightweight statistical oracle
detects that fingerprint from the PoAC frame stream and emits an advisory code:

    0x2B: TEMPORAL_ANOMALY — bot-like inter-press timing distribution (advisory)

This code is intentionally OUTSIDE the hard cheat range [0x28, 0x2A]:
- TeamProofAggregator does NOT block records with 0x2B (not a hard cheat)
- SkillOracle treats 0x2B as an advisory signal (no rating penalty)
- The signal is a novel timing-based Sybil resistance layer anchored to PoAC

Three independent signals (need ≥2/3 to fire):
    1. Coefficient of variation (CV = std/mean) < 0.08 — bot timing is unnaturally steady
    2. Shannon entropy (50ms bins) < 1.0 bits — bot uses very few distinct intervals
    3. Quantization score > 0.55 — bot intervals cluster on 60Hz timer multiples (16.67ms)

Integration into dualshock_integration.py (Layer 5):
    After Layer 4 (BiometricFusionClassifier), before record dispatch:
        for f in frames:
            temporal_oracle.push_frame(f)
        result = temporal_oracle.classify()
        if result is not None and inference not in CHEAT_CODES:
            inference, confidence = result  # 0x2B advisory override
"""

from __future__ import annotations

import hashlib
import os as _os
import time as _time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Inference code
# ---------------------------------------------------------------------------

INFER_TEMPORAL_ANOMALY = 0x2B
"""
Bot-like inter-press timing distribution — soft anomaly signal.
Outside hard cheat range [0x28, 0x2A]; advisory use only.
"""

# ---------------------------------------------------------------------------
# Oracle tuning constants
# ---------------------------------------------------------------------------

_MIN_SAMPLES: int = 20
"""Minimum inter-press intervals before the oracle will fire."""

_WINDOW: int = 120
"""Rolling window size in FeatureFrames (~4 seconds at 30Hz)."""

_R2_PRESS_THRESH: int = 64
"""r2_trigger ADC threshold — button 'pressed' when crossing from below (mirrors threshold_calibrator.py)."""

_R2_RELEASE_THRESH: int = 30
"""r2_trigger ADC threshold — button 'released' when dropping below."""

CROSS_BIT: int = 1 << 0
"""InputSnapshot.buttons bit 0 = Cross (X) button (set by DualSenseReader.poll() from ds.state.cross)."""

_CV_THRESHOLD: float = float(_os.getenv("L5_CV_THRESHOLD", "0.08"))
"""CV (std/mean) below this → signal 1 fires. Bots are unnaturally steady.
Hardware-calibrated (N=50 DualShock Edge sessions): human baseline ~0.34 (4x margin).
Override via L5_CV_THRESHOLD env var."""

_ENTROPY_THRESHOLD: float = float(_os.getenv("L5_ENTROPY_THRESHOLD", "1.0"))
"""Shannon entropy (bits, 50ms bins) below this → signal 2 fires.
Hardware-calibrated (N=50 DualShock Edge sessions): human baseline ~1.38 bits.
Threshold set to 1.0 bits — safely below human minimum, above bot range (0–0.5 bits).
Override via L5_ENTROPY_THRESHOLD env var."""

_QUANT_THRESHOLD: float = 0.55
"""Fraction of intervals within ±5ms of a 60Hz tick → signal 3 fires if > this."""

_TICK_MS: float = 16.6667
"""60Hz game-loop timer tick in milliseconds."""

_SIGNALS_REQUIRED: int = 2
"""Number of signals that must fire (out of 3) to classify as TEMPORAL_ANOMALY."""

_BASE_CONFIDENCE: int = 180
"""Base confidence when any signals fire."""

_CONFIDENCE_PER_SIGNAL: int = 25
"""Additional confidence per anomaly signal beyond the base."""

_L2_PRESS_THRESH: int = 64
"""L2 ADC threshold — button 'pressed' when crossing from below (symmetric to R2)."""

_L2_RELEASE_THRESH: int = 30
"""L2 ADC release hysteresis (symmetric to R2)."""

TRIANGLE_BIT: int = 1 << 3
"""InputSnapshot.buttons bit 3 = Triangle button."""

_POOL_MIN_PER_BUTTON: int = 5
"""Minimum per-button sample count to contribute to pooled IBI fallback."""

_HASH_SEPARATOR: bytes = b"\xff\xff\xff\xff"
"""4-byte sentinel separating per-button deques in rhythm_hash().
0xFFFFFFFF is not a representable interval value (intervals are positive ms → int > 0),
so it cannot collide with real data and ensures different button arrangements hash distinctly."""


# ---------------------------------------------------------------------------
# Feature dataclass
# ---------------------------------------------------------------------------

@dataclass
class TemporalRhythmFeatures:
    """
    6 statistical features describing the inter-press timing distribution.

    Populated by TemporalRhythmOracle.extract_features().
    """
    sample_count: int
    """Number of inter-press intervals analyzed from the rolling window."""

    cv: float
    """
    Coefficient of variation (std / mean) of inter-press intervals.
    Healthy human play: CV > 0.15. Bot-like: CV < 0.08.
    """

    entropy_bits: float
    """
    Shannon entropy of interval distribution (50ms-bucket histogram), in bits.
    Healthy human play: entropy ~1.38 bits (N=50 hardware). Bot-like: entropy < 1.0 bits.
    """

    quant_score: float
    """
    Fraction of intervals whose nearest half-tick deviation < 5ms.
    I.e. fraction that snap to multiples of 16.667ms (60Hz timer grid).
    Bot-like: quant_score > 0.55.
    """

    anomaly_signals: int
    """Count of the 3 signals that fired (range 0–3)."""

    confidence: int
    """Encoded confidence: _BASE_CONFIDENCE + anomaly_signals × _CONFIDENCE_PER_SIGNAL."""

    source: str = "unknown"
    """Informational: which button or 'pooled' was used to score this window."""


# ---------------------------------------------------------------------------
# Oracle class
# ---------------------------------------------------------------------------

class TemporalRhythmOracle:
    """
    Layer 5 PITL — statistical timing anomaly detector.

    Maintains a rolling window of inter-press intervals extracted from
    FeatureFrame objects.  After _MIN_SAMPLES intervals are accumulated,
    classify() checks three independent bot fingerprints and returns
    (INFER_TEMPORAL_ANOMALY, confidence) when ≥ _SIGNALS_REQUIRED fire.

    Usage (from dualshock_integration.py Layer 5 block):
        for f in frames:
            oracle.push_frame(f)
        result = oracle.classify()
        if result is not None and inference not in CHEAT_CODES:
            inference, confidence = result
    """

    def __init__(self) -> None:
        self._intervals: deque = deque(maxlen=_WINDOW)        # R2 intervals (push_frame compat)
        self._cross_intervals: deque = deque(maxlen=_WINDOW)  # Cross (X) intervals (push_snapshot)
        self._l2_intervals: deque = deque(maxlen=_WINDOW)     # L2 digital intervals
        self._triangle_intervals: deque = deque(maxlen=_WINDOW)  # Triangle intervals
        # Rising-edge state for push_snapshot()
        self._r2_above: bool = False
        self._cross_above: bool = False
        self._l2_above: bool = False
        self._triangle_above: bool = False
        self._r2_last_press_ts: float = 0.0
        self._cross_last_press_ts: float = 0.0
        self._l2_last_press_ts: float = 0.0
        self._triangle_last_press_ts: float = 0.0

    # ------------------------------------------------------------------
    # Frame ingestion
    # ------------------------------------------------------------------

    def push_frame(self, frame: object) -> None:
        """
        Extract inter_press_ms from frame and append to rolling window.

        Frames with inter_press_ms == 0 (no button press this frame) are
        skipped — only actual press events contribute to the distribution.
        """
        ms = float(getattr(frame, "inter_press_ms", 0.0))
        if ms > 0.0:
            self._intervals.append(ms)

    def push_snapshot(self, snap: object) -> None:
        """
        Rising-edge detection on Cross (X) and R2 from a live InputSnapshot.

        Replaces push_frame() in the live PITL pipeline (dualshock_integration.py).
        push_frame() is kept unchanged for backward compatibility with existing tests.

        Cross: snap.buttons bit 0  (DualSenseReader.poll() sets from ds.state.cross)
        R2:    snap.r2_trigger >= 64 / < 30 hysteresis (mirrors threshold_calibrator.py)

        Intervals are appended to per-button deques:
          _cross_intervals  — Cross (X)
          _intervals        — R2 (shared with push_frame path)
        """
        now_wall = _time.monotonic() * 1000.0  # ms absolute timestamp

        buttons = int(getattr(snap, "buttons", 0))
        r2 = int(getattr(snap, "r2_trigger", 0))

        # Cross (X) rising edge
        cross_pressed = bool(buttons & CROSS_BIT)
        if cross_pressed and not self._cross_above:
            self._cross_above = True
            if self._cross_last_press_ts > 0:
                dt = now_wall - self._cross_last_press_ts
                if dt > 0:
                    self._cross_intervals.append(dt)
            self._cross_last_press_ts = now_wall
        elif not cross_pressed:
            self._cross_above = False

        # R2 rising edge (hysteresis)
        if not self._r2_above and r2 >= _R2_PRESS_THRESH:
            self._r2_above = True
            if self._r2_last_press_ts > 0:
                dt = now_wall - self._r2_last_press_ts
                if dt > 0:
                    self._intervals.append(dt)
            self._r2_last_press_ts = now_wall
        elif self._r2_above and r2 < _R2_RELEASE_THRESH:
            self._r2_above = False

        # L2 digital (symmetric to R2 — same hysteresis thresholds)
        l2_val = int(getattr(snap, "l2_trigger", 0))
        if not self._l2_above and l2_val >= _L2_PRESS_THRESH:
            self._l2_above = True
            if self._l2_last_press_ts > 0.0:
                dt = now_wall - self._l2_last_press_ts
                if dt > 0.0:
                    self._l2_intervals.append(dt)
            self._l2_last_press_ts = now_wall
        elif self._l2_above and l2_val < _L2_RELEASE_THRESH:
            self._l2_above = False

        # Triangle (digital button, bit 3)
        tri_pressed = bool(getattr(snap, "buttons", 0) & TRIANGLE_BIT)
        if tri_pressed and not self._triangle_above:
            self._triangle_above = True
            if self._triangle_last_press_ts > 0.0:
                dt = now_wall - self._triangle_last_press_ts
                if dt > 0.0:
                    self._triangle_intervals.append(dt)
            self._triangle_last_press_ts = now_wall
        elif not tri_pressed and self._triangle_above:
            self._triangle_above = False

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def extract_features(self) -> Optional[TemporalRhythmFeatures]:
        """
        Compute timing-distribution features from the current window.

        Returns None if fewer than _MIN_SAMPLES intervals have been collected
        or if all intervals are effectively zero.
        """
        # Priority order: Cross > L2_dig > R2 > Triangle (descending IBI CV per N=69 calibration)
        _PRIORITY_DEQUES = [
            ("cross",    self._cross_intervals),
            ("l2_dig",   self._l2_intervals),
            ("r2",       self._intervals),
            ("triangle", self._triangle_intervals),
        ]

        # Step 1: Try single-button with >= _MIN_SAMPLES
        arr = None
        source = None
        for name, dq in _PRIORITY_DEQUES:
            if len(dq) >= _MIN_SAMPLES:
                arr = np.array(dq, dtype=np.float32)
                source = name
                break

        # Step 2: Pooled fallback — merge all buttons with >= _POOL_MIN_PER_BUTTON
        if arr is None:
            pool: list = []
            for name, dq in _PRIORITY_DEQUES:
                if len(dq) >= _POOL_MIN_PER_BUTTON:
                    pool.extend(dq)
            if len(pool) >= _MIN_SAMPLES:
                arr = np.array(pool, dtype=np.float32)
                source = "pooled"

        if arr is None:
            return None

        mean = float(arr.mean())
        if mean < 1e-6:
            return None

        # Signal 1: coefficient of variation
        cv = float(arr.std()) / mean

        # Signal 2: Shannon entropy over 50ms-wide buckets
        max_val = float(arr.max())
        bins = np.arange(0.0, max_val + 51.0, 50.0)
        counts, _ = np.histogram(arr, bins=bins)
        nonzero = counts[counts > 0]
        if len(nonzero) == 0:
            return None
        probs = nonzero / nonzero.sum()
        entropy_bits = float(-np.sum(probs * np.log2(probs)))

        # Signal 3: quantization score (60Hz tick snapping)
        # Compute distance to nearest multiple of _TICK_MS:
        #   residue = x mod tick
        #   dist    = min(residue, tick - residue)  [wraps at half-tick]
        # Exact tick multiples → dist = 0; midpoints → dist = tick/2 ≈ 8.33ms
        residue = arr % _TICK_MS
        deviations = np.minimum(residue, _TICK_MS - residue)
        quant_score = float(np.mean(deviations < 5.0))

        # Count signals and derive confidence (capped at 230 per spec)
        signals = (
            int(cv < _CV_THRESHOLD)
            + int(entropy_bits < _ENTROPY_THRESHOLD)
            + int(quant_score > _QUANT_THRESHOLD)
        )
        confidence = min(230, _BASE_CONFIDENCE + signals * _CONFIDENCE_PER_SIGNAL)

        return TemporalRhythmFeatures(
            sample_count=len(arr),
            cv=cv,
            entropy_bits=entropy_bits,
            quant_score=quant_score,
            anomaly_signals=signals,
            confidence=confidence,
            source=source,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self) -> Optional[Tuple[int, int]]:
        """
        Return (INFER_TEMPORAL_ANOMALY, confidence) if ≥ _SIGNALS_REQUIRED
        anomaly signals fire; otherwise return None.

        Confidence range:
            1 signal → 205 (base 180 + 1×25)  — not returned (below threshold)
            2 signals → 205                    — returned
            3 signals → 255 (capped at 230)    — returned
        """
        features = self.extract_features()
        if features is None or features.anomaly_signals < _SIGNALS_REQUIRED:
            return None
        return (INFER_TEMPORAL_ANOMALY, features.confidence)

    def rhythm_humanity_score(self) -> float:
        """Positive humanity signal ∈ [0,1] — inverts CV/entropy/quant anomaly metrics.

        A human playing under competitive pressure has high CV (reaction variance),
        high entropy (many distinct intervals), and low quantization (no timer snapping).
        Returns 0.5 (neutral) if insufficient samples.
        """
        features = self.extract_features()
        if features is None:
            return 0.5
        cv_humanity   = min(1.0, features.cv / 0.25)          # CV=0.08 → 0.32; CV=0.25+ → 1.0
        entropy_score = min(1.0, features.entropy_bits / 3.0)  # 1.5 bits → 0.5; 3.0 bits → 1.0
        non_quant     = 1.0 - features.quant_score             # 0.55 quant → 0.45
        return (cv_humanity + entropy_score + non_quant) / 3.0

    # ------------------------------------------------------------------
    # Sensor commitment contribution
    # ------------------------------------------------------------------

    def rhythm_hash(self) -> bytes:
        """
        SHA-256 of all four button interval windows in canonical priority order:
        Cross ‖ L2 ‖ R2 ‖ Triangle, each separated by _HASH_SEPARATOR.

        The separator ensures the same intervals stored in different button deques
        produce distinct hashes — a bot using only Cross cannot produce the same
        commitment as one using only R2 with identical timing values.

        Can be included in sensor_commitment extensions to commit timing
        distribution data into the on-chain PoAC record.
        """
        parts: list = []
        for dq in (
            self._cross_intervals,
            self._l2_intervals,
            self._intervals,           # R2 / push_frame compat
            self._triangle_intervals,
        ):
            parts.append(b"".join(int(v).to_bytes(4, "big") for v in dq))
            parts.append(_HASH_SEPARATOR)
        return hashlib.sha256(b"".join(parts)).digest()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear interval windows and rising-edge state. Classify will return None until refilled."""
        self._intervals.clear()
        self._cross_intervals.clear()
        self._l2_intervals.clear()
        self._triangle_intervals.clear()
        self._r2_above = False
        self._cross_above = False
        self._l2_above = False
        self._triangle_above = False
        self._r2_last_press_ts = 0.0
        self._cross_last_press_ts = 0.0
        self._l2_last_press_ts = 0.0
        self._triangle_last_press_ts = 0.0
