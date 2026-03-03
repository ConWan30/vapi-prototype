"""
VAPI Phase 13 — Multi-Modal Biometric Fusion with Adaptive Trigger Resistance

Reflexive-layer (Layer 4) biometric anomaly detector for the DualShock Edge.

Extracts 7 biometric signals unique to adaptive trigger hardware that software
injection cannot reproduce, builds a per-device kinematic fingerprint, and
detects anomalies via Mahalanobis distance. Produces inference code:
  0x30: BIOMETRIC_ANOMALY — kinematic fingerprint mismatch (soft anomaly signal)

This code is intentionally OUTSIDE the cheat range [0x28, 0x2A]:
- TeamProofAggregator does NOT block records with 0x30 (not a hard cheat)
- SkillOracle treats 0x30 as an advisory signal (no rating penalty)
- The signal is committed into sensor_commitment via the biometric_distance field

Sensor commitment schema v2 expansion (backward-compatible — still tagged schema_version=2):
  Original 48-byte input:  >hhhhBBBBffffffIQ
  Expanded 56-byte input:  >hhhhBBBBffffffIQ + fI
    - f: biometric_distance (float32) — Mahalanobis distance of current session
    - I: trigger_mode_hash  (uint32)  — compact hash of L2/R2 mode sequence

The SHA-256 output remains 32 bytes — 228-byte wire format is UNCHANGED.

Model manifest hash:
  SHA-256(b"biometric_fusion_v1.0_adaptive_trigger") replaces the static
  "heuristic_fallback_v0" hash, versioning the biometric model.

Integration into dualshock_integration.py (Layer 4):
  After Layer 3 (BackendCheatClassifier), before record dispatch:
    bio_features = bio_extractor.extract(frames, snap)
    bio_classifier.update_fingerprint(bio_features)
    result = bio_classifier.classify(bio_features)
    if result is not None and inference not in CHEAT_CODES:
        inference, confidence = result  # 0x30 override
    sensor_hash = compute_sensor_commitment_v2_bio(snap, bio_classifier)
"""

from __future__ import annotations

import hashlib
import math
import os as _os
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Inference code
# ---------------------------------------------------------------------------

INFER_BIOMETRIC_ANOMALY = 0x30
"""
Kinematic fingerprint mismatch — soft anomaly signal.
Outside hard cheat range [0x28, 0x2A]; advisory use only.
"""

# ---------------------------------------------------------------------------
# Model versioning
# ---------------------------------------------------------------------------

BIOMETRIC_MODEL_VERSION = b"biometric_fusion_v1.0_adaptive_trigger"
BIOMETRIC_MODEL_MANIFEST_HASH: bytes = hashlib.sha256(BIOMETRIC_MODEL_VERSION).digest()
"""SHA-256 of model version string. Used as model_manifest_hash in PoAC body."""

# ---------------------------------------------------------------------------
# Biometric feature dataclass (7 signals beyond the existing 30-feature FeatureFrame)
# ---------------------------------------------------------------------------

