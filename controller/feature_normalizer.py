"""
VAPI Phase 28 — Cross-Controller Biometric Feature Normalizer

FeatureNormalizer maps raw biometric feature dicts to the canonical 7-key
format used by ContinuityProver (FEATURE_KEYS) and BiometricFeatureExtractor.

Cross-controller compatibility requires that controllers missing hardware (no IMU,
no adaptive triggers) still produce a valid 7-key feature vector — unsupported
features are zero-filled rather than raised as errors.

Canonical feature keys (from bridge/vapi_bridge/continuity_prover.py):
    trigger_resistance_change_rate  — L2/R2 adaptive trigger mode transition rate
    trigger_onset_velocity_l2       — L2 engagement speed [0, 1]
    trigger_onset_velocity_r2       — R2 engagement speed [0, 1]
    micro_tremor_accel_variance     — IMU accel variance at low gyro magnitude
    grip_asymmetry                  — L2/(R2+ε) ratio during dual-press
    stick_autocorr_lag1             — Pearson autocorrelation at lag-1 frame
    stick_autocorr_lag5             — Pearson autocorrelation at lag-5 frames

Zero-fill rules:
    micro_tremor_accel_variance     → 0.0  when has_accelerometer=False AND has_gyroscope=False
    trigger_resistance_change_rate  → 0.0  when has_adaptive_triggers=False

These zero-fills allow BiometricFusionClassifier to still run on STANDARD-tier
controllers (DualSense, SCUF, Xbox Elite S2) without raising KeyError.
Continuity proofs that compare CERTIFIED↔STANDARD controllers will show reduced
Mahalanobis distance accuracy on the zeroed features — this is expected and
documented in the certification_notes of each STANDARD-tier profile.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from device_profile import DeviceProfile

# Canonical output keys — must match FEATURE_KEYS in bridge/vapi_bridge/continuity_prover.py
CANONICAL_KEYS: list[str] = [
    "trigger_resistance_change_rate",
    "trigger_onset_velocity_l2",
    "trigger_onset_velocity_r2",
    "micro_tremor_accel_variance",
    "grip_asymmetry",
    "stick_autocorr_lag1",
    "stick_autocorr_lag5",
]

# Features gated by hardware capability
_IMU_KEYS = frozenset({"micro_tremor_accel_variance"})
_ADAPTIVE_KEYS = frozenset({"trigger_resistance_change_rate"})


class FeatureNormalizer:
    """Normalize raw biometric features to the canonical 7-key dict for a given profile.

    Features unsupported by the device hardware are zero-filled (never raises).
    Designed to be instantiated once per session:

        normalizer = FeatureNormalizer(profile)
        canonical_features = normalizer.normalize(raw_extractor_output)
    """

    def __init__(self, profile: DeviceProfile) -> None:
        self._has_imu = profile.has_accelerometer or profile.has_gyroscope
        self._has_adaptive = profile.has_adaptive_triggers
        self._profile_id = profile.profile_id

    def normalize(self, raw: dict) -> dict:
        """Return a dict with all CANONICAL_KEYS; unsupported features are 0.0.

        Args:
            raw: Dict of feature_name → float, as produced by BiometricFeatureExtractor.
                 May be partial — missing keys are also zero-filled.

        Returns:
            Dict with exactly CANONICAL_KEYS as keys, all values float.
        """
        out: dict[str, float] = {}
        for k in CANONICAL_KEYS:
            v = raw.get(k, 0.0)
            if k in _IMU_KEYS and not self._has_imu:
                v = 0.0
            elif k in _ADAPTIVE_KEYS and not self._has_adaptive:
                v = 0.0
            out[k] = float(v)
        return out

    @property
    def supported_keys(self) -> list[str]:
        """Return canonical keys this profile can provide non-zero values for.

        Keys that are always zero due to missing hardware are excluded.
        Used to set expectations for Mahalanobis distance accuracy.
        """
        skip: set[str] = set()
        if not self._has_imu:
            skip.update(_IMU_KEYS)
        if not self._has_adaptive:
            skip.update(_ADAPTIVE_KEYS)
        return [k for k in CANONICAL_KEYS if k not in skip]

    def __repr__(self) -> str:
        return (
            f"FeatureNormalizer(profile={self._profile_id!r}, "
            f"has_imu={self._has_imu}, has_adaptive={self._has_adaptive}, "
            f"supported={len(self.supported_keys)}/{len(CANONICAL_KEYS)} keys)"
        )
