"""
Phase 13 — swarm_zk_aggregator.py tests.

Tests cover:
- generate_mock_proof() returns exactly 256 bytes
- generate_mock_proof() embeds correct merkle_root at [0:32]
- generate_mock_proof() embeds correct nullifier at [32:64]
- generate_mock_proof() embeds member_count at [64:66]
- verify_mock_proof() returns True for valid mock proof
- verify_mock_proof() returns False for wrong-length proof
- verify_mock_proof() returns False when merkle root mismatch
- compute_merkle_root() produces stable 32-byte hash
- compute_merkle_root() is deterministic: same inputs → same root
- compute_merkle_root() changes when record hashes change
- compute_merkle_root() sorts lexicographically (order-independent)
- compute_nullifier() returns 32 bytes and changes with epoch
- submit_team_proof_zk() calls chain with correct arguments (mock chain)
"""

import sys
import struct
import hashlib
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parents[1]))

from swarm_zk_aggregator import SwarmZKAggregator, MOCK_PROOF_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hashes(n: int) -> list:
    """Generate n distinct 32-byte hashes."""
    return [hashlib.sha256(f"record_{i}".encode()).digest() for i in range(n)]


TEAM_ID = hashlib.sha256(b"Team Alpha").digest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGenerateMockProof(unittest.TestCase):

    def setUp(self):
        self.agg = SwarmZKAggregator()

    def test_returns_256_bytes(self):
        proof = self.agg.generate_mock_proof(TEAM_ID, _make_hashes(3), [b"\x00" * 32] * 3)
        self.assertEqual(len(proof), MOCK_PROOF_SIZE)

    def test_merkle_root_embedded_at_offset_0(self):
        hashes = _make_hashes(3)
        merkle_root = self.agg.compute_merkle_root(hashes)
        proof = self.agg.generate_mock_proof(TEAM_ID, hashes, [b"\x00" * 32] * 3)
        self.assertEqual(proof[0:32], merkle_root)

    def test_nullifier_embedded_at_offset_32(self):
        hashes = _make_hashes(2)
        expected_nullifier = self.agg.compute_nullifier(TEAM_ID, epoch=0)
        proof = self.agg.generate_mock_proof(TEAM_ID, hashes, [b"\x00" * 32] * 2)
        self.assertEqual(proof[32:64], expected_nullifier)

    def test_member_count_embedded_at_offset_64(self):
        hashes = _make_hashes(4)
        proof = self.agg.generate_mock_proof(TEAM_ID, hashes, [b"\x00" * 32] * 4)
        member_count = struct.unpack(">H", proof[64:66])[0]
        self.assertEqual(member_count, 4)

    def test_padding_is_zeros(self):
        proof = self.agg.generate_mock_proof(TEAM_ID, _make_hashes(2), [b"\x00" * 32] * 2)
        self.assertEqual(proof[66:], b"\x00" * (MOCK_PROOF_SIZE - 66))

    def test_raises_for_empty_hashes(self):
        with self.assertRaises(ValueError):
            self.agg.generate_mock_proof(TEAM_ID, [], [])


class TestVerifyMockProof(unittest.TestCase):

    def setUp(self):
        self.agg = SwarmZKAggregator()
        self.hashes = _make_hashes(3)
        self.merkle_root = self.agg.compute_merkle_root(self.hashes)
        self.proof = self.agg.generate_mock_proof(TEAM_ID, self.hashes, [b"\x00" * 32] * 3)

    def test_valid_proof_returns_true(self):
        self.assertTrue(self.agg.verify_mock_proof(self.proof, self.merkle_root))

    def test_wrong_length_returns_false(self):
        self.assertFalse(self.agg.verify_mock_proof(b"\xab" * 64, self.merkle_root))

    def test_merkle_root_mismatch_returns_false(self):
        wrong_root = b"\xff" * 32
        self.assertFalse(self.agg.verify_mock_proof(self.proof, wrong_root))

    def test_corrupted_member_count_returns_false(self):
        # Set member_count to 0 (out of valid range [2,6])
        bad_proof = bytearray(self.proof)
        bad_proof[64:66] = struct.pack(">H", 0)
        self.assertFalse(self.agg.verify_mock_proof(bytes(bad_proof), self.merkle_root))


