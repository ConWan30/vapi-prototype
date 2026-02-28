"""
End-to-end integration test for the VAPI bridge PoAC codec pipeline.

This test simulates a realistic device lifecycle:
  1. Generates a P256 keypair (as the Pebble Tracker would at provisioning)
  2. Builds 5 chained PoAC records (each prev_hash = SHA-256 of previous body)
  3. Signs each record with the keypair
  4. Parses each record from wire format
  5. Verifies each ECDSA-P256 signature
  6. Verifies hash-chain linkage between consecutive records
  7. Verifies monotonic counter increment
  8. Verifies all record hashes match SHA-256 of their bodies
"""

import hashlib
import os
import struct
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from vapi_bridge.codec import (
    POAC_BODY_SIZE,
    POAC_RECORD_SIZE,
    PoACRecord,
    compute_device_id,
    parse_record,
    verify_chain_link,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _generate_keypair():
    """Generate a SECP256R1 (P-256) keypair.

    Returns:
        (private_key, pubkey_bytes): The private key object and 65-byte
        uncompressed SEC1 public key bytes (0x04 || x || y).
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    pubkey_bytes = private_key.public_key().public_bytes(
        encoding=Encoding.X962,
        format=PublicFormat.UncompressedPoint,
    )
    assert len(pubkey_bytes) == 65
    assert pubkey_bytes[0] == 0x04
    return private_key, pubkey_bytes


def _sign_body(body: bytes, private_key: ec.EllipticCurvePrivateKey) -> bytes:
    """Sign a 164-byte body and return raw 64-byte r||s signature."""
    assert len(body) == POAC_BODY_SIZE
    der_sig = private_key.sign(body, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_sig)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _build_body(
    prev_hash: bytes,
    counter: int,
    timestamp_ms: int,
    sensor_commit: bytes = None,
    model_manifest: bytes = None,
    world_model: bytes = None,
    inference: int = 0x00,
    action: int = 0x01,
    confidence: int = 200,
    battery: int = 75,
    lat: float = 40.7128,
    lon: float = -74.0060,
    bounty_id: int = 0,
) -> bytes:
    """Construct a 164-byte PoAC body from field values."""
    if sensor_commit is None:
        sensor_commit = os.urandom(32)
    if model_manifest is None:
        model_manifest = hashlib.sha256(b"tinyml-anomaly-v1.0").digest()
    if world_model is None:
        world_model = hashlib.sha256(b"world-model-snapshot").digest()

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
    assert len(body) == POAC_BODY_SIZE
    return body


# ---------------------------------------------------------------------------
# The end-to-end test
# ---------------------------------------------------------------------------
CHAIN_LENGTH = 5


class TestEndToEndChain:
    """
    Full pipeline test: keygen -> build chain -> sign -> parse -> verify.
    """

    @pytest.fixture(autouse=True)
    def setup_chain(self):
        """Build and parse a chain of 5 signed PoAC records."""
        self.private_key, self.pubkey_bytes = _generate_keypair()
        self.device_id = compute_device_id(self.pubkey_bytes)

        self.raw_records = []
        self.parsed_records = []

        # Genesis record: prev_hash is all zeros (no predecessor)
        prev_hash = b"\x00" * 32
        base_timestamp = 1700000000000  # Realistic epoch-ms

        for i in range(CHAIN_LENGTH):
            # Simulate sensor data, decreasing battery, different inferences
            inference_codes = [0x00, 0x10, 0x11, 0x01, 0x12]
            action_codes = [0x09, 0x01, 0x01, 0x02, 0x01]
            battery_levels = [100, 95, 88, 80, 73]

            body = _build_body(
                prev_hash=prev_hash,
                counter=i + 1,  # Monotonic counter starts at 1
                timestamp_ms=base_timestamp + i * 30000,  # 30s intervals
                inference=inference_codes[i],
                action=action_codes[i],
                confidence=180 + i * 5,
                battery=battery_levels[i],
                lat=40.7128 + i * 0.0001,
                lon=-74.0060 + i * 0.0001,
                bounty_id=0 if i < 3 else 42,
            )

            sig = _sign_body(body, self.private_key)
            raw = body + sig

            assert len(raw) == POAC_RECORD_SIZE

            self.raw_records.append(raw)

            rec = parse_record(raw)
            self.parsed_records.append(rec)

            # Next record's prev_hash = SHA-256 of this body
            prev_hash = hashlib.sha256(body).digest()

    # -------------------------------------------------------------------
    # Step 4: Verify parsing produced valid records
    # -------------------------------------------------------------------
    def test_chain_length(self):
        """We should have exactly CHAIN_LENGTH parsed records."""
        assert len(self.parsed_records) == CHAIN_LENGTH

    def test_all_records_parsed(self):
        """Every record should be a PoACRecord instance."""
        for rec in self.parsed_records:
            assert isinstance(rec, PoACRecord)

    # -------------------------------------------------------------------
    # Step 5: Verify each signature
    # -------------------------------------------------------------------
    def test_all_signatures_valid(self):
        """Every record's ECDSA-P256 signature must verify against our pubkey."""
        for i, rec in enumerate(self.parsed_records):
            result = verify_signature(rec, self.pubkey_bytes)
            assert result is True, f"Signature verification failed for record {i}"

    def test_signature_fails_with_wrong_key(self):
        """Signatures should NOT verify against a different keypair."""
        _, other_pubkey = _generate_keypair()
        for rec in self.parsed_records:
            assert verify_signature(rec, other_pubkey) is False

    # -------------------------------------------------------------------
    # Step 6: Verify chain linkage between consecutive records
    # -------------------------------------------------------------------
    def test_chain_linkage_all_consecutive(self):
        """Every consecutive pair should have valid hash-chain linkage."""
        for i in range(CHAIN_LENGTH - 1):
            prev_rec = self.parsed_records[i]
            curr_rec = self.parsed_records[i + 1]
            result = verify_chain_link(prev_rec, curr_rec)
            assert result is True, (
                f"Chain linkage broken between record {i} and {i + 1}"
            )

    def test_genesis_record_has_zero_prev_hash(self):
        """The first record's prev_poac_hash should be all zeros."""
        assert self.parsed_records[0].prev_poac_hash == b"\x00" * 32

    def test_non_consecutive_linkage_fails(self):
        """Records that are not consecutive should NOT chain-link."""
        if CHAIN_LENGTH >= 3:
            # Record 0 and Record 2 should NOT link directly
            assert verify_chain_link(self.parsed_records[0], self.parsed_records[2]) is False

    def test_reverse_linkage_fails(self):
        """Chain linkage should fail in the reverse direction."""
        for i in range(CHAIN_LENGTH - 1):
            result = verify_chain_link(self.parsed_records[i + 1], self.parsed_records[i])
            assert result is False, (
                f"Reverse chain linkage should fail between record {i + 1} and {i}"
            )

    # -------------------------------------------------------------------
    # Step 7: Verify monotonic counter increment
    # -------------------------------------------------------------------
    def test_monotonic_counter_strictly_increasing(self):
        """Monotonic counter should increment by 1 for each record."""
        for i in range(CHAIN_LENGTH):
            assert self.parsed_records[i].monotonic_ctr == i + 1

    def test_monotonic_counter_no_gaps(self):
        """No gaps between consecutive counter values."""
        for i in range(CHAIN_LENGTH - 1):
            curr = self.parsed_records[i].monotonic_ctr
            nxt = self.parsed_records[i + 1].monotonic_ctr
            assert nxt == curr + 1, (
                f"Counter gap: record {i} has {curr}, record {i + 1} has {nxt}"
            )

    # -------------------------------------------------------------------
    # Step 8: Verify all record hashes match SHA-256 of body
    # -------------------------------------------------------------------
    def test_record_hashes_match_sha256(self):
        """Each record_hash must be SHA-256 of the first 164 bytes."""
        for i, (raw, rec) in enumerate(
            zip(self.raw_records, self.parsed_records)
        ):
            expected = hashlib.sha256(raw[:POAC_BODY_SIZE]).digest()
            assert rec.record_hash == expected, (
                f"Record hash mismatch for record {i}"
            )

    def test_record_hash_is_32_bytes(self):
        for rec in self.parsed_records:
            assert len(rec.record_hash) == 32

    def test_raw_body_preserved(self):
        """raw_body should match the first 164 bytes of the wire record."""
        for i, (raw, rec) in enumerate(
            zip(self.raw_records, self.parsed_records)
        ):
            assert rec.raw_body == raw[:POAC_BODY_SIZE], (
                f"raw_body mismatch for record {i}"
            )

    # -------------------------------------------------------------------
    # Additional end-to-end integrity checks
    # -------------------------------------------------------------------
    def test_timestamps_increasing(self):
        """Timestamps should be strictly increasing (30s intervals)."""
        for i in range(CHAIN_LENGTH - 1):
            t1 = self.parsed_records[i].timestamp_ms
            t2 = self.parsed_records[i + 1].timestamp_ms
            assert t2 > t1, f"Timestamp not increasing: {t1} -> {t2}"

    def test_battery_decreasing(self):
        """Battery should decrease over the chain."""
        first_battery = self.parsed_records[0].battery_pct
        last_battery = self.parsed_records[-1].battery_pct
        assert last_battery < first_battery

    def test_device_id_consistent(self):
        """Device ID computed from the pubkey should be consistent."""
        id1 = compute_device_id(self.pubkey_bytes)
        id2 = compute_device_id(self.pubkey_bytes)
        assert id1 == id2
        assert len(id1) == 32

    def test_to_dict_all_records(self):
        """to_dict() should work on every record and return all expected keys."""
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
        for i, rec in enumerate(self.parsed_records):
            d = rec.to_dict()
            assert set(d.keys()) == expected_keys, (
                f"Missing keys in to_dict() for record {i}"
            )

    def test_chain_prev_hash_matches_prev_record_hash(self):
        """
        For record i (i > 0), prev_poac_hash should equal the SHA-256 of
        record (i-1)'s raw_body, which is also record (i-1)'s record_hash.
        """
        for i in range(1, CHAIN_LENGTH):
            assert self.parsed_records[i].prev_poac_hash == self.parsed_records[i - 1].record_hash

    def test_location_varies(self):
        """Latitude and longitude should vary slightly across records."""
        lats = [rec.latitude for rec in self.parsed_records]
        lons = [rec.longitude for rec in self.parsed_records]
        # All should be distinct (we added 0.0001 per record)
        assert len(set(lats)) == CHAIN_LENGTH
        assert len(set(lons)) == CHAIN_LENGTH

    def test_bounty_id_transition(self):
        """First 3 records have bounty_id=0, last 2 have bounty_id=42."""
        for i in range(3):
            assert self.parsed_records[i].bounty_id == 0
        for i in range(3, CHAIN_LENGTH):
            assert self.parsed_records[i].bounty_id == 42

    def test_full_pipeline_summary(self):
        """
        Summary assertion: the entire pipeline (keygen -> serialize ->
        sign -> parse -> verify_sig -> verify_chain -> verify_hash)
        produces a consistent, verifiable chain.
        """
        # This is a single assertion that ties everything together:
        # for each record, verify signature + hash + chain link
        for i, rec in enumerate(self.parsed_records):
            # Signature
            assert verify_signature(rec, self.pubkey_bytes) is True

            # Record hash
            expected_hash = hashlib.sha256(self.raw_records[i][:POAC_BODY_SIZE]).digest()
            assert rec.record_hash == expected_hash

            # Chain link (skip genesis)
            if i > 0:
                assert verify_chain_link(self.parsed_records[i - 1], rec) is True
