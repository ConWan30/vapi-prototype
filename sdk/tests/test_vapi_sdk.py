"""
Phase 64 — VAPI SDK tests (Phase 63 parity update).

40 tests across 7 groups:
    Group 1: TestVAPIRecord       (14) — parse, inference_name, is_clean, hashes, chain links, 0x31/0x32
    Group 2: TestVAPIDevice       (5)  — get_profile, unknown raises, certification, is_phci_certified
    Group 3: TestVAPIVerifier     (4)  — verify_record valid/invalid, verify_chain ordered/broken
    Group 4: TestVAPISession      (5)  — ingest callbacks, chain_integrity, summary, cheat callback
    Group 5: TestSDKSelfVerify    (5)  — SDKAttestation, L5 active, hash determinism, 5 layers, L2B
    Group 6: TestVAPIEnrollment   (4)  — offline fallback, is_eligible, sessions_remaining
    Group 7: TestVAPIZKProof      (3)  — validate valid, missing key, public_inputs order
"""

import asyncio
import hashlib
import struct
import sys
import unittest
from pathlib import Path

# sdk/ → sys.path so vapi_sdk is importable
_sdk_dir = str(Path(__file__).resolve().parents[1])
if _sdk_dir not in sys.path:
    sys.path.insert(0, _sdk_dir)

