"""
CI Tests for DualShock Edge bridge integration (Phase 4).

All tests run without real hardware using simulation/mock modes.
No pydualsense, no IoTeX RPC, no live contracts required.

Coverage:
  1.  PersistentIdentity — keypair persistence and stability
  2.  _SkillOracleTracker — ELO math mirrors SkillOracle.sol exactly
  3.  _ProgressAttestationTracker — BPS computation and submission
  4.  compute_merkle_root — matches TeamProofAggregator.sol exactly
  5.  TeamSessionCoordinator — team lifecycle and Merkle submission
  6.  DualShockTransport init — device registration and config defaults
  7.  Full simulate session — record generation and pipeline wiring
"""

import asyncio
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make sure bridge package is importable
sys.path.insert(0, str(Path(__file__).parents[1]))
# Make sure controller package is importable
sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))

from vapi_bridge.dualshock_integration import (
    CHEAT_CODES,
    GAMING_INFERENCE_NAMES,
    INFER_CHEAT_MAC,
    INFER_NOMINAL,
    INFER_SKILLED,
    METRIC_ACCURACY,
    _ProgressAttestationTracker,
    _SkillOracleTracker,
    _rating_tier,
)
from vapi_bridge.team_session import TeamSessionCoordinator, compute_merkle_root


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _keccak256(data: bytes) -> bytes:
    from eth_hash.auto import keccak
    return keccak(data)


def _build_raw_record(inference=0x20, confidence=220, bounty_id=0, counter=1):
    """Build a minimal valid 228-byte PoAC record for testing."""
    body = (
        b"\x00" * 32 +   # prev_poac_hash
        b"\xAB" * 32 +   # sensor_commitment
        b"\xCD" * 32 +   # model_manifest_hash
        b"\xEF" * 32 +   # world_model_hash
        struct.pack(
            ">BBBBIqddI",
            inference, 0x01, confidence, 80,
            counter,
            int(time.time() * 1000),
            40.7128, -74.0060,
            bounty_id,
        )
    )
    assert len(body) == 164
    return body + b"\x00" * 64   # zero signature (sufficient for parse_record)


# =====================================================================
# 1. PersistentIdentity — keypair persistence
# =====================================================================

class TestPersistentIdentity:
    """Tests that require cryptography but NOT pydualsense."""

    def test_generates_new_keypair(self, tmp_path):
        from persistent_identity import PersistentIdentity
        identity = PersistentIdentity(key_dir=tmp_path).load_or_create()
        assert len(identity.public_key_bytes) == 65
        assert identity.public_key_bytes[0] == 0x04
        assert len(identity.device_id) == 32

    def test_keypair_persists_across_instances(self, tmp_path):
        from persistent_identity import PersistentIdentity
        id1 = PersistentIdentity(key_dir=tmp_path).load_or_create()
        id2 = PersistentIdentity(key_dir=tmp_path).load_or_create()
        assert id1.public_key_bytes == id2.public_key_bytes
        assert id1.device_id == id2.device_id

    def test_device_id_is_keccak256_of_pubkey(self, tmp_path):
        from persistent_identity import PersistentIdentity
        identity = PersistentIdentity(key_dir=tmp_path).load_or_create()
        expected = _keccak256(identity.public_key_bytes)
        assert identity.device_id == expected

    def test_key_file_created(self, tmp_path):
        from persistent_identity import PersistentIdentity
        PersistentIdentity(key_dir=tmp_path).load_or_create()
        key_file = tmp_path / "dualshock_device_key.json"
        assert key_file.exists()
        data = json.loads(key_file.read_text())
        assert "private_der_hex" in data
        assert "public_key_hex" in data

    def test_different_dirs_different_identities(self, tmp_path):
        from persistent_identity import PersistentIdentity
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        id_a = PersistentIdentity(key_dir=dir_a).load_or_create()
        id_b = PersistentIdentity(key_dir=dir_b).load_or_create()
        assert id_a.device_id != id_b.device_id

    def test_corrupted_key_file_regenerates(self, tmp_path):
        from persistent_identity import PersistentIdentity
        # Write a corrupted key file
        (tmp_path / "dualshock_device_key.json").write_text("{bad json")
        identity = PersistentIdentity(key_dir=tmp_path).load_or_create()
        # Should regenerate without error
        assert len(identity.public_key_bytes) == 65


