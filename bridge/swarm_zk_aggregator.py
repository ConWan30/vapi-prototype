"""
Phase 13 — Enhancement 3 (Design + Mock): ZK Swarm Consensus Aggregator.

Phase 13 scope is MOCK ONLY. This module provides:
  1. Groth16 circuit specification as documentation (Phase 14 implements real Circom).
  2. Mock proof generator (256-byte structured placeholder).
  3. Python interface matching what the real ZK path will use in Phase 14.
  4. Merkle root computation matching the on-chain algorithm (sorted, keccak256).

Follows the exact precedent of TieredDeviceRegistryV2Testable / P256 mock:
mock-first, real-later. Phase 14 drops in the real Groth16 verifier without
changing the Python interface or the Solidity TeamProofAggregatorZK contract.

===========================================================================
ZK CIRCUIT SPECIFICATION (Phase 14 Implementation Target)
===========================================================================

Circuit: TeamProof(MAX_MEMBERS=6) in Circom 2.0

Public inputs:
  merkleRoot       : field   // Sorted Merkle root of record hashes
  nullifierHash    : field   // Prevent double-spend: H(teamId, epoch)
  memberCount      : field   // 2–6

Private inputs:
  deviceIds[6]     : field[] // keccak256(pubkey) for each member
  recordHashes[6]  : field[] // SHA-256 body hash for each member
  inferenceResults[6]: field[] // inference_result byte for each record
  identityCommitments[6]: field[] // H(deviceId, secret) for anonymity

Constraints enforced:
  1. For each i in [0, memberCount):
       inferenceResults[i] NOT IN [CHEAT_INFERENCE_MIN, CHEAT_INFERENCE_MAX]
       (i.e., NOT IN [0x28, 0x2A])
  2. Merkle root of lexicographically sorted recordHashes == public merkleRoot
     (matching TeamProofAggregator._computeMerkleRoot on-chain algorithm)
  3. Each identityCommitment[i] == Poseidon(deviceIds[i], privKey[i])
     (zero-knowledge identity binding)

Trusted setup: Hermez perpetual powers-of-tau (no new ceremony needed).
Proof size: 128 bytes (Groth16 compressed BN254). Verification gas: ~350k.
Implementation tool: Circom 2.0 + snarkjs (JavaScript proof generation).
Python bridge (Phase 14): subprocess call to snarkjs, OR py_ecc for BN254.

Phase 14 deliverable:
  - contracts/circuits/TeamProof.circom   — actual Circom circuit
  - bridge/zk_prover.py                  — proof generation via snarkjs
  - bridge/swarm_zk_aggregator.py        — real proof path (this file updated)
===========================================================================
"""

from __future__ import annotations

import hashlib
import struct
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # ChainClient import would be circular; use string annotation in method sigs

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOCK_PROOF_SIZE = 256      # Phase 13: mock proof is 256 bytes (accepted by Solidity mock)
ZK_VERSION = "TeamProofZK_Groth16_Phase14"

# Cheat range from TeamProofAggregator (must match on-chain constants)
CHEAT_INFERENCE_MIN = 0x28
CHEAT_INFERENCE_MAX = 0x2A


# ---------------------------------------------------------------------------
# SwarmZKAggregator
# ---------------------------------------------------------------------------

