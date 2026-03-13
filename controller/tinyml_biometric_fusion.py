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
# Frame window constants
# ---------------------------------------------------------------------------

LIVE_WINDOW_FRAMES: int = 120
"""Live classification window (~0.12s at 1000Hz polling).
Tremor FFT (features 8-9) requires >=1024 velocity samples from the ring buffer.
Ring buffer (_FFT_RING_MAXLEN=1025) fills after ~1025 cumulative frames (~1.0s at
1000Hz across successive extract() calls). Returns 0.0 during warm-up; the
BiometricFusionClassifier zero-variance mask excludes inactive features automatically.
"""

CALIBRATION_WINDOW_FRAMES: int = 1025
"""Calibration/offline analysis window (~1.025s at 1000Hz polling).
Produces exactly 1024 velocity samples (np.diff of 1025 positions) in a single
extract() call, activating the tremor FFT at 0.977 Hz/bin — 4 bins across the
8–12 Hz physiological tremor band.
Pass window_frames=CALIBRATION_WINDOW_FRAMES to extract() when running
threshold_calibrator.py or analyze_interperson_separation.py.
"""

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

    tremor_peak_hz: float = 0.0
    """
    Dominant FFT frequency in right-stick X velocity spectrum (Hz).
    Human physiological tremor: 8-12 Hz.
    Bot PID oscillation: 0 Hz (static) or 30-60 Hz. Bot constant hold: 0 Hz.
    Requires ≥32 frames; set to 0.0 if insufficient.
    """

    tremor_band_power: float = 0.0
    """
    Fraction of FFT power in the 8-12 Hz physiological tremor band.
    Human: typically > 0.10. Bot (static or PID): < 0.03.
    Requires ≥32 frames; set to 0.0 if insufficient.
    """

    accel_magnitude_spectral_entropy: float = 0.0
    """
    Shannon entropy of the 0–500 Hz power spectrum of ||accel||_demeaned.
    Gravity-invariant: magnitude eliminates orientation dependence.
    Computed from 1024-sample ring buffer; returns 0.0 during warm-up.
    Human natural grip: ~3–6 bits (calibration-derived).
    Static zero injection: 0.0 (variance guard). Random noise: ~9.0 bits.
    Requires 1000 Hz polling — unreliable at standard HID (125–250 Hz).
    Replaces structurally-zero touchpad_active_fraction (Phase 46).
    """

    touch_position_variance: float = 0.0
    """
    Variance of touch0_x normalized [0, 1] during active touch frames.
    Low variance = consistent thumb resting position (player fingerprint).
    Set to 0.0 if fewer than 3 active touch frames.
    """

    def to_vector(self) -> np.ndarray:
        return np.array([
            self.trigger_resistance_change_rate,
            self.trigger_onset_velocity_l2,
            self.trigger_onset_velocity_r2,
            self.micro_tremor_accel_variance,
            self.grip_asymmetry,
            self.stick_autocorr_lag1,
            self.stick_autocorr_lag5,
            self.tremor_peak_hz,
            self.tremor_band_power,
            self.accel_magnitude_spectral_entropy,  # index 9 (was touchpad_active_fraction)
            self.touch_position_variance,
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

_BIO_FEATURE_DIM = 11

class BiometricFeatureExtractor:
    """
    Extracts 11 biometric features from a sequence of InputSnapshot objects.

    Stateful: maintains a separate 1025-position ring buffer for right-stick X
    positions so that the tremor FFT (features 8-9) can accumulate across
    successive live calls even when window_frames=LIVE_WINDOW_FRAMES (120).
    The ring yields up to 1024 velocity samples (np.diff), giving 0.977 Hz/bin
    frequency resolution at 1000 Hz — 4 bins across the 8–12 Hz tremor band.

    In calibration/offline mode (window_frames=CALIBRATION_WINDOW_FRAMES=1025),
    a single call adds exactly 1025 positions to the ring, producing 1024 velocity
    samples and activating the FFT immediately.
    """

    _FFT_RING_MAXLEN: int = 1025  # positions; yields up to 1024 velocity samples (0.977 Hz/bin)

    def __init__(self) -> None:
        # Separate ring buffer for right_stick_x positions used by tremor FFT.
        # Accumulates across calls; maxlen=1025 → up to 1024 velocity samples (0.977 Hz/bin).
        self._fft_ring: deque[float] = deque(maxlen=self._FFT_RING_MAXLEN)
        # Ring buffer for accel magnitude used by spectral entropy (feature index 9).
        # 1024 samples → 513 frequency bins at 0.977 Hz/bin at 1000 Hz.
        self._accel_mag_ring: deque[float] = deque(maxlen=1024)

    def extract(
        self,
        snapshots: Sequence[object],
        window_frames: int = LIVE_WINDOW_FRAMES,
    ) -> BiometricFeatureFrame:
        """
        Extract biometric features from a window of InputSnapshot objects.

        Args:
            snapshots: Recent InputSnapshot frames (most recent last).
                       Requires at least 10 frames; returns zeros if fewer.
            window_frames: Maximum frames to use for non-FFT features.
                LIVE_WINDOW_FRAMES (120): default live path.  After ~1025
                    cumulative frames (~1.0s at 1000Hz) the tremor FFT activates
                    via ring buffer at 0.977 Hz/bin (4 bins across 8–12 Hz).
                CALIBRATION_WINDOW_FRAMES (1025): offline analysis — tremor
                    FFT activates immediately from the single large window.

        Returns:
            BiometricFeatureFrame with 11 features.
        """
        snaps = list(snapshots)[-window_frames:]
        n = len(snaps)
        if n < 10:
            return BiometricFeatureFrame()

        # Helper: safe attribute get
        def _g(snap: object, attr: str, default: float = 0.0) -> float:
            return float(getattr(snap, attr, default))

        # 1. Trigger resistance change rate
        # NOTE: This feature is game-specific. In NCAA Football 26, adaptive trigger modes
        # are static throughout play (no L2/R2 mode changes) so this is always 0.0 for
        # that game. Signal activates in trigger-heavy games (e.g., FPS with variable
        # resistance profiles) where the game/driver changes trigger effect modes mid-session.
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

        # 3. Micro-tremor: accel variance during still frames
        # Threshold 20.0 LSB = raw HID gyro noise floor at rest (~14 LSB std).
        # Previous threshold 0.01 assumed normalized rad/s — never matched raw LSB values
        # (active play: 201 LSB, rest: 14–50 LSB), so zero frames ever passed the gate.
        still_accel_mags = []
        for s in snaps:
            gx = _g(s, "gyro_x"); gy = _g(s, "gyro_y"); gz = _g(s, "gyro_z")
            gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)
            if gyro_mag < 20.0:  # raw LSB threshold (rest noise floor ~14 LSB)
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

        # 6. Right-stick tremor FFT (8-12 Hz physiological tremor)
        # Estimate sampling frequency from current window timing.
        dt_vals = [max(_g(s, "inter_frame_us", 1000) / 1_000_000.0, 1e-6) for s in snaps[1:]]
        fs = 1.0 / max(float(np.median(dt_vals)), 1e-6) if dt_vals else 1000.0

        # Update the persistent ring buffer with right_stick_x positions from
        # this window's snapshots.  Duplicates are harmless: the ring is
        # capped at _FFT_RING_MAXLEN=513; the caller's window slice already
        # represents the most-recent frames so we append only those.
        for s in snaps:
            self._fft_ring.append(float(getattr(s, "right_stick_x", 0)))

        # Prefer the ring buffer when it has accumulated enough history;
        # otherwise fall back to the current window (activates immediately for
        # large calibration windows >= 513 frames).
        ring_arr = np.array(list(self._fft_ring), dtype=np.float32)
        rx_vels_src = np.diff(ring_arr) / 32768.0

        if len(rx_vels_src) >= 1024:
            # 1024 velocity samples → bin width ≈0.977 Hz at 1000 Hz; 4 bins across 8–12 Hz tremor band.
            fft_mag = np.abs(np.fft.rfft(rx_vels_src))
            freqs   = np.fft.rfftfreq(len(rx_vels_src), d=1.0 / fs)
            total_power = float(np.sum(fft_mag ** 2)) or 1e-9
            peak_idx = int(np.argmax(fft_mag))
            tremor_peak_hz  = float(freqs[peak_idx])
            band_mask = (freqs >= 8.0) & (freqs <= 12.0)
            tremor_band_power = float(np.sum(fft_mag[band_mask] ** 2) / total_power)
        else:
            # Ring not yet full and window too small — tremor FFT inactive.
            tremor_peak_hz = 0.0
            tremor_band_power = 0.0

        # 7. Accel magnitude spectral entropy (gravity-invariant; 1000 Hz exclusive)
        # Update ring buffer with magnitude samples from this window.
        for s in snaps:
            _ax = float(getattr(s, "accel_x", 0.0))
            _ay = float(getattr(s, "accel_y", 0.0))
            _az = float(getattr(s, "accel_z", 0.0))
            self._accel_mag_ring.append(math.sqrt(_ax * _ax + _ay * _ay + _az * _az))

        # Compute entropy from ring when full (1024 samples); 0.0 during warm-up.
        if len(self._accel_mag_ring) >= 1024:
            _ring_arr = np.array(list(self._accel_mag_ring), dtype=np.float64)
            _var = float(np.var(_ring_arr))
            if _var < 4.0:
                # Near-constant signal: static injection or dead controller
                accel_magnitude_spectral_entropy = 0.0
            else:
                _dc = _ring_arr - float(np.mean(_ring_arr))
                _power = np.abs(np.fft.rfft(_dc)) ** 2
                _total = float(np.sum(_power))
                if _total < 1e-12:
                    accel_magnitude_spectral_entropy = 0.0
                else:
                    _p = _power / _total
                    _p = _p[_p > 1e-12]
                    accel_magnitude_spectral_entropy = float(-np.sum(_p * np.log2(_p)))
        else:
            accel_magnitude_spectral_entropy = 0.0

        # Touchpad position variance — kept at index 10 (pending post-Phase-17 recapture)
        touch_xs = [
            float(getattr(s, "touch0_x", 0)) / 1920.0
            for s in snaps
            if bool(getattr(s, "touch_active", False))
        ]
        touch_position_variance = float(np.var(touch_xs)) if len(touch_xs) >= 3 else 0.0

        return BiometricFeatureFrame(
            trigger_resistance_change_rate=resistance_change_rate,
            trigger_onset_velocity_l2=onset_vel_l2,
            trigger_onset_velocity_r2=onset_vel_r2,
            micro_tremor_accel_variance=micro_tremor_var,
            grip_asymmetry=grip_asym,
            stick_autocorr_lag1=autocorr_lag1,
            stick_autocorr_lag5=autocorr_lag5,
            tremor_peak_hz=tremor_peak_hz,
            tremor_band_power=tremor_band_power,
            accel_magnitude_spectral_entropy=accel_magnitude_spectral_entropy,  # index 9
            touch_position_variance=touch_position_variance,                      # index 10
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
    # Phase 46 empirical calibration (N=74 sessions including hw_074–hw_078, 11-feature space,
    # accel_magnitude_spectral_entropy at index 9 replacing touchpad_active_fraction).
    # Source: scripts/threshold_calibrator.py → calibration_profile.json
    ANOMALY_THRESHOLD: float = float(_os.getenv("L4_ANOMALY_THRESHOLD", "6.726"))
    CONTINUITY_THRESHOLD: float = float(_os.getenv("L4_CONTINUITY_THRESHOLD", "5.097"))
    CONFIDENCE_MIN: int = 180          # Minimum confidence to report anomaly [0-255]
    CONFIDENCE_SCALE: float = 30.0     # Maps distance above threshold to confidence
    VAR_FLOOR: float = 1e-6            # Prevent division by zero in Mahalanobis
    ZERO_VAR_THRESHOLD: float = 1e-4
    """Features with training variance below this are excluded from Mahalanobis distance.
    Prevents structurally-zero features (e.g. touchpad in pre-Phase-17 sessions,
    trigger_resistance_change_rate in static-trigger games) from causing false-positive
    0x30 alerts when the feature first becomes active in legitimate use.
    The EMA fingerprint still updates on all features — exclusion only applies
    to the distance computation in classify().
    """

    USE_FULL_COVARIANCE: bool = False
    """
    When True, classify() uses a full NxN covariance matrix rather than
    the diagonal variance approximation.

    Full covariance captures cross-feature correlations (e.g. grip_asymmetry
    and trigger_onset_velocity_l2 are empirically correlated for the same
    player).  An adversary must match the full joint distribution of all 11
    features simultaneously, making transplant / replay attacks harder to
    score as nominal.

    Requires minimum ~500 NOMINAL sessions per device for a stable covariance
    estimate.  Leave False (default) until that data is available; the
    diagonal approximation is safe and well-tested on N=69.

    Toggle per-instance: classifier.USE_FULL_COVARIANCE = True
    """

    _COV_LAMBDA: float = 0.01
    """Tikhonov (L2) regularization added to the diagonal of the covariance
    matrix before inversion to prevent ill-conditioning:
        Sigma_reg = Sigma + lambda * I
    Chosen so that condition_number(Sigma_reg) <= 1/lambda = 100 in
    near-degenerate cases.  Increase if np.linalg.solve raises LinAlgError.
    """

    def __init__(self) -> None:
        self._mean: np.ndarray = np.zeros(_BIO_FEATURE_DIM, dtype=np.float64)
        self._var:  np.ndarray = np.ones(_BIO_FEATURE_DIM,  dtype=np.float64) * 0.1
        self._cov:  np.ndarray = np.eye(_BIO_FEATURE_DIM, dtype=np.float64) * 0.1
        self._n_sessions: int = 0
        self._stable_mean: np.ndarray = np.zeros(_BIO_FEATURE_DIM, dtype=np.float64)
        self._stable_var:  np.ndarray = np.ones(_BIO_FEATURE_DIM,  dtype=np.float64) * 0.1
        self._stable_cov:  np.ndarray = np.eye(_BIO_FEATURE_DIM, dtype=np.float64) * 0.1
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
            self._cov   = (1 - self.EMA_ALPHA) * self._cov + self.EMA_ALPHA * np.outer(delta, delta)
        self._n_sessions += 1

    def update_stable_fingerprint(self, features: BiometricFeatureFrame) -> None:
        """Update the STABLE (poison-proof) fingerprint — only called on clean NOMINAL sessions."""
        v = features.to_vector().astype(np.float64)
        if not self._stable_initialized:
            self._stable_mean = v.copy()
            self._stable_var  = self._var.copy()
            self._stable_cov  = self._cov.copy()
            self._stable_initialized = True
        else:
            delta = v - self._stable_mean
            self._stable_mean += self.EMA_ALPHA * delta
            self._stable_var   = (1 - self.EMA_ALPHA) * self._stable_var + self.EMA_ALPHA * delta ** 2
            self._stable_cov   = (1 - self.EMA_ALPHA) * self._stable_cov + self.EMA_ALPHA * np.outer(delta, delta)

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
        # Exclude features with near-zero training variance (structurally inactive features).
        # With VAR_FLOOR = 1e-6, a feature always-zero in training has ref_var ≈ 1e-6.
        # If a post-training sample has value 0.8: contribution = 0.8² / 1e-6 = 640,000 →
        # false-positive 0x30. This occurs for touchpad features (pre-Phase-17 sessions)
        # and trigger_resistance_change_rate (static-trigger games like NCAA Football 26).
        # After the threshold is met (training variance accumulates), the feature re-enters.
        active_mask = ref_var > self.ZERO_VAR_THRESHOLD
        if not np.any(active_mask):
            # Degenerate: fingerprint has no informative features yet.
            self.last_distance = 0.0
            return None

        if self.USE_FULL_COVARIANCE:
            # Full NxN Mahalanobis: d = sqrt(diff_active^T @ inv(Sigma_reg) @ diff_active)
            # Only active features participate; inactive rows/cols are excluded.
            ref_cov = self._stable_cov if self._stable_initialized else self._cov
            sub_cov = ref_cov[np.ix_(active_mask, active_mask)]
            # Tikhonov regularisation to prevent ill-conditioning (lambda * I).
            sub_cov_reg = sub_cov + self._COV_LAMBDA * np.eye(sub_cov.shape[0])
            diff_active = diff[active_mask]
            try:
                solved = np.linalg.solve(sub_cov_reg, diff_active)
                distance = float(np.sqrt(max(float(np.dot(diff_active, solved)), 0.0)))
            except np.linalg.LinAlgError:
                # Singular matrix despite regularisation — fall back to diagonal.
                distance = float(np.sqrt(np.sum(diff[active_mask] ** 2 / var_safe[active_mask])))
        else:
            # Diagonal covariance (default): fast, well-tested on N=69 sessions.
            # Each feature contributes independently; no cross-feature interaction.
            distance = float(np.sqrt(np.sum(diff[active_mask] ** 2 / var_safe[active_mask])))
        self.last_distance = distance

        if distance <= self.ANOMALY_THRESHOLD:
            return None

        excess = distance - self.ANOMALY_THRESHOLD
        raw_conf = self.CONFIDENCE_MIN + min(int(excess * self.CONFIDENCE_SCALE), 75)
        confidence = max(self.CONFIDENCE_MIN, min(255, raw_conf))
        return (INFER_BIOMETRIC_ANOMALY, confidence)

    def fingerprint_hash(self) -> bytes:
        """
        SHA-256 of fingerprint state.
        Diagonal mode: mean + variance arrays.
        Full-covariance mode: mean + variance + lower-triangle of cov matrix.
        Contributed to sensor_commitment for on-chain verification.
        """
        buf = self._mean.astype(np.float32).tobytes() + self._var.astype(np.float32).tobytes()
        if self.USE_FULL_COVARIANCE:
            # Include lower-triangle of covariance matrix for stronger binding.
            tril_indices = np.tril_indices(_BIO_FEATURE_DIM)
            buf += self._cov[tril_indices].astype(np.float32).tobytes()
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
