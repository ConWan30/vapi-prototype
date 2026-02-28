"""
Comprehensive tests for the VAPI bridge PoAC record codec.

Tests cover parsing, serialization, signature verification, chain linkage,
device ID computation, and edge cases for the 228-byte wire format.
"""

import hashlib
import os
import struct

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes

from vapi_bridge.codec import (
    ACTION_NAMES,
    INFERENCE_NAMES,
    POAC_BODY_SIZE,
    POAC_HASH_SIZE,
    POAC_RECORD_SIZE,
    POAC_SIG_SIZE,
    PoACRecord,
    compute_device_id,
    parse_record,
    verify_chain_link,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Helper: build a raw 228-byte PoAC record from field values
# ---------------------------------------------------------------------------
def build_raw_record(
    prev_hash=b"\x00" * 32,
    sensor_commit=b"\x00" * 32,
    model_manifest=b"\x00" * 32,
    world_model=b"\x00" * 32,
    inference=0x20,
    action=0x01,
    confidence=200,
    battery=75,
    counter=1,
    timestamp_ms=1700000000000,
    lat=40.7128,
    lon=-74.0060,
    bounty_id=0,
    signature=None,
):
    """
    Construct a raw 228-byte PoAC record from individual field values.

    If ``signature`` is None a random 64-byte blob is used (the record will
    not be verifiable against any known key, but it will be structurally
    valid).
    """
    body = b""
    body += prev_hash
    body += sensor_commit
    body += model_manifest
    body += world_model
    body += struct.pack(
        ">BBBBIqddI",
        inference,
        action,
        confidence,
        battery,
        counter,
        timestamp_ms,
        lat,
        lon,
        bounty_id,
    )
    assert len(body) == 164, f"Body length is {len(body)}, expected 164"
    if signature is None:
        signature = os.urandom(64)
    return body + signature


def _sign_body(body: bytes, private_key: ec.EllipticCurvePrivateKey) -> bytes:
    """
    Sign a 164-byte body with an ECDSA-P256 key and return 64-byte raw r||s.
    """
    der_sig = private_key.sign(body, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_sig)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _generate_keypair():
    """Return (private_key, pubkey_bytes_65) for SECP256R1."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pubkey_bytes = private_key.public_key().public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint,
    )
    return private_key, pubkey_bytes


# ===================================================================
# 1. test_constants
# ===================================================================
class TestConstants:
    def test_hash_size(self):
        assert POAC_HASH_SIZE == 32

    def test_sig_size(self):
        assert POAC_SIG_SIZE == 64

    def test_body_size(self):
        assert POAC_BODY_SIZE == 164

    def test_record_size(self):
        assert POAC_RECORD_SIZE == 228

    def test_record_size_is_body_plus_sig(self):
        assert POAC_RECORD_SIZE == POAC_BODY_SIZE + POAC_SIG_SIZE


# ===================================================================
# 2. test_parse_record_valid
# ===================================================================
class TestParseRecordValid:
    def test_all_fields_match(self):
        prev_hash = os.urandom(32)
        sensor = os.urandom(32)
        manifest = os.urandom(32)
        world = os.urandom(32)
        inference = 0x02
        action = 0x05
        confidence = 180
        battery = 42
        counter = 9999
        ts = 1700000000000
        lat = 51.5074
        lon = -0.1278
        bounty = 12345
        sig = os.urandom(64)

        raw = build_raw_record(
            prev_hash=prev_hash,
            sensor_commit=sensor,
            model_manifest=manifest,
            world_model=world,
            inference=inference,
            action=action,
            confidence=confidence,
            battery=battery,
            counter=counter,
            timestamp_ms=ts,
            lat=lat,
            lon=lon,
            bounty_id=bounty,
            signature=sig,
        )

        rec = parse_record(raw)

        assert rec.prev_poac_hash == prev_hash
        assert rec.sensor_commitment == sensor
        assert rec.model_manifest_hash == manifest
        assert rec.world_model_hash == world
        assert rec.inference_result == inference
        assert rec.action_code == action
        assert rec.confidence == confidence
        assert rec.battery_pct == battery
        assert rec.monotonic_ctr == counter
        assert rec.timestamp_ms == ts
        assert rec.latitude == pytest.approx(lat)
        assert rec.longitude == pytest.approx(lon)
        assert rec.bounty_id == bounty
        assert rec.signature == sig


# ===================================================================
# 3. test_parse_record_wrong_size
# ===================================================================
class TestParseRecordWrongSize:
    def test_too_short(self):
        with pytest.raises(ValueError, match="Invalid record size"):
            parse_record(b"\x00" * 100)

    def test_too_long(self):
        with pytest.raises(ValueError, match="Invalid record size"):
            parse_record(b"\x00" * 300)

    def test_empty(self):
        with pytest.raises(ValueError, match="Invalid record size"):
            parse_record(b"")

    def test_one_byte_short(self):
        with pytest.raises(ValueError, match="Invalid record size"):
            parse_record(b"\x00" * 227)

    def test_one_byte_long(self):
        with pytest.raises(ValueError, match="Invalid record size"):
            parse_record(b"\x00" * 229)


# ===================================================================
# 4. test_parse_record_zero_body
# ===================================================================
class TestParseRecordZeroBody:
    def test_zero_body_parses(self):
        sig = os.urandom(64)
        raw = b"\x00" * 164 + sig
        rec = parse_record(raw)

        assert rec.prev_poac_hash == b"\x00" * 32
        assert rec.sensor_commitment == b"\x00" * 32
        assert rec.model_manifest_hash == b"\x00" * 32
        assert rec.world_model_hash == b"\x00" * 32
        assert rec.inference_result == 0
        assert rec.action_code == 0
        assert rec.confidence == 0
        assert rec.battery_pct == 0
        assert rec.monotonic_ctr == 0
        assert rec.timestamp_ms == 0
        assert rec.latitude == 0.0
        assert rec.longitude == 0.0
        assert rec.bounty_id == 0
        assert rec.signature == sig


# ===================================================================
# 5. test_record_hash_is_sha256
# ===================================================================
class TestRecordHash:
    def test_hash_matches_sha256_of_body(self):
        raw = build_raw_record()
        rec = parse_record(raw)
        expected_hash = hashlib.sha256(raw[:164]).digest()
        assert rec.record_hash == expected_hash

    def test_hash_is_32_bytes(self):
        raw = build_raw_record()
        rec = parse_record(raw)
        assert len(rec.record_hash) == 32

    def test_hash_hex_matches(self):
        raw = build_raw_record()
        rec = parse_record(raw)
        expected_hex = hashlib.sha256(raw[:164]).hexdigest()
        assert rec.record_hash_hex == expected_hex


# ===================================================================
# 6. test_raw_body_preserved
# ===================================================================
class TestRawBodyPreserved:
    def test_raw_body_equals_first_164_bytes(self):
        raw = build_raw_record()
        rec = parse_record(raw)
        assert rec.raw_body == raw[:164]

    def test_raw_body_length(self):
        raw = build_raw_record()
        rec = parse_record(raw)
        assert len(rec.raw_body) == 164

    def test_raw_body_unchanged_across_different_records(self):
        for _ in range(5):
            raw = build_raw_record(
                prev_hash=os.urandom(32),
                sensor_commit=os.urandom(32),
                counter=int.from_bytes(os.urandom(4), "big"),
            )
            rec = parse_record(raw)
            assert rec.raw_body == raw[:164]


# ===================================================================
# 7. test_lat_lon_fixed_point
# ===================================================================
class TestLatLonFixedPoint:
    def test_new_york(self):
        raw = build_raw_record(lat=40.7128, lon=-74.0060)
        rec = parse_record(raw)
        assert rec.lat_fixed == 407128000
        assert rec.lon_fixed == -740060000

    def test_zero_coordinates(self):
        raw = build_raw_record(lat=0.0, lon=0.0)
        rec = parse_record(raw)
        assert rec.lat_fixed == 0
        assert rec.lon_fixed == 0

    def test_southern_hemisphere(self):
        raw = build_raw_record(lat=-33.8688, lon=151.2093)
        rec = parse_record(raw)
        assert rec.lat_fixed == -338688000
        assert rec.lon_fixed == 1512093000

    def test_extreme_coordinates(self):
        raw = build_raw_record(lat=90.0, lon=180.0)
        rec = parse_record(raw)
        assert rec.lat_fixed == 900000000
        assert rec.lon_fixed == 1800000000

    def test_negative_extreme(self):
        raw = build_raw_record(lat=-90.0, lon=-180.0)
        rec = parse_record(raw)
        assert rec.lat_fixed == -900000000
        assert rec.lon_fixed == -1800000000


# ===================================================================
# 8. test_action_name_mapping
# ===================================================================
class TestActionNameMapping:
    @pytest.mark.parametrize(
        "code, expected_name",
        [
            (0x00, "NONE"),
            (0x01, "REPORT"),
            (0x02, "ALERT"),
            (0x03, "BOUNTY_ACCEPT"),
            (0x04, "BOUNTY_DECLINE"),
            (0x05, "BOUNTY_CLAIM"),
            (0x06, "PSM_ENTER"),
            (0x07, "PSM_EXIT"),
            (0x08, "MODEL_UPDATE"),
            (0x09, "BOOT"),
            (0x0A, "SWARM_SYNC"),
        ],
    )
    def test_known_action_code(self, code, expected_name):
        raw = build_raw_record(action=code)
        rec = parse_record(raw)
        assert rec.action_name == expected_name

    def test_unknown_action_code_hex_fallback(self):
        raw = build_raw_record(action=0xFF)
        rec = parse_record(raw)
        assert rec.action_name == "0xff"

    def test_action_names_dict_completeness(self):
        """ACTION_NAMES should have entries for codes 0x00 through 0x0A."""
        for code in range(0x0B):
            assert code in ACTION_NAMES


# ===================================================================
# 9. test_inference_name_mapping
# ===================================================================
class TestInferenceNameMapping:
    @pytest.mark.parametrize(
        "code, expected_name",
        [
            (0x00, "NOMINAL"),
            (0x01, "ANOMALY_LOW"),
            (0x02, "ANOMALY_HIGH"),
            (0x10, "STATIONARY"),
            (0x11, "WALKING"),
            (0x12, "VEHICLE"),
            (0x13, "FALL"),
        ],
    )
    def test_known_inference_code(self, code, expected_name):
        raw = build_raw_record(inference=code)
        rec = parse_record(raw)
        assert rec.inference_name == expected_name

    def test_unknown_inference_code_hex_fallback(self):
        raw = build_raw_record(inference=0xAB)
        rec = parse_record(raw)
        assert rec.inference_name == "0xab"


# ===================================================================
# 10. test_to_dict
# ===================================================================
class TestToDict:
    def test_all_keys_present(self):
        raw = build_raw_record()
        rec = parse_record(raw)
        d = rec.to_dict()

        expected_keys = {
            "record_hash",
            "device_id",
            "prev_hash",
            "sensor_commitment",
            "model_manifest",
            "world_model",
            "inference",
            "inference_code",
            "action",
            "action_code",
            "confidence",
            "battery_pct",
            "counter",
            "timestamp_ms",
            "latitude",
            "longitude",
            "bounty_id",
        }
        assert set(d.keys()) == expected_keys

    def test_values_match_record(self):
        prev_hash = os.urandom(32)
        raw = build_raw_record(
            prev_hash=prev_hash,
            inference=0x01,
            action=0x02,
            confidence=180,
            battery=42,
            counter=9999,
            timestamp_ms=1700000000000,
            lat=40.7128,
            lon=-74.0060,
            bounty_id=12345,
        )
        rec = parse_record(raw)
        d = rec.to_dict()

        assert d["prev_hash"] == prev_hash.hex()
        assert d["inference"] == "ANOMALY_LOW"
        assert d["inference_code"] == 0x01
        assert d["action"] == "ALERT"
        assert d["action_code"] == 0x02
        assert d["confidence"] == 180
        assert d["battery_pct"] == 42
        assert d["counter"] == 9999
        assert d["timestamp_ms"] == 1700000000000
        assert d["latitude"] == pytest.approx(40.7128)
        assert d["longitude"] == pytest.approx(-74.0060)
        assert d["bounty_id"] == 12345

    def test_record_hash_hex_in_dict(self):
        raw = build_raw_record()
        rec = parse_record(raw)
        d = rec.to_dict()
        assert d["record_hash"] == hashlib.sha256(raw[:164]).hexdigest()

    def test_to_dict_is_json_serializable(self):
        import json

        raw = build_raw_record()
        rec = parse_record(raw)
        d = rec.to_dict()
        # Should not raise
        json_str = json.dumps(d)
        assert isinstance(json_str, str)


# ===================================================================
# 11. test_sign_and_verify
# ===================================================================
class TestSignAndVerify:
    def test_valid_signature_returns_true(self):
        private_key, pubkey_bytes = _generate_keypair()

        body = build_raw_record()[:164]
        sig = _sign_body(body, private_key)
        raw = body + sig

        rec = parse_record(raw)
        assert verify_signature(rec, pubkey_bytes) is True

    def test_different_keypair_fails(self):
        private_key, _ = _generate_keypair()
        _, other_pubkey = _generate_keypair()

        body = build_raw_record()[:164]
        sig = _sign_body(body, private_key)
        raw = body + sig

        rec = parse_record(raw)
        assert verify_signature(rec, other_pubkey) is False

    def test_invalid_pubkey_format_raises(self):
        raw = build_raw_record()
        rec = parse_record(raw)

        with pytest.raises(ValueError, match="Invalid public key format"):
            verify_signature(rec, b"\x00" * 64)  # Wrong length

        with pytest.raises(ValueError, match="Invalid public key format"):
            verify_signature(rec, b"\x00" * 65)  # Wrong prefix (should be 0x04)

    def test_multiple_signatures_verify(self):
        """Sign multiple different bodies with the same key, all should verify."""
        private_key, pubkey_bytes = _generate_keypair()

        for i in range(5):
            body = build_raw_record(counter=i, timestamp_ms=1700000000000 + i * 30000)[:164]
            sig = _sign_body(body, private_key)
            raw = body + sig
            rec = parse_record(raw)
            assert verify_signature(rec, pubkey_bytes) is True


# ===================================================================
# 12. test_verify_bad_signature
# ===================================================================
class TestVerifyBadSignature:
    def test_flipped_bit_in_signature(self):
        private_key, pubkey_bytes = _generate_keypair()

        body = build_raw_record()[:164]
        sig = _sign_body(body, private_key)

        # Flip one bit in the first byte of the signature
        bad_sig = bytes([sig[0] ^ 0x01]) + sig[1:]
        raw = body + bad_sig

        rec = parse_record(raw)
        assert verify_signature(rec, pubkey_bytes) is False

    def test_zeroed_signature(self):
        private_key, pubkey_bytes = _generate_keypair()

        body = build_raw_record()[:164]
        raw = body + b"\x00" * 64

        rec = parse_record(raw)
        assert verify_signature(rec, pubkey_bytes) is False

    def test_random_signature(self):
        private_key, pubkey_bytes = _generate_keypair()

        body = build_raw_record()[:164]
        raw = body + os.urandom(64)

        rec = parse_record(raw)
        assert verify_signature(rec, pubkey_bytes) is False

    def test_tampered_body(self):
        """Sign the original body but tamper with one byte before parsing."""
        private_key, pubkey_bytes = _generate_keypair()

        body = build_raw_record()[:164]
        sig = _sign_body(body, private_key)

        # Tamper with the body (flip a bit in the confidence byte at offset 0x82)
        tampered = bytearray(body)
        tampered[0x82] ^= 0x01
        raw = bytes(tampered) + sig

        rec = parse_record(raw)
        assert verify_signature(rec, pubkey_bytes) is False


# ===================================================================
# 13. test_compute_device_id
# ===================================================================
class TestComputeDeviceId:
    def test_device_id_is_keccak256(self):
        _, pubkey_bytes = _generate_keypair()
        device_id = compute_device_id(pubkey_bytes)

        from eth_hash.auto import keccak

        expected = keccak(pubkey_bytes)
        assert device_id == expected

    def test_device_id_length(self):
        _, pubkey_bytes = _generate_keypair()
        device_id = compute_device_id(pubkey_bytes)
        assert len(device_id) == 32

    def test_deterministic(self):
        _, pubkey_bytes = _generate_keypair()
        id1 = compute_device_id(pubkey_bytes)
        id2 = compute_device_id(pubkey_bytes)
        assert id1 == id2

    def test_different_keys_different_ids(self):
        _, pub1 = _generate_keypair()
        _, pub2 = _generate_keypair()
        assert compute_device_id(pub1) != compute_device_id(pub2)


# ===================================================================
# 14. test_chain_linkage
# ===================================================================
class TestChainLinkage:
    def test_valid_chain_link(self):
        # Build record 1
        raw1 = build_raw_record(counter=1)
        rec1 = parse_record(raw1)

        # Build record 2 with prev_hash = SHA-256(rec1.raw_body)
        prev_hash_2 = hashlib.sha256(rec1.raw_body).digest()
        raw2 = build_raw_record(prev_hash=prev_hash_2, counter=2)
        rec2 = parse_record(raw2)

        assert verify_chain_link(rec1, rec2) is True

    def test_chain_of_three(self):
        raw1 = build_raw_record(counter=1)
        rec1 = parse_record(raw1)

        prev_hash_2 = hashlib.sha256(rec1.raw_body).digest()
        raw2 = build_raw_record(prev_hash=prev_hash_2, counter=2)
        rec2 = parse_record(raw2)

        prev_hash_3 = hashlib.sha256(rec2.raw_body).digest()
        raw3 = build_raw_record(prev_hash=prev_hash_3, counter=3)
        rec3 = parse_record(raw3)

        assert verify_chain_link(rec1, rec2) is True
        assert verify_chain_link(rec2, rec3) is True


# ===================================================================
# 15. test_chain_linkage_broken
# ===================================================================
class TestChainLinkageBroken:
    def test_wrong_prev_hash(self):
        raw1 = build_raw_record(counter=1)
        rec1 = parse_record(raw1)

        # Use a random (wrong) prev_hash
        raw2 = build_raw_record(prev_hash=os.urandom(32), counter=2)
        rec2 = parse_record(raw2)

        assert verify_chain_link(rec1, rec2) is False

    def test_swapped_order(self):
        """If we verify chain_link(rec2, rec1) it should be False."""
        raw1 = build_raw_record(counter=1)
        rec1 = parse_record(raw1)

        prev_hash_2 = hashlib.sha256(rec1.raw_body).digest()
        raw2 = build_raw_record(prev_hash=prev_hash_2, counter=2)
        rec2 = parse_record(raw2)

        # Forward link is valid
        assert verify_chain_link(rec1, rec2) is True
        # Reverse should be invalid
        assert verify_chain_link(rec2, rec1) is False

    def test_zero_prev_hash_no_match(self):
        raw1 = build_raw_record(counter=1, prev_hash=os.urandom(32))
        rec1 = parse_record(raw1)

        raw2 = build_raw_record(prev_hash=b"\x00" * 32, counter=2)
        rec2 = parse_record(raw2)

        assert verify_chain_link(rec1, rec2) is False


# ===================================================================
# 16. test_roundtrip_extreme_values
# ===================================================================
class TestRoundtripExtremeValues:
    def test_max_uint32_counter(self):
        raw = build_raw_record(counter=0xFFFFFFFF)
        rec = parse_record(raw)
        assert rec.monotonic_ctr == 0xFFFFFFFF

    def test_max_uint32_bounty(self):
        raw = build_raw_record(bounty_id=0xFFFFFFFF)
        rec = parse_record(raw)
        assert rec.bounty_id == 0xFFFFFFFF

    def test_max_int64_timestamp(self):
        max_i64 = (2**63) - 1
        raw = build_raw_record(timestamp_ms=max_i64)
        rec = parse_record(raw)
        assert rec.timestamp_ms == max_i64

    def test_min_int64_timestamp(self):
        min_i64 = -(2**63)
        raw = build_raw_record(timestamp_ms=min_i64)
        rec = parse_record(raw)
        assert rec.timestamp_ms == min_i64

    def test_max_uint8_fields(self):
        raw = build_raw_record(inference=0xFF, action=0xFF, confidence=0xFF, battery=0xFF)
        rec = parse_record(raw)
        assert rec.inference_result == 0xFF
        assert rec.action_code == 0xFF
        assert rec.confidence == 0xFF
        assert rec.battery_pct == 0xFF

    def test_zero_uint8_fields(self):
        raw = build_raw_record(inference=0, action=0, confidence=0, battery=0)
        rec = parse_record(raw)
        assert rec.inference_result == 0
        assert rec.action_code == 0
        assert rec.confidence == 0
        assert rec.battery_pct == 0

    def test_extreme_latitude(self):
        """IEEE 754 extreme value for latitude."""
        import sys

        raw = build_raw_record(lat=sys.float_info.max, lon=-sys.float_info.max)
        rec = parse_record(raw)
        assert rec.latitude == pytest.approx(sys.float_info.max)
        assert rec.longitude == pytest.approx(-sys.float_info.max)

    def test_negative_zero_float(self):
        raw = build_raw_record(lat=-0.0, lon=-0.0)
        rec = parse_record(raw)
        # -0.0 == 0.0 in Python
        assert rec.latitude == 0.0
        assert rec.longitude == 0.0

    def test_subnormal_float(self):
        """Test with very small subnormal floats."""
        import sys

        tiny = sys.float_info.min * sys.float_info.epsilon
        raw = build_raw_record(lat=tiny, lon=-tiny)
        rec = parse_record(raw)
        assert rec.latitude == pytest.approx(tiny)
        assert rec.longitude == pytest.approx(-tiny)

    def test_all_0xff_hashes(self):
        raw = build_raw_record(
            prev_hash=b"\xff" * 32,
            sensor_commit=b"\xff" * 32,
            model_manifest=b"\xff" * 32,
            world_model=b"\xff" * 32,
        )
        rec = parse_record(raw)
        assert rec.prev_poac_hash == b"\xff" * 32
        assert rec.sensor_commitment == b"\xff" * 32
        assert rec.model_manifest_hash == b"\xff" * 32
        assert rec.world_model_hash == b"\xff" * 32

    def test_zero_timestamp(self):
        raw = build_raw_record(timestamp_ms=0)
        rec = parse_record(raw)
        assert rec.timestamp_ms == 0

    def test_counter_zero(self):
        raw = build_raw_record(counter=0)
        rec = parse_record(raw)
        assert rec.monotonic_ctr == 0