@dataclass
class BiometricFeatureFrame:
    """
    7 biometric signals extracted from DualShock Edge adaptive trigger dynamics.

    These signals are unique to physical hardware:
    - Trigger resistance dynamics exploit the L2/R2 actuator's programmable resistance
      profiles running at ~200Hz. Software injection cannot reproduce the mechanical
      hysteresis, thermal noise, and micro-jitter of real adaptive trigger engagement.
    - Micro-tremor proxy exploits IMU noise floor during genuine still periods.
    - Grip asymmetry captures the ratio of L2/R2 engagement unique to each player's grip.
    - Stick autocorrelation captures temporal persistence of stick movement patterns.
    """
    trigger_resistance_change_rate: float = 0.0
    """dMode/dt: trigger effect mode transitions per 100 frames. Fast switches = anomaly."""

    trigger_onset_velocity_l2: float = 0.0
    """Frames from l2_trigger==0 to peak / peak_value; normalized [0, 1]. Player-specific onset speed."""

    trigger_onset_velocity_r2: float = 0.0
    """Same as above for R2."""

    micro_tremor_accel_variance: float = 0.0
    """
    Variance of accel_mag = sqrt(ax^2+ay^2+az^2) during frames where gyro_mag < 0.01 rad/s
    (device physically still). Human grip micro-tremor is 8-12 Hz, 0.01–0.1 g amplitude.
    Software replay cannot reproduce this signal without physical hardware noise.
    """

    grip_asymmetry: float = 0.0
    """
    l2_trigger / (r2_trigger + 1e-6) during frames where both triggers are pressed
    (both > 10/255). Player-specific grip strength ratio. Stable across sessions.
    """

    stick_autocorr_lag1: float = 0.0
    """
    Pearson autocorrelation of stick velocity magnitude at lag-1 frame.
    Measures temporal persistence of stick movement. Human play has characteristic persistence.
    """

    stick_autocorr_lag5: float = 0.0
    """Same as lag-1 but at lag-5 frames. Captures longer-range temporal structure."""

    def to_vector(self) -> np.ndarray:
        return np.array([
            self.trigger_resistance_change_rate,
            self.trigger_onset_velocity_l2,
            self.trigger_onset_velocity_r2,
            self.micro_tremor_accel_variance,
            self.grip_asymmetry,
            self.stick_autocorr_lag1,
            self.stick_autocorr_lag5,
        ], dtype=np.float32)

# ---------------------------------------------------------------------------
# Input snapshot protocol (matches dualshock_emulator.py InputSnapshot fields)
# ---------------------------------------------------------------------------

@dataclass
class _InputSnapshotLike:
    """Minimal interface expected from InputSnapshot objects."""
    left_stick_x: int = 0
    left_stick_y: int = 0
    right_stick_x: int = 0
    right_stick_y: int = 0
    l2_trigger: int = 0
    r2_trigger: int = 0
    gyro_x: float = 0.0
    gyro_y: float = 0.0
    gyro_z: float = 0.0
    accel_x: float = 0.0
    accel_y: float = 0.0
    accel_z: float = 1.0
    l2_effect_mode: int = 0
    r2_effect_mode: int = 0
    inter_frame_us: int = 8000


# ---------------------------------------------------------------------------
# Biometric feature extractor
# ---------------------------------------------------------------------------

_BIO_FEATURE_DIM = 7

