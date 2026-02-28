"""
Phase 23 — ContinuityProver

Computes diagonal Mahalanobis distance between two devices' biometric fingerprints
stored in SQLite, and produces the on-chain proof hash for IdentityContinuityRegistry.

The distance formula mirrors BiometricFusionClassifier._classify() exactly:
    d = sqrt( sum( (μ_a - μ_b)² / σ²_a ) )

where σ²_a comes from the stored variance for device_a (the warmed-up reference device).
Falls back to Euclidean distance (unit diagonal) when stored variance is unavailable.
"""

import hashlib
import json
import logging
import struct

log = logging.getLogger(__name__)

# Feature keys in canonical order — must match BiometricFusionClassifier._FEATURE_KEYS.
FEATURE_KEYS = [
    "trigger_resistance_change_rate",
    "trigger_onset_velocity_l2",
    "trigger_onset_velocity_r2",
    "micro_tremor_accel_variance",
    "grip_asymmetry",
    "stick_autocorr_lag1",
    "stick_autocorr_lag5",
]

# Minimum variance to avoid division by zero (mirrors VAR_FLOOR in BiometricFusionClassifier)
VAR_FLOOR = 1e-6


class ContinuityProver:
    """
    Off-chain biometric continuity prover.

    Usage (bridge, post-warmup):
        prover = ContinuityProver(store, threshold=2.0)
        should, dist = prover.should_attest(old_device_id, new_device_id)
        if should:
            proof = prover.make_proof_hash(old_device_id, new_device_id, dist)
            await chain.attest_continuity(old_device_id, new_device_id, proof)
    """

    def __init__(self, store, threshold: float = 2.0):
        """
        Args:
            store:     Store instance with get_biometric_fingerprint() and
                       get_fingerprint_variance() methods.
            threshold: Maximum Mahalanobis distance to consider two devices
                       the same human (default 2.0 — tighter than anomaly threshold 3.0).
        """
        self._store     = store
        self._threshold = threshold

    def compute_distance(self, device_a: str, device_b: str) -> float | None:
        """Diagonal Mahalanobis distance between two devices' biometric fingerprints.

        Mirrors BiometricFusionClassifier._classify() distance formula exactly.
        Uses device_a's stored variance as the scaling diagonal.

        Returns:
            Distance as float, or None if either device has no fingerprint data.
        """
        try:
            import numpy as np
        except ImportError:
            log.warning("numpy unavailable — ContinuityProver cannot compute distance")
            return None

        fp_a = self._store.get_biometric_fingerprint(device_a)
        fp_b = self._store.get_biometric_fingerprint(device_b)
        if not fp_a or not fp_b:
            return None

        va = np.array([fp_a.get(k, 0.0) for k in FEATURE_KEYS], dtype=np.float64)
        vb = np.array([fp_b.get(k, 0.0) for k in FEATURE_KEYS], dtype=np.float64)
        diff = va - vb

        # Use stored variance for device_a (the established reference device)
        stored_var = self._store.get_fingerprint_variance(device_a)
        if stored_var is not None and len(stored_var) == len(FEATURE_KEYS):
            var = stored_var
        else:
            # Fallback: unit diagonal (Euclidean distance)
            var = np.ones(len(FEATURE_KEYS), dtype=np.float64)

        var_safe = np.maximum(var, VAR_FLOOR)
        distance = float(np.sqrt(np.sum(diff ** 2 / var_safe)))
        log.debug(
            "ContinuityProver: device_a=%s device_b=%s dist=%.4f threshold=%.1f",
            device_a[:16], device_b[:16], distance, self._threshold,
        )
        return distance

    def make_proof_hash(
        self, device_old: str, device_new: str, distance: float
    ) -> bytes:
        """Produce the on-chain biometricProofHash.

        Formula: SHA-256(old_fp_hash || new_fp_hash || distance_bytes)

        The fingerprint hashes are SHA-256 of the JSON-serialised mean feature
        dictionaries (sort_keys=True), so the proof commits to the feature vectors
        without revealing them on-chain.

        Args:
            device_old: Source device identifier (hex string).
            device_new: Destination device identifier (hex string).
            distance:   Mahalanobis distance (float64).

        Returns:
            32-byte proof hash.
        """
        fp_old = self._store.get_biometric_fingerprint(device_old) or {}
        fp_new = self._store.get_biometric_fingerprint(device_new) or {}

        old_h = hashlib.sha256(
            json.dumps(fp_old, sort_keys=True).encode()
        ).digest()
        new_h = hashlib.sha256(
            json.dumps(fp_new, sort_keys=True).encode()
        ).digest()
        dist_b = struct.pack(">d", distance)  # big-endian float64

        return hashlib.sha256(old_h + new_h + dist_b).digest()

    def should_attest(
        self, device_old: str, device_new: str
    ) -> tuple[bool, float | None]:
        """Determine whether a continuity attestation should be submitted.

        Returns:
            (should_attest, distance)
            should_attest is True only when distance < threshold AND both devices
            have fingerprint data.
        """
        dist = self.compute_distance(device_old, device_new)
        if dist is None:
            return False, None
        return dist < self._threshold, dist
