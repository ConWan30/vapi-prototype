"""
Phase 62 -- Player Enrollment + PHG Credential Ceremony
=========================================================

EnrollmentManager runs after each PITL session proof is submitted.
When a device accumulates enrollment_min_sessions NOMINAL sessions
with avg humanity >= enrollment_humanity_min, it automatically mints
a PHGCredential via chain.mint_phg_credential().

Invariants:
  - Credential is soulbound ERC-5192 -- one per device, permanent
  - Nullifier: taken from the device's latest pitl_session_proofs row
  - Feature commitment: latest proof's feature_commitment
  - Humanity prob int: mean across all NOMINAL sessions (x1000, clamped 0-1000)
  - mint_phg_credential() fails gracefully if PHG_CREDENTIAL_ADDRESS not configured
  - Re-checks has_phg_credential() before minting to avoid double-mint on restart
"""

import asyncio
import logging

log = logging.getLogger(__name__)

# Hard cheat codes (block tournament; ineligible for enrollment)
_HARD_CHEAT_CODES = {0x28, 0x29, 0x2A}


class EnrollmentManager:
    """Tracks enrollment progress and triggers PHGCredential mint when eligible.

    Called from _shutdown_cleanup() after each PITL session proof is stored.
    All chain calls are fire-and-forget asyncio tasks; failures are logged
    and do not affect the main bridge pipeline.
    """

    def __init__(self, store, chain, cfg):
        self._store = store
        self._chain = chain
        self._cfg   = cfg

    async def update_enrollment(
        self,
        device_id: str,
        inference_code: int,
        humanity_prob: float,
    ) -> None:
        """Update enrollment progress for device after a PITL session proof.

        Called after each proof store. Updates nominal session count and
        triggers credential mint when thresholds are met.
        """
        try:
            nominal_count, avg_humanity = self._store.count_nominal_sessions(device_id)
            device_row = self._store.get_device(device_id) or {}
            total_count = int(device_row.get("records_total", 0))
            is_hard_cheat = inference_code in _HARD_CHEAT_CODES

            existing = self._store.get_enrollment(device_id)
            status = existing["status"] if existing else "pending"

            # Already terminal -- no further action
            if status in ("credentialed", "minting"):
                return

            # Update progress row
            self._store.upsert_enrollment(
                device_id, nominal_count, total_count, avg_humanity, status
            )

            min_sessions = getattr(self._cfg, "enrollment_min_sessions", 10)
            min_humanity = getattr(self._cfg, "enrollment_humanity_min", 0.60)

            # Transition to eligible when thresholds met (not for hard cheats)
            if (
                nominal_count >= min_sessions
                and avg_humanity >= min_humanity
                and not is_hard_cheat
                and status not in ("credentialed", "minting", "eligible")
            ):
                self._store.upsert_enrollment(
                    device_id, nominal_count, total_count, avg_humanity, "eligible"
                )
                asyncio.create_task(self._try_mint_credential(device_id))

        except Exception:
            log.exception("enrollment update failed for %s", device_id)

    async def _try_mint_credential(self, device_id: str) -> None:
        """Attempt to mint PHGCredential. Idempotent -- checks on-chain first."""
        try:
            # Check on-chain: already minted?
            if self._chain is not None and await self._chain.has_phg_credential(device_id):
                # Sync local state to reflect on-chain reality
                self._store.upsert_enrollment(
                    device_id, 0, 0, 0.0, "credentialed", tx_hash="already_minted"
                )
                return

            # Fetch latest PITL session proof (nullifier + commitment + humanity)
            proof_row = self._store.get_latest_pitl_proof(device_id)
            if not proof_row:
                log.warning("enrollment: no proof row for %s", device_id)
                return

            # Get fresh nominal counts for accurate minting state
            nominal_count, avg_humanity = self._store.count_nominal_sessions(device_id)

            self._store.upsert_enrollment(
                device_id, nominal_count, 0, avg_humanity, "minting"
            )

            humanity_prob_int = int(
                min(1000, max(0, proof_row.get("humanity_prob_int", 0)))
            )

            tx_hash = None
            if self._chain is not None:
                tx_hash = await self._chain.mint_phg_credential(
                    device_id,
                    proof_row["nullifier_hash"],
                    proof_row["feature_commitment"],
                    humanity_prob_int,
                )

            status = "credentialed" if tx_hash else "failed"
            self._store.upsert_enrollment(
                device_id, nominal_count, 0, avg_humanity, status,
                tx_hash=tx_hash or ""
            )
            if tx_hash:
                log.info(
                    "PHG credential minted for %s: tx=%s", device_id, tx_hash
                )
            else:
                log.warning(
                    "PHG credential mint returned no tx for %s "
                    "(chain not configured or call failed)",
                    device_id,
                )

        except Exception:
            log.exception("credential mint failed for %s", device_id)
            try:
                self._store.upsert_enrollment(device_id, 0, 0, 0.0, "failed")
            except Exception:
                pass