from vapi_sdk import (
    CHEAT_CODES, INFERENCE_NAMES, POAC_BODY_SIZE, POAC_RECORD_SIZE,
    SDK_VERSION, SDKAttestation, VAPIDevice, VAPIEnrollment,
    VAPIRecord, VAPISession, VAPIVerifier, VAPIZKProof,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_raw_record(
    inference: int = 0x20,
    action:    int = 0x01,
    confidence:int = 220,
    battery:   int = 95,
    ctr:       int = 1,
    ts_ms:     int = 1_700_000_000_000,
    prev_hash: bytes = b"\x00" * 32,
) -> bytes:
    """Build a syntactically valid 228-byte PoAC record for testing."""
    hashes = prev_hash + b"\xAB" * 32 + b"\xCD" * 32 + b"\xEF" * 32  # 128 bytes
    packed = struct.pack(">BBBBI", inference, action, confidence, battery, ctr)
    packed += struct.pack(">Q", ts_ms)
    packed += struct.pack(">ddI", 40.7128, -74.0060, 0)   # NYC coords
    body = hashes + packed                                  # 164 bytes
    assert len(body) == POAC_BODY_SIZE
    sig = b"\x00" * 64
    return body + sig                                       # 228 bytes


# ---------------------------------------------------------------------------
# Group 1: VAPIRecord
# ---------------------------------------------------------------------------

class TestVAPIRecord(unittest.TestCase):

    def test_parse_228_bytes(self):
        """Valid 228-byte record parses without error."""
        raw = _make_raw_record()
        rec = VAPIRecord(raw)
        self.assertEqual(len(rec._raw), POAC_RECORD_SIZE)
        self.assertEqual(rec.confidence, 220)
        self.assertEqual(rec.battery_pct, 95)
        self.assertEqual(rec.monotonic_ctr, 1)

    def test_wrong_size_raises(self):
        """VAPIRecord raises ValueError for non-228-byte input."""
        with self.assertRaises(ValueError):
            VAPIRecord(b"\x00" * 100)
        with self.assertRaises(ValueError):
            VAPIRecord(b"\x00" * 229)

    def test_inference_name_nominal(self):
        """inference_name returns 'NOMINAL' for code 0x20."""
        rec = VAPIRecord(_make_raw_record(inference=0x20))
        self.assertEqual(rec.inference_name, "NOMINAL")

    def test_is_clean_true_for_nominal(self):
        """is_clean is True for NOMINAL (0x20)."""
        rec = VAPIRecord(_make_raw_record(inference=0x20))
        self.assertTrue(rec.is_clean)

    def test_is_clean_false_for_cheat(self):
        """is_clean is False for DRIVER_INJECT (0x28)."""
        rec = VAPIRecord(_make_raw_record(inference=0x28))
        self.assertFalse(rec.is_clean)

    def test_record_hash_is_sha256_of_body(self):
        """record_hash equals SHA-256(raw[:164])."""
        raw = _make_raw_record()
        rec = VAPIRecord(raw)
        expected = hashlib.sha256(raw[:POAC_BODY_SIZE]).digest()
        self.assertEqual(rec.record_hash, expected)

    def test_chain_hash_is_sha256_of_full_record(self):
        """chain_hash equals SHA-256(raw[:228])."""
        raw = _make_raw_record()
        rec = VAPIRecord(raw)
        expected = hashlib.sha256(raw).digest()
        self.assertEqual(rec.chain_hash, expected)

    def test_verify_chain_link_genesis(self):
        """verify_chain_link(None) returns True when prev_poac_hash is all zeros."""
        rec = VAPIRecord(_make_raw_record(prev_hash=b"\x00" * 32))
        self.assertTrue(rec.verify_chain_link(None))

    def test_verify_chain_link_valid_continuation(self):
        """verify_chain_link(prev) returns True when prev_poac_hash == prev.record_hash (164B body)."""
        rec1 = VAPIRecord(_make_raw_record(ctr=1))
        rec2 = VAPIRecord(_make_raw_record(prev_hash=rec1.record_hash, ctr=2))
        self.assertTrue(rec2.verify_chain_link(rec1))

    def test_verify_chain_link_broken(self):
        """verify_chain_link returns False when prev_poac_hash doesn't match."""
        rec1 = VAPIRecord(_make_raw_record(ctr=1))
        rec2 = VAPIRecord(_make_raw_record(prev_hash=b"\xFF" * 32, ctr=2))
        self.assertFalse(rec2.verify_chain_link(rec1))

    def test_inference_name_0x31_imu_press_decoupled(self):
        rec = VAPIRecord(_make_raw_record(inference=0x31))
        self.assertEqual(rec.inference_name, "IMU_PRESS_DECOUPLED")

    def test_inference_name_0x32_stick_imu_decoupled(self):
        rec = VAPIRecord(_make_raw_record(inference=0x32))
        self.assertEqual(rec.inference_name, "STICK_IMU_DECOUPLED")

    def test_is_advisory_true_for_0x31(self):
        rec = VAPIRecord(_make_raw_record(inference=0x31))
        self.assertTrue(rec.is_advisory)

    def test_is_advisory_true_for_0x32(self):
        rec = VAPIRecord(_make_raw_record(inference=0x32))
        self.assertTrue(rec.is_advisory)


# ---------------------------------------------------------------------------
# Group 2: VAPIDevice
# ---------------------------------------------------------------------------

class TestVAPIDevice(unittest.TestCase):

    def test_get_profile_known(self):
        """get_profile('sony_dualshock_edge_v1') returns a DeviceProfile."""
        dev = VAPIDevice()
        profile = dev.get_profile("sony_dualshock_edge_v1")
        self.assertEqual(profile.profile_id, "sony_dualshock_edge_v1")
        self.assertEqual(dev.profile, profile)

    def test_get_profile_unknown_raises(self):
        """get_profile with unknown profile_id raises KeyError."""
        dev = VAPIDevice()
        with self.assertRaises(KeyError):
            dev.get_profile("nonexistent_profile_v99")

    def test_certification_certified_for_dualshock_edge(self):
        """DualShock Edge profile yields PHCITier.CERTIFIED certification."""
        from device_profile import PHCITier  # type: ignore
        dev = VAPIDevice()
        dev.get_profile("sony_dualshock_edge_v1")
        cert = dev.certification()
        self.assertIsNotNone(cert)
        self.assertEqual(cert.phci_tier, PHCITier.CERTIFIED)

    def test_is_phci_certified_true_for_edge(self):
        """is_phci_certified() returns True for DualShock Edge."""
        dev = VAPIDevice()
        dev.get_profile("sony_dualshock_edge_v1")
        self.assertTrue(dev.is_phci_certified())

    def test_is_phci_certified_false_for_hori(self):
        """is_phci_certified() returns False for HORI (PHCITier.NONE)."""
        dev = VAPIDevice()
        dev.get_profile("hori_fighting_commander_ps5_v1")
        self.assertFalse(dev.is_phci_certified())


# ---------------------------------------------------------------------------
# Group 3: VAPIVerifier
# ---------------------------------------------------------------------------

class TestVAPIVerifier(unittest.TestCase):

    def test_verify_record_valid(self):
        """verify_record returns True for a syntactically valid 228-byte record."""
        v = VAPIVerifier()
        self.assertTrue(v.verify_record(_make_raw_record()))

    def test_verify_record_wrong_size(self):
        """verify_record returns False for wrong-size input."""
        v = VAPIVerifier()
        self.assertFalse(v.verify_record(b"\x00" * 100))
        self.assertFalse(v.verify_record(b""))

    def test_verify_chain_ordered(self):
        """verify_chain returns True for a valid 3-record chain (prev_hash = SHA-256 of 164B body)."""
        v  = VAPIVerifier()
        r1 = _make_raw_record(ctr=1, prev_hash=b"\x00" * 32)
        r2 = _make_raw_record(ctr=2, prev_hash=hashlib.sha256(r1[:POAC_BODY_SIZE]).digest())
        r3 = _make_raw_record(ctr=3, prev_hash=hashlib.sha256(r2[:POAC_BODY_SIZE]).digest())
        self.assertTrue(v.verify_chain([r1, r2, r3]))

    def test_verify_chain_broken(self):
        """verify_chain returns False when a link is broken."""
        v  = VAPIVerifier()
        r1 = _make_raw_record(ctr=1)
        r2 = _make_raw_record(ctr=2, prev_hash=b"\xFF" * 32)  # wrong prev hash
        self.assertFalse(v.verify_chain([r1, r2]))


# ---------------------------------------------------------------------------
# Group 4: VAPISession
# ---------------------------------------------------------------------------

class TestVAPISession(unittest.TestCase):

    def test_ingest_record_fires_cheat_callback(self):
        """on_cheat_detected fires for a record with a cheat inference code."""
        session = VAPISession()
        detected = []
        session.on_cheat_detected(lambda r: detected.append(r.inference_result))
        session.ingest_record(_make_raw_record(inference=0x28))  # DRIVER_INJECT
        self.assertEqual(detected, [0x28])

    def test_ingest_record_no_callback_for_clean(self):
        """on_cheat_detected does NOT fire for a clean (NOMINAL) record."""
        session = VAPISession()
        detected = []
        session.on_cheat_detected(lambda r: detected.append(r))
        session.ingest_record(_make_raw_record(inference=0x20))
        self.assertEqual(detected, [])

    def test_chain_integrity_after_ingest(self):
        """chain_integrity() is True for a properly linked sequence (prev_hash = SHA-256 of 164B body)."""
        session = VAPISession()
        r1 = _make_raw_record(ctr=1, prev_hash=b"\x00" * 32)
        r2 = _make_raw_record(ctr=2, prev_hash=hashlib.sha256(r1[:POAC_BODY_SIZE]).digest())
        session.ingest_record(r1)
        session.ingest_record(r2)
        self.assertTrue(session.chain_integrity())

    def test_summary_counts(self):
        """summary() correctly counts clean, cheat, and advisory records."""
        session = VAPISession()
        session.ingest_record(_make_raw_record(inference=0x20))  # clean
        session.ingest_record(_make_raw_record(inference=0x28))  # cheat
        session.ingest_record(_make_raw_record(inference=0x2B))  # advisory
        summary = session.summary()
        self.assertEqual(summary["clean_records"],    1)
        self.assertEqual(summary["cheat_detections"], 1)
        self.assertEqual(summary["advisory_records"], 1)
        self.assertEqual(summary["total_records"],    3)

    def test_async_context_manager(self):
        """VAPISession works as an async context manager."""
        async def _run():
            async with VAPISession("sony_dualshock_edge_v1") as s:
                s.ingest_record(_make_raw_record())
                return s.summary()
        result = asyncio.run(_run())
        self.assertEqual(result["total_records"], 1)


# ---------------------------------------------------------------------------
# Group 5: SDK Self-Verification (the novel feature)
# ---------------------------------------------------------------------------

class TestSDKSelfVerify(unittest.TestCase):

    def setUp(self):
        self.session = VAPISession()
        self.attestation = self.session.self_verify()

    def test_returns_sdk_attestation(self):
        """self_verify() returns an SDKAttestation with correct sdk_version."""
        self.assertIsInstance(self.attestation, SDKAttestation)
        self.assertEqual(self.attestation.sdk_version, SDK_VERSION)
        self.assertEqual(len(self.attestation.attestation_hash), 32)

    def test_l5_temporal_layer_active(self):
        """L5 temporal oracle is active and scores >= 0.5 (detects synthetic bot)."""
        self.assertTrue(
            self.attestation.layers_active.get("L5_temporal", False),
            "L5 TemporalRhythmOracle must be importable from controller/",
        )
        self.assertGreaterEqual(
            self.attestation.pitl_scores.get("L5_temporal", 0.0), 0.5,
            "L5 must detect the synthetic 100ms constant-interval bot session",
        )

    def test_attestation_hash_determinism_with_new_call(self):
        """Two self_verify() calls produce different hashes (timestamp differs)."""
        att2 = self.session.self_verify()
        # Timestamps differ → hashes differ
        self.assertNotEqual(
            self.attestation.attestation_hash,
            att2.attestation_hash,
        )

    def test_all_five_layers_present(self):
        """self_verify() reports on exactly the 5 PITL layer keys."""
        expected_keys = {
            "L2_hid_xinput", "L3_behavioral", "L4_biometric",
            "L5_temporal", "L2B_imu_press",
        }
        self.assertEqual(set(self.attestation.layers_active.keys()), expected_keys)

    def test_l2b_layer_in_self_verify(self):
        """L2B key present; score in [0.0, 1.0] regardless of import success."""
        self.assertIn("L2B_imu_press", self.attestation.layers_active)
        score = self.attestation.pitl_scores.get("L2B_imu_press", -1.0)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


# ---------------------------------------------------------------------------
# Group 6: VAPIEnrollment
# ---------------------------------------------------------------------------

class TestVAPIEnrollment(unittest.TestCase):

    def test_get_status_no_bridge_returns_unavailable(self):
        enroll = VAPIEnrollment()   # bridge_url="" default
        status = enroll.get_status("a" * 64)
        self.assertEqual(status["status"], "unavailable")
        self.assertEqual(status["sessions_nominal"], 0)
        self.assertEqual(status["required_sessions"], 10)

    def test_is_tournament_eligible_credentialed_true(self):
        self.assertTrue(VAPIEnrollment.is_tournament_eligible({"status": "credentialed"}))

    def test_is_tournament_eligible_pending_false(self):
        self.assertFalse(VAPIEnrollment.is_tournament_eligible({"status": "pending"}))
        self.assertFalse(VAPIEnrollment.is_tournament_eligible({"status": "eligible"}))
        self.assertFalse(VAPIEnrollment.is_tournament_eligible({"status": "unavailable"}))

    def test_sessions_remaining_calculates_correctly(self):
        status = {"status": "pending", "sessions_nominal": 3, "required_sessions": 10}
        self.assertEqual(VAPIEnrollment.sessions_remaining(status), 7)
        self.assertEqual(VAPIEnrollment.sessions_remaining({"status": "credentialed"}), 0)
        self.assertEqual(VAPIEnrollment.sessions_remaining({"status": "eligible"}), 0)


# ---------------------------------------------------------------------------
# Group 7: VAPIZKProof
# ---------------------------------------------------------------------------

class TestVAPIZKProof(unittest.TestCase):

    def _valid(self):
        return {
            "proof_bytes": b"\x00" * 256, "feature_commitment": 99999,
            "humanity_prob_int": 750, "inference_code": 0x20,
            "nullifier_hash": 12345, "epoch": 100,
        }

    def test_validate_proof_dict_valid(self):
        ok, err = VAPIZKProof(self._valid()).validate()
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_validate_proof_dict_missing_key_fails(self):
        d = self._valid()
        del d["nullifier_hash"]
        ok, err = VAPIZKProof(d).validate()
        self.assertFalse(ok)
        self.assertIn("nullifier_hash", err)

    def test_public_inputs_returns_five_signals_in_order(self):
        d = self._valid()
        inputs = VAPIZKProof(d).public_inputs()
        self.assertEqual(len(inputs), VAPIZKProof.N_PUBLIC)
        self.assertEqual(inputs[0], d["feature_commitment"])
        self.assertEqual(inputs[1], d["humanity_prob_int"])
        self.assertEqual(inputs[2], d["inference_code"])
        self.assertEqual(inputs[3], d["nullifier_hash"])
        self.assertEqual(inputs[4], d["epoch"])


if __name__ == "__main__":
    unittest.main()