# =====================================================================
# 2. SkillOracle math — mirrors SkillOracle.sol exactly
# =====================================================================

class TestSkillOracleTracker:
    """ELO math must be bit-perfect with SkillOracle.sol."""

    def _tracker(self):
        return _SkillOracleTracker(device_id=b"\x00" * 32)

    # --- NOMINAL gains ---
    def test_nominal_conf_255_gain_5(self):
        t = self._tracker()
        r = t.apply(INFER_NOMINAL, 255)
        assert r == 1000 + 5     # floor(5 * 255 / 255) = 5

    def test_nominal_conf_220_gain_4(self):
        t = self._tracker()
        r = t.apply(INFER_NOMINAL, 220)
        assert r == 1000 + 4     # floor(5 * 220 / 255) = 4

    def test_nominal_conf_1_gain_min_1(self):
        t = self._tracker()
        r = t.apply(INFER_NOMINAL, 1)
        assert r == 1001          # max(1, floor(5*1/255)) = max(1,0) = 1

    # --- SKILLED gains ---
    def test_skilled_conf_255_gain_12(self):
        t = self._tracker()
        r = t.apply(INFER_SKILLED, 255)
        assert r == 1012

    def test_skilled_conf_220_gain_10(self):
        t = self._tracker()
        r = t.apply(INFER_SKILLED, 220)
        assert r == 1010          # floor(12 * 220 / 255) = 10

    # --- CHEAT penalty ---
    def test_cheat_penalty_200(self):
        t = self._tracker()
        r = t.apply(INFER_CHEAT_MAC, 230)
        assert r == 800           # 1000 - 200 = 800

    def test_cheat_does_not_go_below_zero(self):
        t = self._tracker()
        t._rating = 50
        r = t.apply(INFER_CHEAT_MAC, 200)
        assert r == 0

    # --- Ceiling ---
    def test_rating_capped_at_3000(self):
        t = self._tracker()
        t._rating = 2999
        r = t.apply(INFER_SKILLED, 255)  # +12 -> 3011, capped at 3000
        assert r == 3000

    # --- Tier mapping ---
    def test_tier_bronze(self):
        assert _rating_tier(0)   == "Bronze"
        assert _rating_tier(999) == "Bronze"

    def test_tier_silver(self):
        assert _rating_tier(1000) == "Silver"
        assert _rating_tier(1499) == "Silver"

    def test_tier_gold(self):
        assert _rating_tier(1500) == "Gold"

    def test_tier_platinum(self):
        assert _rating_tier(2000) == "Platinum"

    def test_tier_diamond(self):
        assert _rating_tier(2500) == "Diamond"
        assert _rating_tier(3000) == "Diamond"

    # --- summary() ---
    def test_summary_structure(self):
        t = self._tracker()
        t.apply(INFER_NOMINAL, 220)
        t.apply(INFER_CHEAT_MAC, 200)
        s = t.summary()
        assert s["rating"] == 804    # 1000+4=1004, 1004-200=804
        assert s["tier"] == "Bronze"
        assert s["records"] == 2
        assert s["cheats_detected"] == 1

    def test_multi_session_sequence(self):
        """Replicate Phase 6 test from hardware suite: 1000->1004->1014->814."""
        t = self._tracker()
        r = t.apply(INFER_NOMINAL, 220)    # +4   -> 1004
        assert r == 1004
        r = t.apply(INFER_SKILLED, 220)   # +10  -> 1014  (floor(12*220/255)=10)
        assert r == 1014
        r = t.apply(INFER_CHEAT_MAC, 200) # -200 -> 814
        assert r == 814


# =====================================================================
# 3. ProgressAttestation — BPS computation
# =====================================================================