class SwarmZKAggregator:
    """
    ZK-aware swarm aggregation for team PoAC proofs.

    Phase 13: Generates structured 256-byte mock proofs and verifies their structure.
    Phase 14: Replace generate_mock_proof() with real Groth16 proof generation
              (snarkjs or py_ecc). The submit_team_proof_zk() interface is stable.

    Mock proof format (256 bytes):
      [0:32]   merkle_root   — Merkle root of sorted record hashes
      [32:64]  nullifier     — keccak256(team_id || epoch_bytes)
      [64:66]  member_count  — uint16 big-endian
      [66:256] padding       — zeros (reserved for real Groth16 proof in Phase 14)
    """

    def generate_mock_proof(
        self,
        team_id: bytes,
        record_hashes: List[bytes],
        member_device_ids: List[bytes],
        epoch: int = 0,
    ) -> bytes:
        """
        Generate a structured 256-byte mock proof.

        The proof embeds the Merkle root and a nullifier derived from
        team_id + epoch. Structure is deterministic and inspectable.

        Args:
            team_id:           32-byte team identifier.
            record_hashes:     List of 32-byte PoAC record SHA-256 hashes.
            member_device_ids: List of 32-byte device IDs (keccak256 of pubkeys).
            epoch:             Optional epoch counter for nullifier uniqueness.

        Returns:
            256-byte mock proof bytes.
        """
        if not record_hashes:
            raise ValueError("record_hashes must not be empty")

        merkle_root = self.compute_merkle_root(record_hashes)
        nullifier = self.compute_nullifier(team_id, epoch)

        member_count = len(record_hashes)
        buf = bytearray(MOCK_PROOF_SIZE)

        # [0:32] merkle root
        buf[0:32] = merkle_root

        # [32:64] nullifier
        buf[32:64] = nullifier

        # [64:66] member count (uint16 big-endian)
        buf[64:66] = struct.pack(">H", member_count)

        # [66:256] padding zeros (reserved for Phase 14 Groth16 proof)
        # (already zero from bytearray initialization)

        return bytes(buf)

    def verify_mock_proof(
        self,
        proof: bytes,
        merkle_root: bytes,
    ) -> bool:
        """
        Verify mock proof structure (not cryptographic).

        Checks:
          - Correct length (256 bytes)
          - First 32 bytes match the expected merkle_root
          - Member count field is in valid range [2, 6]

        Phase 14: Replace with real Groth16 verification (py_ecc or snarkjs).
        """
        if len(proof) != MOCK_PROOF_SIZE:
            return False
        if proof[0:32] != merkle_root:
            return False
        member_count = struct.unpack(">H", proof[64:66])[0]
        if not (2 <= member_count <= 6):
            return False
        return True

    def compute_merkle_root(self, record_hashes: List[bytes]) -> bytes:
        """
        Compute the Merkle root of a list of 32-byte record hashes.

        Algorithm matches TeamProofAggregator._computeMerkleRoot() on-chain:
          1. Sort hashes lexicographically (ascending byte order).
          2. Pairwise keccak256 of adjacent pairs until one hash remains.
          3. Odd leaf is promoted (no duplication).

        Args:
            record_hashes: List of 32-byte SHA-256 hashes.

        Returns:
            32-byte Merkle root.
        """
        if not record_hashes:
            raise ValueError("Cannot compute Merkle root of empty list")

        # Validate all hashes are 32 bytes
        for h in record_hashes:
            if len(h) != 32:
                raise ValueError(f"All record hashes must be 32 bytes, got {len(h)}")

        leaves = sorted(record_hashes)  # lexicographic sort

        if len(leaves) == 1:
            return leaves[0]

        current = leaves
        while len(current) > 1:
            next_level = []
            i = 0
            while i < len(current):
                if i + 1 < len(current):
                    # keccak256(left || right)
                    combined = current[i] + current[i + 1]
                    next_level.append(hashlib.new("sha3_256", combined).digest()
                                      if False else  # keccak256 not in stdlib
                                      self._keccak256(combined))
                    i += 2
                else:
                    # Odd leaf: promote unchanged (matches Solidity behavior)
                    next_level.append(current[i])
                    i += 1
            current = next_level

        return current[0]

    @staticmethod
    def _keccak256(data: bytes) -> bytes:
        """Keccak-256 using eth-hash (always available via requirements.txt)."""
        from eth_hash.auto import keccak
        return keccak(data)

    def compute_nullifier(self, team_id: bytes, epoch: int = 0) -> bytes:
        """
        Derive anti-replay nullifier: keccak256(team_id || epoch_bytes).

        Each (team_id, epoch) pair produces a unique nullifier. The on-chain
        usedNullifiers mapping ensures each nullifier can only be submitted once.

        Args:
            team_id: 32-byte team identifier.
            epoch:   Epoch counter (uint32).

        Returns:
            32-byte nullifier hash.
        """
        epoch_bytes = struct.pack(">I", epoch & 0xFFFFFFFF)
        return self._keccak256(team_id + epoch_bytes)

    async def submit_team_proof_zk(
        self,
        chain,  # ChainClient
        team_id: bytes,
        record_hashes: List[bytes],
        member_device_ids: List[bytes],
        epoch: int = 0,
        # Phase 14C: optional real-proof inputs (required when ZK artifacts present)
        inference_results: Optional[List[int]] = None,
        identity_secrets: Optional[List[int]] = None,
        active_flags: Optional[List[int]] = None,
    ) -> str:
        """
        Submit a ZK team proof to the TeamProofAggregatorZK contract.

        Phase 13 (mock path):
          1. Generate 256-byte mock proof.
          2. Compute nullifier.
          3. Call chain.submit_team_proof_zk() with the proof bytes.

        Phase 14C (real path — when VAPI_ZK_WASM_PATH + VAPI_ZK_ZKEY_PATH are set):
          - Uses ZKProver to generate a real Groth16 BN254 proof from circuit inputs.
          - inference_results / identity_secrets / active_flags must be supplied.
          - poseidonMerkleRoot and nullifierHash come from the prover output.

        Args:
            chain:             ChainClient instance with submit_team_proof_zk() method.
            team_id:           32-byte team identifier.
            record_hashes:     List of 32-byte PoAC record SHA-256 hashes.
            member_device_ids: List of 32-byte device IDs.
            epoch:             Epoch counter for nullifier uniqueness.
            inference_results: 8-bit VAPI inference codes (Phase 14C real path).
            identity_secrets:  Poseidon(deviceId) integers (Phase 14C real path).
            active_flags:      Active-slot bitmask (Phase 14C real path).

        Returns:
            Transaction hash string.
        """
        from zk_prover import ZKProver, ZK_ARTIFACTS_AVAILABLE

        if ZK_ARTIFACTS_AVAILABLE and inference_results is not None:
            # Phase 14C: real Groth16 proof
            member_count = len([f for f in (active_flags or []) if f])
            member_count = max(member_count, 2)
            prover = ZKProver()
            proof, poseidon_root, nullifier = prover.generate_proof(
                inference_results=inference_results,
                identity_secrets=identity_secrets or [0] * 6,
                active_flags=active_flags or [0] * 6,
                member_count=member_count,
                epoch=epoch,
                team_id=team_id,
            )
            merkle_root = self.compute_merkle_root(record_hashes)
        else:
            # Phase 13 mock fallback
            proof = self.generate_mock_proof(team_id, record_hashes, member_device_ids, epoch)
            merkle_root = self.compute_merkle_root(record_hashes)
            nullifier = self.compute_nullifier(team_id, epoch)

        return await chain.submit_team_proof_zk(
            team_id=team_id,
            record_hashes=record_hashes,
            merkle_root=merkle_root,
            nullifier_hash=nullifier,
            zk_proof=proof,
        )
