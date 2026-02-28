"""
Phase 26 — WorldModelAttestation.

Verifies on startup that EWC model weights match the last committed world_model_hash
in the PoAC record chain, closing the E4 adversarial surface.

world_model_hash is at raw_data[96:128]:
  Full wire record = 228B (164B body + 64B ECDSA sig).
  Body layout: prev_hash(32) + sensor_commit(32) + model_hash(32) + wm_hash(32) + ...
  raw_data stores the full 228B; wm_hash = raw_data[96:128].

Advisory only — logs critical on mismatch, does not block record acceptance.
"""

import hashlib
import logging

log = logging.getLogger(__name__)


class WorldModelAttestation:
    """Integrity check: compare live EWC weights to committed world_model_hash."""

    def __init__(self, store, ewc_model=None):
        self._store = store
        self._ewc = ewc_model

    def verify_current_weights(self, device_id: str) -> tuple:
        """Verify live model weights against the last committed hash.

        Returns (bool ok, str reason):
          (True,  "no_model")    — ewc_model not provided
          (True,  "no_records")  — no records with raw_data for device
          (True,  "match")       — SHA-256(current weights) == committed hash
          (False, "mismatch:…")  — weights differ (possible model poisoning)
        """
        if self._ewc is None:
            return True, "no_model"

        committed = self._store.get_latest_world_model_hash(device_id)
        if committed is None:
            return True, "no_records"

        try:
            current_hash = self._ewc.compute_hash()
        except Exception as exc:
            log.warning("WorldModelAttestation: compute_hash failed: %s", exc)
            return True, "no_model"

        if isinstance(current_hash, str):
            current_bytes = bytes.fromhex(current_hash)
        else:
            current_bytes = bytes(current_hash)

        if current_bytes == committed:
            return True, "match"

        return False, f"mismatch:{current_bytes.hex()[:16]}vs{committed.hex()[:16]}"

    def get_weight_hash_chain(self, device_id: str, limit: int = 20) -> list:
        """Return chronological world_model_hash chain for a device.

        Delegates to store.get_world_model_hash_chain() which extracts raw_data[96:128].
        Returns [{timestamp_ms, wm_hash_hex}] in ascending time order.
        """
        return self._store.get_world_model_hash_chain(device_id, limit=limit)

    def is_model_drifted(self, device_id: str, expected_hash_hex: str) -> bool:
        """Return True if current model weights hash != expected_hash_hex."""
        if self._ewc is None:
            return False
        try:
            current_hash = self._ewc.compute_hash()
            if isinstance(current_hash, str):
                current_hex = current_hash
            else:
                current_hex = bytes(current_hash).hex()
            return current_hex != expected_hash_hex
        except Exception:
            return False