class BiometricFeatureExtractor:
    """
    Extracts 7 biometric features from a sequence of InputSnapshot objects.

    Designed to be stateless per call — all state (onset tracking, etc.) is
    maintained in rolling buffers passed by the caller or computed inline.
    """

    @staticmethod
    def extract(
        snapshots: Sequence[object],
        window_frames: int = 120,
    ) -> BiometricFeatureFrame:
        """
        Extract biometric features from a window of InputSnapshot objects.

        Args:
            snapshots: Recent InputSnapshot frames (most recent last).
                       Requires at least 10 frames; returns zeros if fewer.
            window_frames: Maximum frames to use (default: ~1 second at 120Hz).

        Returns:
            BiometricFeatureFrame with 7 features.
        """
        snaps = list(snapshots)[-window_frames:]
        n = len(snaps)
        if n < 10:
            return BiometricFeatureFrame()

        # Helper: safe attribute get
        def _g(snap: object, attr: str, default: float = 0.0) -> float:
            return float(getattr(snap, attr, default))

        # 1. Trigger resistance change rate
        l2_modes = [int(getattr(s, "l2_effect_mode", 0)) for s in snaps]
        r2_modes = [int(getattr(s, "r2_effect_mode", 0)) for s in snaps]
        mode_changes = sum(
            1 for i in range(1, n)
            if l2_modes[i] != l2_modes[i - 1] or r2_modes[i] != r2_modes[i - 1]
        )
        resistance_change_rate = (mode_changes / n) * 100.0  # per 100 frames

        # 2. Trigger onset velocities (L2)
        l2_vals = [int(getattr(s, "l2_trigger", 0)) for s in snaps]
        r2_vals = [int(getattr(s, "r2_trigger", 0)) for s in snaps]
        onset_vel_l2 = _compute_trigger_onset_velocity(l2_vals)
        onset_vel_r2 = _compute_trigger_onset_velocity(r2_vals)

        # 3. Micro-tremor: accel variance during still frames (gyro_mag < 0.01)
        still_accel_mags = []
        for s in snaps:
            gx = _g(s, "gyro_x"); gy = _g(s, "gyro_y"); gz = _g(s, "gyro_z")
            gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)
            if gyro_mag < 0.01:
                ax = _g(s, "accel_x"); ay = _g(s, "accel_y"); az = _g(s, "accel_z")
                still_accel_mags.append(math.sqrt(ax * ax + ay * ay + az * az))
        micro_tremor_var = float(np.var(still_accel_mags)) if len(still_accel_mags) >= 5 else 0.0

        # 4. Grip asymmetry (dual-press frames only)
        dual_press_ratios = []
        for s in snaps:
            l2 = int(getattr(s, "l2_trigger", 0))
            r2 = int(getattr(s, "r2_trigger", 0))
            if l2 > 10 and r2 > 10:
                dual_press_ratios.append(l2 / (r2 + 1e-6))
        grip_asym = float(np.mean(dual_press_ratios)) if dual_press_ratios else 1.0

        # 5. Stick velocity autocorrelation at lag 1 and lag 5
        stick_vels = []
        prev_lx, prev_ly = _g(snaps[0], "left_stick_x"), _g(snaps[0], "left_stick_y")
        for s in snaps[1:]:
            lx = _g(s, "left_stick_x"); ly = _g(s, "left_stick_y")
            dt = max(_g(s, "inter_frame_us", 8000) / 1_000_000.0, 1e-6)
            vel = math.sqrt(((lx - prev_lx) / 32768.0) ** 2 + ((ly - prev_ly) / 32768.0) ** 2) / dt
            stick_vels.append(vel)
            prev_lx, prev_ly = lx, ly

        autocorr_lag1 = _autocorr(stick_vels, lag=1)
        autocorr_lag5 = _autocorr(stick_vels, lag=5)

        return BiometricFeatureFrame(
            trigger_resistance_change_rate=resistance_change_rate,
            trigger_onset_velocity_l2=onset_vel_l2,
            trigger_onset_velocity_r2=onset_vel_r2,
            micro_tremor_accel_variance=micro_tremor_var,
            grip_asymmetry=grip_asym,
            stick_autocorr_lag1=autocorr_lag1,
            stick_autocorr_lag5=autocorr_lag5,
        )


def _compute_trigger_onset_velocity(trigger_vals: list[int]) -> float:
    """
    Compute normalized onset velocity for a trigger sequence.
    Onset = frames from 0 to peak / peak_value; lower = faster onset.
    Returns mean onset velocity across all detected onsets.
    """
    onsets = []
    in_onset = False
    onset_start = 0
    for i, v in enumerate(trigger_vals):
        if not in_onset and v > 5:
            in_onset = True
            onset_start = i
        elif in_onset and (v >= 250 or (i > onset_start and trigger_vals[i - 1] > v)):
            # Peak found
            peak = trigger_vals[i - 1] if i > onset_start else v
            duration = max(i - onset_start, 1)
            onsets.append(duration / (peak + 1e-6))
            in_onset = False
    return float(np.mean(onsets)) if onsets else 0.0


def _autocorr(series: list[float], lag: int) -> float:
    """Pearson autocorrelation at given lag. Returns 0 if insufficient data."""
    if len(series) <= lag + 2:
        return 0.0
    x = np.array(series[:-lag], dtype=np.float64)
    y = np.array(series[lag:],  dtype=np.float64)
    if x.std() < 1e-10 or y.std() < 1e-10:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


