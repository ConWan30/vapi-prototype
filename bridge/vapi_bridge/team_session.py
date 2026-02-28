"""
VAPI TeamProofAggregator Integration

Coordinates multi-player sessions (2-6 DualShock devices) and submits
Merkle-aggregated team proofs to TeamProofAggregator.sol on IoTeX.

The Merkle root computation EXACTLY mirrors the Solidity implementation:
  1. Sort leaf hashes lexicographically (bytes compare, unsigned)
  2. Pairwise keccak256(left || right) up the tree
  3. Odd leaf is promoted unchanged
  4. Root = team attestation hash

Usage (bridge side)::

    coordinator = TeamSessionCoordinator(chain_client, store)
    await coordinator.register_team("squad_alpha", [dev_id_1, dev_id_2, dev_id_3])

    # As each member's record is verified on-chain:
    coordinator.record_verified("squad_alpha", dev_id_1, record_hash_1)
    coordinator.record_verified("squad_alpha", dev_id_2, record_hash_2)
    coordinator.record_verified("squad_alpha", dev_id_3, record_hash_3)

    # When all members have a verified record:
    proof_tx = await coordinator.submit_proof("squad_alpha")
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merkle root — mirrors TeamProofAggregator._computeMerkleRoot() exactly
# ---------------------------------------------------------------------------

def _keccak256_pair(a: bytes, b: bytes) -> bytes:
    """keccak256(a || b) — matches Solidity keccak256(abi.encodePacked(a, b))."""
    from eth_hash.auto import keccak
    return keccak(a + b)


def compute_merkle_root(leaves: list[bytes]) -> bytes:
    """
    Compute the Merkle root of a list of 32-byte leaf hashes.

    Algorithm matches TeamProofAggregator.sol _computeMerkleRoot() exactly:
      - Sort leaves lexicographically
      - Pairwise keccak256 bottom-up
      - Odd leaf promoted unchanged

    Args:
        leaves: List of 32-byte record hashes. Length must be 2–6.

    Returns:
        32-byte Merkle root.

    Raises:
        ValueError: If leaves list is empty or any leaf is not 32 bytes.
    """
    if not leaves:
        raise ValueError("Cannot compute Merkle root of empty leaf list")
    if any(len(h) != 32 for h in leaves):
        raise ValueError("All leaves must be 32-byte hashes")

    n = len(leaves)
    if n == 1:
        return leaves[0]

    # Sort lexicographically (bytes compare as unsigned, matching Solidity)
    working = sorted(leaves)

    while n > 1:
        new_n = (n + 1) // 2
        for i in range(new_n):
            left  = i * 2
            right = left + 1
            if right < n:
                working[i] = _keccak256_pair(working[left], working[right])
            else:
                working[i] = working[left]   # Promote odd leaf unchanged
        n = new_n

    return working[0]


# ---------------------------------------------------------------------------
# Team session state
# ---------------------------------------------------------------------------

@dataclass
class _TeamState:
    """In-memory state for a single team session."""
    team_id:     bytes
    device_ids:  list[bytes]                   # Ordered member device IDs
    # Records collected from verified on-chain confirmations: device_id -> record_hash
    verified_records: dict[bytes, bytes] = field(default_factory=dict)
    registered:  bool = False
    proof_submitted: bool = False

    @property
    def member_count(self) -> int:
        return len(self.device_ids)

    @property
    def is_complete(self) -> bool:
        """True when every member has a verified record."""
        return len(self.verified_records) == self.member_count

    @property
    def missing_members(self) -> list[bytes]:
        return [d for d in self.device_ids if d not in self.verified_records]


# ---------------------------------------------------------------------------
# TeamSessionCoordinator
# ---------------------------------------------------------------------------

class TeamSessionCoordinator:
    """
    Manages multi-device team sessions and submits aggregated Merkle proofs.

    Thread-safe (asyncio lock guards all mutations).

    Configuration (environment variables):
        TEAM_AGGREGATOR_ADDRESS   str  TeamProofAggregator contract address
    """

    def __init__(self, chain_client, store=None):
        self._chain  = chain_client
        self._store  = store
        self._teams  : dict[str, _TeamState] = {}   # team_name -> state
        self._lock   = asyncio.Lock()

    # ------------------------------------------------------------------
    # Team lifecycle
    # ------------------------------------------------------------------

    async def register_team(
        self,
        team_name: str,
        device_ids: list[bytes],
    ) -> bytes:
        """
        Register a team on-chain and return the team_id (keccak256 of name).

        Args:
            team_name:  Human-readable team name (e.g. "squad_alpha").
            device_ids: List of 32-byte device IDs for all members (2–6).

        Returns:
            32-byte team_id.
        """
        if not (2 <= len(device_ids) <= 6):
            raise ValueError(f"Team size must be 2–6, got {len(device_ids)}")

        from eth_hash.auto import keccak
        team_id = keccak(team_name.encode())

        async with self._lock:
            state = _TeamState(team_id=team_id, device_ids=list(device_ids))
            self._teams[team_name] = state

        try:
            tx = await self._chain.create_team(team_id, device_ids)
            async with self._lock:
                state.registered = True
            log.info("Team '%s' registered: id=%s... tx=%s...",
                     team_name, team_id.hex()[:16], tx[:16])
        except Exception as exc:
            log.warning("Team on-chain registration failed: %s", exc)
            log.warning("Proceeding in local-only mode — submit will fail")

        return team_id

    def record_verified(
        self,
        team_name: str,
        device_id: bytes,
        record_hash: bytes,
    ) -> bool:
        """
        Register a verified PoAC record for a team member.

        Should be called by the bridge pipeline when a record for a known
        team member reaches STATUS_VERIFIED.

        Returns True if the team is now complete (all members have a record).
        """
        if team_name not in self._teams:
            return False
        state = self._teams[team_name]
        if device_id not in state.device_ids:
            log.warning("Device %s... not a member of team '%s'",
                        device_id.hex()[:16], team_name)
            return False
        state.verified_records[device_id] = record_hash
        log.debug(
            "Team '%s': %d/%d members have verified records",
            team_name, len(state.verified_records), state.member_count,
        )
        return state.is_complete

    async def submit_proof(self, team_name: str) -> Optional[str]:
        """
        Compute Merkle root from all members' verified records and submit on-chain.

        Should be called once ``record_verified()`` returns True (team complete).

        Returns:
            Transaction hash hex string, or None if not configured.
        """
        if team_name not in self._teams:
            raise KeyError(f"Unknown team: {team_name}")
        state = self._teams[team_name]
        if not state.is_complete:
            missing = [d.hex()[:16] for d in state.missing_members]
            raise RuntimeError(
                f"Team '{team_name}' not complete — missing: {missing}"
            )
        if state.proof_submitted:
            log.warning("Team '%s' proof already submitted", team_name)
            return None

        # Build record_hashes list in member order (contract requires this order)
        record_hashes = [state.verified_records[d] for d in state.device_ids]
        merkle_root   = compute_merkle_root(record_hashes)

        log.info(
            "Submitting team proof: team=%s members=%d root=%s...",
            team_name, state.member_count, merkle_root.hex()[:16],
        )

        try:
            tx = await self._chain.submit_team_proof(
                state.team_id, record_hashes, merkle_root
            )
            state.proof_submitted = True
            log.info("TeamProof confirmed: team=%s tx=%s...", team_name, tx[:16])
            return tx
        except Exception as exc:
            log.error("TeamProof submission failed for '%s': %s", team_name, exc)
            raise

    def team_status(self, team_name: str) -> dict:
        """Return a status summary dict for logging/dashboard."""
        if team_name not in self._teams:
            return {"error": "not found"}
        s = self._teams[team_name]
        return {
            "team_id":        s.team_id.hex(),
            "members":        s.member_count,
            "verified":       len(s.verified_records),
            "complete":       s.is_complete,
            "proof_submitted": s.proof_submitted,
        }

    def list_teams(self) -> list[str]:
        return list(self._teams.keys())