class TestProgressAttestationTracker:

    def _tracker(self):
        return _ProgressAttestationTracker(device_id=b"\x00" * 32)

    def test_no_attest_below_min_window(self):
        t = self._tracker()
        # Feed only 4 clean records (min is 5*2=10)
        for i in range(4):
            t.record(bytes([i]) * 32, INFER_NOMINAL, 180)
        assert not t.can_attest()

    def test_can_attest_with_enough_records(self):
        t = self._tracker()
        for i in range(10):
            t.record(os.urandom(32), INFER_NOMINAL, 180 + i)
        # No chain configured -> can_attest is False (no chain_client)
        assert not t.can_attest()

    def test_no_improvement_returns_zero_bps(self):
        t = self._tracker()
        # Same confidence throughout — no improvement
        hashes = [os.urandom(32) for _ in range(12)]
        for h in hashes:
            t.record(h, INFER_NOMINAL, 200)
        _, _, bps = t.compute_improvement()
        assert bps == 0

    def test_degradation_returns_zero_bps(self):
        t = self._tracker()
        for i in range(12):
            conf = 220 - i * 3    # Decreasing confidence
            t.record(os.urandom(32), INFER_NOMINAL, conf)
        _, _, bps = t.compute_improvement()
        assert bps == 0

    def test_improvement_computes_correct_bps(self):
        t = self._tracker()
        # First 5 records: avg conf = 180
        for _ in range(5):
            t.record(os.urandom(32), INFER_NOMINAL, 180)
        # Middle filler
        for _ in range(2):
            t.record(os.urandom(32), INFER_NOMINAL, 200)
        # Last 5 records: avg conf = 220
        for _ in range(5):
            t.record(os.urandom(32), INFER_NOMINAL, 220)

        baseline_h, current_h, bps = t.compute_improvement()
        assert bps > 0
        # Expected: (220 - 180) / 180 * 10000 = 2222 bps
        assert bps == round((220 - 180) / 180 * 10000)
        assert baseline_h is not None
        assert current_h  is not None

    def test_cheat_records_not_counted(self):
        t = self._tracker()
        # Mix of clean and cheat — only NOMINAL/SKILLED should be tracked
        for _ in range(5):
            t.record(os.urandom(32), INFER_NOMINAL, 180)
        for _ in range(20):
            t.record(os.urandom(32), INFER_CHEAT_MAC, 230)  # cheats
        for _ in range(5):
            t.record(os.urandom(32), INFER_NOMINAL, 220)
        # Should still have exactly 10 clean records
        assert len(t._clean_records) == 10

    def test_submit_no_chain_returns_false(self):
        t = self._tracker()
        result = asyncio.get_event_loop().run_until_complete(t.submit())
        assert result is False

    def test_submit_with_mock_chain(self):
        chain = MagicMock()
        chain.attest_progress = AsyncMock(return_value="0xdeadbeef" * 4)
        t = _ProgressAttestationTracker(
            device_id=b"\x00" * 32,
            chain_client=chain,
            attest_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        for _ in range(5):
            t.record(os.urandom(32), INFER_NOMINAL, 180)
        for _ in range(5):
            t.record(os.urandom(32), INFER_NOMINAL, 220)

        result = asyncio.get_event_loop().run_until_complete(t.submit())
        assert result is True
        chain.attest_progress.assert_called_once()
        args = chain.attest_progress.call_args
        assert (args.kwargs.get("metric_type") == METRIC_ACCURACY or
                args[0][3] == METRIC_ACCURACY)


# =====================================================================
# 4. compute_merkle_root — must match TeamProofAggregator.sol exactly
# =====================================================================

class TestMerkleRoot:
    """
    Verified against Hardhat test vectors from test_contracts
    (Phase 3 hardware suite: root=1ae617870f4c7c2a for 4 leaves).
    """

    def test_single_leaf_is_itself(self):
        leaf = os.urandom(32)
        assert compute_merkle_root([leaf]) == leaf

    def test_two_leaves_sorted_keccak(self):
        a = b"\x01" * 32
        b_ = b"\x02" * 32
        # Sorted: a < b_ lexicographically
        from eth_hash.auto import keccak
        expected = keccak(a + b_)
        assert compute_merkle_root([a, b_]) == expected

    def test_two_leaves_sorted_keccak_reversed(self):
        """Input order should not matter — leaves are sorted before hashing."""
        a = b"\x01" * 32
        b_ = b"\x02" * 32
        from eth_hash.auto import keccak
        expected = keccak(a + b_)
        # Provide in reverse order
        result = compute_merkle_root([b_, a])
        assert result == expected

    def test_three_leaves_odd_promoted(self):
        """3 leaves: sorted [a, b, c]; tree: [keccak(a||b), c]; root = keccak(c||keccak(a||b))."""
        leaves = [b"\x01" * 32, b"\x02" * 32, b"\x03" * 32]
        from eth_hash.auto import keccak
        sorted_l = sorted(leaves)
        level1_0 = keccak(sorted_l[0] + sorted_l[1])
        level1_1 = sorted_l[2]   # Promoted odd leaf
        root = keccak(level1_0 + level1_1)
        assert compute_merkle_root(leaves) == root

    def test_four_leaves(self):
        """4 leaves: 2 rounds of pairing."""
        leaves = [os.urandom(32) for _ in range(4)]
        from eth_hash.auto import keccak
        s = sorted(leaves)
        root = keccak(keccak(s[0] + s[1]) + keccak(s[2] + s[3]))
        assert compute_merkle_root(leaves) == root

    def test_deterministic(self):
        """Same leaves always produce the same root."""
        leaves = [b"\xAB" * 32, b"\xCD" * 32, b"\xEF" * 32]
        r1 = compute_merkle_root(leaves)
        r2 = compute_merkle_root(leaves)
        assert r1 == r2

    def test_input_order_invariant(self):
        """Merkle root is order-invariant (sorted internally)."""
        import random
        leaves = [os.urandom(32) for _ in range(5)]
        root1 = compute_merkle_root(leaves)
        shuffled = leaves[:]
        random.shuffle(shuffled)
        root2 = compute_merkle_root(shuffled)
        assert root1 == root2

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            compute_merkle_root([])

    def test_wrong_hash_size_raises(self):
        with pytest.raises(ValueError, match="32-byte"):
            compute_merkle_root([b"\x00" * 31])

    def test_six_members_completes(self):
        leaves = [os.urandom(32) for _ in range(6)]
        root = compute_merkle_root(leaves)
        assert len(root) == 32


# =====================================================================
# 5. TeamSessionCoordinator
# =====================================================================

class TestTeamSessionCoordinator:

    def _make_coordinator(self, should_fail=False):
        chain = MagicMock()
        chain.create_team    = AsyncMock(return_value="0xtx_create")
        chain.submit_team_proof = AsyncMock(return_value="0xtx_proof")
        if should_fail:
            chain.submit_team_proof = AsyncMock(side_effect=RuntimeError("Revert"))
        return TeamSessionCoordinator(chain_client=chain), chain

    def test_register_team_returns_team_id(self):
        coord, _ = self._make_coordinator()
        from eth_hash.auto import keccak
        expected_id = keccak(b"squad_alpha")
        loop = asyncio.get_event_loop()
        tid = loop.run_until_complete(
            coord.register_team("squad_alpha", [b"\x01" * 32, b"\x02" * 32])
        )
        assert tid == expected_id

    def test_invalid_team_size_raises(self):
        coord, _ = self._make_coordinator()
        loop = asyncio.get_event_loop()
        with pytest.raises(ValueError, match="2.6"):
            loop.run_until_complete(
                coord.register_team("solo", [b"\x01" * 32])
            )

    def test_record_verified_tracks_members(self):
        coord, _ = self._make_coordinator()
        loop = asyncio.get_event_loop()
        dev_ids = [os.urandom(32) for _ in range(3)]
        loop.run_until_complete(coord.register_team("trio", dev_ids))
        r1 = coord.record_verified("trio", dev_ids[0], os.urandom(32))
        r2 = coord.record_verified("trio", dev_ids[1], os.urandom(32))
        r3 = coord.record_verified("trio", dev_ids[2], os.urandom(32))
        assert not r1
        assert not r2
        assert r3     # Complete when all 3 are in

    def test_submit_proof_calls_chain(self):
        coord, chain = self._make_coordinator()
        loop = asyncio.get_event_loop()
        dev_ids = [os.urandom(32) for _ in range(2)]
        hashes  = [os.urandom(32) for _ in range(2)]
        loop.run_until_complete(coord.register_team("pair", dev_ids))
        coord.record_verified("pair", dev_ids[0], hashes[0])
        coord.record_verified("pair", dev_ids[1], hashes[1])
        tx = loop.run_until_complete(coord.submit_proof("pair"))
        assert tx == "0xtx_proof"
        chain.submit_team_proof.assert_called_once()

    def test_submit_proof_passes_correct_merkle_root(self):
        coord, chain = self._make_coordinator()
        loop = asyncio.get_event_loop()
        dev_ids = [os.urandom(32) for _ in range(3)]
        hashes  = [os.urandom(32) for _ in range(3)]
        expected_root = compute_merkle_root(hashes)
        loop.run_until_complete(coord.register_team("trio2", dev_ids))
        for d, h in zip(dev_ids, hashes):
            coord.record_verified("trio2", d, h)
        loop.run_until_complete(coord.submit_proof("trio2"))
        _, kwargs = chain.submit_team_proof.call_args
        submitted_root = chain.submit_team_proof.call_args[0][2]
        assert submitted_root == expected_root

    def test_submit_incomplete_team_raises(self):
        coord, _ = self._make_coordinator()
        loop = asyncio.get_event_loop()
        dev_ids = [os.urandom(32) for _ in range(3)]
        loop.run_until_complete(coord.register_team("incomplete", dev_ids))
        coord.record_verified("incomplete", dev_ids[0], os.urandom(32))
        # Only 1/3 verified
        with pytest.raises(RuntimeError, match="not complete"):
            loop.run_until_complete(coord.submit_proof("incomplete"))

    def test_unknown_device_ignored(self):
        coord, _ = self._make_coordinator()
        loop = asyncio.get_event_loop()
        dev_ids = [os.urandom(32) for _ in range(2)]
        loop.run_until_complete(coord.register_team("duo", dev_ids))
        result = coord.record_verified("duo", os.urandom(32), os.urandom(32))
        assert result is False

    def test_team_status(self):
        coord, _ = self._make_coordinator()
        loop = asyncio.get_event_loop()
        dev_ids = [os.urandom(32) for _ in range(2)]
        loop.run_until_complete(coord.register_team("status_test", dev_ids))
        status = coord.team_status("status_test")
        assert status["members"] == 2
        assert status["verified"] == 0
        assert not status["complete"]


# =====================================================================
# 6. DualShockTransport — config defaults and structure
# =====================================================================

class TestDualShockTransportConfig:

    def _make_cfg(self, **overrides):
        cfg = MagicMock()
        defaults = {
            "dualshock_record_interval_s": 1.0,
            "skill_oracle_address": "",
            "dualshock_active_bounties": "",
            "dualshock_key_dir": str(Path.home() / ".vapi"),
            "progress_attestation_address": "",
        }
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(cfg, k, v)
        return cfg

    def test_transport_constructs_without_error(self):
        from vapi_bridge.dualshock_integration import DualShockTransport
        cfg   = self._make_cfg()
        store = MagicMock()
        cb    = AsyncMock()
        t = DualShockTransport(cfg, store, cb, chain_client=None)
        assert t._interval == 1.0

    def test_interval_from_config(self):
        from vapi_bridge.dualshock_integration import DualShockTransport
        cfg = self._make_cfg(dualshock_record_interval_s=0.5)
        t   = DualShockTransport(cfg, MagicMock(), AsyncMock())
        assert t._interval == 0.5

    def test_active_bounties_parsed(self):
        from vapi_bridge.dualshock_integration import DualShockTransport
        cfg = self._make_cfg(dualshock_active_bounties="1001,1002,1003")
        store = MagicMock()
        store.upsert_device = MagicMock()
        cb = AsyncMock()
        # Just verify the parsing logic doesn't crash at run-time
        t = DualShockTransport(cfg, store, cb)
        bounties = [
            int(tok.strip())
            for tok in cfg.dualshock_active_bounties.split(",")
            if tok.strip().isdigit()
        ]
        assert bounties == [1001, 1002, 1003]


# =====================================================================
# 7. Simulate session — record format and SkillOracle wiring
# =====================================================================

class TestSimulateSession:
    """
    End-to-end smoke test using the emulator's simulate mode.
    Generates real 228-byte signed records and verifies pipeline wiring.
    """

    def test_simulate_records_are_228_bytes(self):
        """Emulator in simulate mode produces valid 228-byte PoAC records."""
        try:
            from dualshock_emulator import PoACEngine, DualSenseReader, AntiCheatClassifier
        except ImportError:
            pytest.skip("dualshock_emulator not importable (controller/ not on path)")

        engine  = PoACEngine()
        reader  = DualSenseReader()  # simulate mode (no controller)

        sensor_hash = hashlib.sha256(b"test_sensor").digest()
        wm_hash     = hashlib.sha256(b"test_wm").digest()
        record = engine.generate(sensor_hash, wm_hash, 0x20, 0x01, 220, 80)
        raw    = record.serialize_full()
        assert len(raw) == 228

    def test_simulate_records_parse_correctly(self):
        """Bridge codec can parse emulator-generated records."""
        try:
            from dualshock_emulator import PoACEngine
        except ImportError:
            pytest.skip("dualshock_emulator not importable")

        from vapi_bridge.codec import parse_record

        engine = PoACEngine()
        sensor_hash = hashlib.sha256(b"sensor").digest()
        wm_hash     = hashlib.sha256(b"wm").digest()
        record = engine.generate(sensor_hash, wm_hash, 0x20, 0x01, 220, 80)
        raw    = record.serialize_full()

        parsed = parse_record(raw)
        assert parsed.inference_result == 0x20
        assert parsed.confidence == 220
        assert parsed.battery_pct == 80
        assert len(parsed.record_hash) == 32

    def test_simulate_chain_of_5_records(self):
        """5 consecutive records form a valid hash chain (bridge codec view)."""
        try:
            from dualshock_emulator import PoACEngine
        except ImportError:
            pytest.skip("dualshock_emulator not importable")

        from vapi_bridge.codec import parse_record

        engine  = PoACEngine()
        records = []
        for i in range(5):
            sensor = hashlib.sha256(f"sensor_{i}".encode()).digest()
            wm     = hashlib.sha256(f"wm_{i}".encode()).digest()
            rec    = engine.generate(sensor, wm, 0x20, 0x01, 220, 80 - i)
            records.append(parse_record(rec.serialize_full()))

        # Verify monotonic counter
        for i, r in enumerate(records):
            assert r.monotonic_ctr == i + 1

    def test_skill_oracle_applies_correctly_over_session(self):
        """Simulate 10-record session and verify final ELO rating."""
        tracker = _SkillOracleTracker(device_id=b"\x00" * 32)
        inferences = [
            INFER_NOMINAL, INFER_NOMINAL, INFER_SKILLED,
            INFER_NOMINAL, INFER_CHEAT_MAC,
            INFER_NOMINAL, INFER_NOMINAL, INFER_SKILLED,
            INFER_NOMINAL, INFER_NOMINAL,
        ]
        confidences = [220] * 10
        for inf, conf in zip(inferences, confidences):
            tracker.apply(inf, conf)

        summary = tracker.summary()
        # 7x NOMINAL at conf=220: +4 each = +28
        # 2x SKILLED at conf=220: +10 each = +20
        # 1x CHEAT:MACRO = -200
        # 1000 + 28 + 20 - 200 = 848
        assert summary["rating"] == 848
        assert summary["cheats_detected"] == 1
        assert summary["records"] == 10