# ---------------------------------------------------------------------------
# Biometric fingerprint + anomaly detector
# ---------------------------------------------------------------------------

class BiometricFusionClassifier:
    """
    Maintains a rolling biometric fingerprint for a device session and
    detects anomalies via Mahalanobis distance from the stored fingerprint.

    The fingerprint is the (mean, variance) of 7 biometric features
    accumulated over the first N_WARMUP_SESSIONS sessions. After warmup,
    each new session is compared against the fingerprint.

    Fingerprint update:
        mean ← EMA(mean, new_features, alpha=EMA_ALPHA)
        var  ← EMA(var,  (new_features - mean)^2, alpha=EMA_ALPHA)

    Anomaly detection:
        d = mahalanobis(current_vector, mean, diag_cov=var)
        if d > ANOMALY_THRESHOLD and d_confidence > CONFIDENCE_MIN:
            return (INFER_BIOMETRIC_ANOMALY, confidence)

    last_distance (float): Mahalanobis distance of most recent classify() call.
        Used to enrich the sensor_commitment hash (see compute_sensor_commitment_v2_bio).
    """

    N_WARMUP_SESSIONS: int = 5         # Sessions before anomaly detection activates
    EMA_ALPHA: float = 0.1             # Fingerprint update rate
    # Thresholds configurable via environment variables (set before module import,
    # or override on individual instances after __init__ for per-device calibration).
    # Production values come from scripts/threshold_calibrator.py run on N>=50 sessions.
    ANOMALY_THRESHOLD: float = float(_os.getenv("L4_ANOMALY_THRESHOLD", "5.869"))
    CONTINUITY_THRESHOLD: float = float(_os.getenv("L4_CONTINUITY_THRESHOLD", "4.617"))
    CONFIDENCE_MIN: int = 180          # Minimum confidence to report anomaly [0-255]
    CONFIDENCE_SCALE: float = 30.0     # Maps distance above threshold to confidence
    VAR_FLOOR: float = 1e-6            # Prevent division by zero in Mahalanobis

    def __init__(self) -> None:
        self._mean: np.ndarray = np.zeros(_BIO_FEATURE_DIM, dtype=np.float64)
        self._var:  np.ndarray = np.ones(_BIO_FEATURE_DIM,  dtype=np.float64) * 0.1
        self._n_sessions: int = 0
        self._stable_mean: np.ndarray = np.zeros(_BIO_FEATURE_DIM, dtype=np.float64)
        self._stable_var:  np.ndarray = np.ones(_BIO_FEATURE_DIM,  dtype=np.float64) * 0.1
        self._stable_initialized: bool = False
        self.last_distance: float = 0.0

    def update_fingerprint(self, features: BiometricFeatureFrame) -> None:
        """Update the rolling fingerprint with a new session's biometric features."""
        v = features.to_vector().astype(np.float64)
        if self._n_sessions == 0:
            self._mean = v.copy()
        else:
            delta = v - self._mean
            self._mean += self.EMA_ALPHA * delta
            self._var   = (1 - self.EMA_ALPHA) * self._var + self.EMA_ALPHA * delta ** 2
        self._n_sessions += 1


    def update_stable_fingerprint(self, features: BiometricFeatureFrame) -> None:
        """Update the STABLE (poison-proof) fingerprint — only called on clean NOMINAL sessions."""
        v = features.to_vector().astype(np.float64)
        if not self._stable_initialized:
            self._stable_mean = v.copy()
            self._stable_var  = self._var.copy()
            self._stable_initialized = True
        else:
            delta = v - self._stable_mean
            self._stable_mean += self.EMA_ALPHA * delta
            self._stable_var   = (1 - self.EMA_ALPHA) * self._stable_var + self.EMA_ALPHA * delta ** 2

    @property
    def fingerprint_drift_velocity(self) -> float:
        """L2 distance between candidate and stable fingerprint means.
        High value = current sessions drifting from stable fingerprint = possible contamination."""
        if not self._stable_initialized or not self.is_warmed_up():
            return 0.0
        return float(np.linalg.norm(self._mean - self._stable_mean))

    def classify(
        self, features: BiometricFeatureFrame
    ) -> tuple[int, int] | None:
        """
        Compare current biometric features to stored fingerprint.

        Returns:
            (INFER_BIOMETRIC_ANOMALY, confidence) if anomaly detected.
            None if within normal range or fingerprint not yet warmed up.
        """
        if self._n_sessions < self.N_WARMUP_SESSIONS:
            return None

        v = features.to_vector().astype(np.float64)
        ref_mean = self._stable_mean if self._stable_initialized else self._mean
        ref_var  = self._stable_var  if self._stable_initialized else self._var
        diff = v - ref_mean
        var_safe = np.maximum(ref_var, self.VAR_FLOOR)
        # NOTE: DIAGONAL COVARIANCE ASSUMPTION
        # The current implementation uses a diagonal covariance matrix — each feature's
        # variance is computed independently, treating all 7 biometric signals as mutually
        # uncorrelated. The Mahalanobis distance is therefore:
        #
        #   d = sqrt( sum_i( (x_i - mu_i)^2 / sigma_i^2 ) )
        #
        # which is equivalent to a full Mahalanobis with Sigma = diag(sigma_1^2, ..., sigma_7^2).
        # Each feature contributes 1/variance_i independently — there is no cross-feature
        # interaction term in the distance computation.
        #
        # WHY THIS UNDERESTIMATES DISTANCE FOR CERTAIN ATTACK VECTORS:
        # In practice, trigger_onset_velocity_l2 and grip_asymmetry are likely correlated
        # for a given human player (faster onset correlates with a characteristic grip ratio).
        # When an adversarial input deviates in BOTH of these correlated features simultaneously
        # (e.g., an aimbot adjusting both trigger pull speed and grip simulation to mimic a
        # human), the diagonal formula treats each deviation independently and underestimates
        # the true statistical distance from the learned fingerprint. A full covariance matrix
        # would capture the joint deviation and produce a larger, more sensitive distance.
        #
        # EXAMPLE: If trigger_onset_velocity and grip_asymmetry have empirical correlation
        # rho=0.7, the off-diagonal covariance term
        #   sigma_{onset,grip} = rho * sigma_onset * sigma_grip
        # is currently ignored. Ignoring a positive correlation when both features deviate
        # in the same direction causes Mahalanobis distance to be underestimated by a factor
        # that grows with rho and the magnitude of simultaneous deviation.
        #
        # TODO: Add full covariance matrix support via numpy for production calibration.
        # Replace diagonal var-only EMA with a full 7x7 covariance EMA:
        #   C <- (1-alpha)*C + alpha * np.outer(delta, delta)
        # Then: distance = sqrt(diff.T @ np.linalg.solve(C, diff))
        # Gate behind a BiometricFusionClassifier.USE_FULL_COVARIANCE class flag (default False)
        # until empirical calibration data from real hardware sessions is available.
        # Requires minimum ~500 NOMINAL sessions per device for a stable covariance estimate.
        distance = float(np.sqrt(np.sum(diff ** 2 / var_safe)))
        self.last_distance = distance

        if distance <= self.ANOMALY_THRESHOLD:
            return None

        excess = distance - self.ANOMALY_THRESHOLD
        raw_conf = self.CONFIDENCE_MIN + min(int(excess * self.CONFIDENCE_SCALE), 75)
        confidence = max(self.CONFIDENCE_MIN, min(255, raw_conf))
        return (INFER_BIOMETRIC_ANOMALY, confidence)

    def fingerprint_hash(self) -> bytes:
        """
        SHA-256 of fingerprint state (mean + variance arrays).
        Contributed to sensor_commitment for on-chain verification.
        """
        buf = self._mean.astype(np.float32).tobytes() + self._var.astype(np.float32).tobytes()
        return hashlib.sha256(buf).digest()

    def is_warmed_up(self) -> bool:
        return self._n_sessions >= self.N_WARMUP_SESSIONS