class TestMerkleRoot(unittest.TestCase):

    def setUp(self):
        self.agg = SwarmZKAggregator()

    def test_returns_32_bytes(self):
        root = self.agg.compute_merkle_root(_make_hashes(3))
        self.assertEqual(len(root), 32)

    def test_deterministic_for_same_input(self):
        hashes = _make_hashes(4)
        r1 = self.agg.compute_merkle_root(hashes)
        r2 = self.agg.compute_merkle_root(hashes)
        self.assertEqual(r1, r2)

    def test_different_records_different_root(self):
        r1 = self.agg.compute_merkle_root(_make_hashes(3))
        alt = [hashlib.sha256(f"alt_{i}".encode()).digest() for i in range(3)]
        r2 = self.agg.compute_merkle_root(alt)
        self.assertNotEqual(r1, r2)

    def test_order_independent(self):
        """Merkle root sorts leaves — order of input should not matter."""
        hashes = _make_hashes(4)
        shuffled = [hashes[2], hashes[0], hashes[3], hashes[1]]
        self.assertEqual(
            self.agg.compute_merkle_root(hashes),
            self.agg.compute_merkle_root(shuffled),
        )

    def test_single_hash_returns_itself(self):
        h = _make_hashes(1)
        self.assertEqual(self.agg.compute_merkle_root(h), h[0])

    def test_raises_for_empty_list(self):
        with self.assertRaises(ValueError):
            self.agg.compute_merkle_root([])

    def test_raises_for_wrong_hash_length(self):
        with self.assertRaises(ValueError):
            self.agg.compute_merkle_root([b"\x00" * 16])


class TestNullifier(unittest.TestCase):

    def setUp(self):
        self.agg = SwarmZKAggregator()

    def test_returns_32_bytes(self):
        n = self.agg.compute_nullifier(TEAM_ID, epoch=0)
        self.assertEqual(len(n), 32)

    def test_different_epoch_different_nullifier(self):
        n0 = self.agg.compute_nullifier(TEAM_ID, epoch=0)
        n1 = self.agg.compute_nullifier(TEAM_ID, epoch=1)
        self.assertNotEqual(n0, n1)

    def test_different_team_different_nullifier(self):
        team_b = hashlib.sha256(b"Team Beta").digest()
        n_a = self.agg.compute_nullifier(TEAM_ID, epoch=0)
        n_b = self.agg.compute_nullifier(team_b, epoch=0)
        self.assertNotEqual(n_a, n_b)


class TestSubmitTeamProofZK(unittest.IsolatedAsyncioTestCase):

    async def test_submit_calls_chain_with_correct_args(self):
        agg = SwarmZKAggregator()
        hashes = _make_hashes(3)
        device_ids = [b"\x01" * 32] * 3

        # Mock chain client
        chain = MagicMock()
        chain.submit_team_proof_zk = AsyncMock(return_value="0x" + "ab" * 32)

        tx = await agg.submit_team_proof_zk(chain, TEAM_ID, hashes, device_ids, epoch=5)

        self.assertEqual(tx, "0x" + "ab" * 32)
        chain.submit_team_proof_zk.assert_called_once()
        call_kwargs = chain.submit_team_proof_zk.call_args.kwargs

        # Verify correct Merkle root and nullifier were passed
        expected_root = agg.compute_merkle_root(hashes)
        expected_nullifier = agg.compute_nullifier(TEAM_ID, epoch=5)
        self.assertEqual(call_kwargs["merkle_root"], expected_root)
        self.assertEqual(call_kwargs["nullifier_hash"], expected_nullifier)
        self.assertEqual(len(call_kwargs["zk_proof"]), MOCK_PROOF_SIZE)


if __name__ == "__main__":
    unittest.main()