# ---------------------------------------------------------------------------
# Sensor commitment computation (v2 expansion — still schema_version=2)
# ---------------------------------------------------------------------------

def compute_trigger_mode_hash(l2_mode_history: list[int], r2_mode_history: list[int]) -> int:
    """
    Compact uint32 hash of the L2/R2 effect mode sequence over the last 16 frames.
    Encodes temporal pattern of adaptive trigger resistance changes.
    """
    n = 16
    l2_tail = (l2_mode_history[-n:] if len(l2_mode_history) >= n else l2_mode_history)
    r2_tail = (r2_mode_history[-n:] if len(r2_mode_history) >= n else r2_mode_history)
    packed = bytes(l2_tail) + bytes(r2_tail)
    digest = hashlib.sha256(packed).digest()
    return struct.unpack(">I", digest[:4])[0]


def compute_sensor_commitment_v2_bio(
    snap: object,
    timestamp_ms: int,
    l2_effect_mode: int,
    r2_effect_mode: int,
    biometric_classifier: BiometricFusionClassifier | None = None,
    l2_mode_history: list[int] | None = None,
    r2_mode_history: list[int] | None = None,
) -> bytes:
    """
    Compute the 32-byte sensor_commitment hash for a DualShock record.

    Expands the Phase 11 schema v2 (48-byte input) with two biometric fields:
      - biometric_distance (float32): Mahalanobis distance of current session
      - trigger_mode_hash  (uint32):  hash of L2/R2 mode sequence (16 frames)

    Total input: 56 bytes → SHA-256 → 32 bytes.
    Wire format: UNCHANGED. Schema tag: still 2.

    Args:
        snap:                 InputSnapshot-like object with sensor fields.
        timestamp_ms:         Current Unix timestamp in milliseconds (int64).
        l2_effect_mode:       Current L2 trigger effect mode ordinal.
        r2_effect_mode:       Current R2 trigger effect mode ordinal.
        biometric_classifier: Optional; provides last_distance. If None, distance=0.
        l2_mode_history:      Last N L2 mode values; used for trigger_mode_hash.
        r2_mode_history:      Last N R2 mode values; used for trigger_mode_hash.

    Returns:
        32-byte SHA-256 sensor commitment hash.
    """
    def _g(attr: str, default: float = 0.0) -> float:
        return float(getattr(snap, attr, default))
    def _gi(attr: str, default: int = 0) -> int:
        return int(getattr(snap, attr, default))

    # Original schema v2 fields (48 bytes)
    base = struct.pack(
        ">hhhhBBBBffffffIQ",
        _gi("left_stick_x"),    _gi("left_stick_y"),
        _gi("right_stick_x"),   _gi("right_stick_y"),
        _gi("l2_trigger"),      _gi("r2_trigger"),
        l2_effect_mode,         r2_effect_mode,
        _g("accel_x"),          _g("accel_y"),          _g("accel_z"),
        _g("gyro_x"),           _g("gyro_y"),            _g("gyro_z"),
        _gi("buttons"),
        timestamp_ms,
    )

    # Biometric extension (8 bytes)
    bio_distance = float(biometric_classifier.last_distance) if biometric_classifier else 0.0
    tmh = compute_trigger_mode_hash(
        l2_mode_history or [l2_effect_mode],
        r2_mode_history or [r2_effect_mode],
    )
    extension = struct.pack(">fI", bio_distance, tmh)

    return hashlib.sha256(base + extension).digest()
