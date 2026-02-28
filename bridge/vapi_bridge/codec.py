"""
PoAC Record Codec — Parse, serialize, and verify 228-byte PoAC records.

Wire format (big-endian, matching firmware poac_serialize()):
  Offset  Field                    Size   Format
  0x00    prev_poac_hash           32B    raw bytes
  0x20    sensor_commitment        32B    raw bytes
  0x40    model_manifest_hash      32B    raw bytes
  0x60    world_model_hash         32B    raw bytes
  0x80    inference_result         1B     uint8
  0x81    action_code              1B     uint8
  0x82    confidence               1B     uint8
  0x83    battery_pct              1B     uint8
  0x84    monotonic_ctr            4B     uint32 big-endian
  0x88    timestamp_ms             8B     int64 big-endian
  0x90    latitude                 8B     IEEE 754 double big-endian
  0x98    longitude                8B     IEEE 754 double big-endian
  0xA0    bounty_id                4B     uint32 big-endian
  ---     body total               164B
  0xA4    signature (r || s)       64B    raw bytes
  ---     record total             228B

Note: poac.h declares POAC_RECORD_SIZE=202 as "approximate". The actual
packed struct with 64-byte raw ECDSA-P256 signatures is 228 bytes.
The 164-byte body (before signature) is what gets signed.
"""

import hashlib
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes

# Wire format constants
POAC_HASH_SIZE = 32
POAC_SIG_SIZE = 64
POAC_BODY_SIZE = 164  # Everything before signature
POAC_RECORD_SIZE = POAC_BODY_SIZE + POAC_SIG_SIZE  # 228 bytes

# Struct format for the non-hash, non-sig fields (after 4x32B hashes)
# inference(B) + action(B) + confidence(B) + battery(B) + counter(>I)
# + timestamp(>q) + lat(>d) + lon(>d) + bounty_id(>I)
_FIELDS_FMT = ">BBBBIqddI"
_FIELDS_SIZE = struct.calcsize(_FIELDS_FMT)  # 1+1+1+1+4+8+8+8+4 = 36

# Action code names for display
ACTION_NAMES = {
    0x00: "NONE",
    0x01: "REPORT",
    0x02: "ALERT",
    0x03: "BOUNTY_ACCEPT",
    0x04: "BOUNTY_DECLINE",
    0x05: "BOUNTY_CLAIM",
    0x06: "PSM_ENTER",
    0x07: "PSM_EXIT",
    0x08: "MODEL_UPDATE",
    0x09: "BOOT",
    0x0A: "SWARM_SYNC",
}

INFERENCE_NAMES = {
    0x00: "NOMINAL",
    0x01: "ANOMALY_LOW",
    0x02: "ANOMALY_HIGH",
    0x10: "STATIONARY",
    0x11: "WALKING",
    0x12: "VEHICLE",
    0x13: "FALL",
}


@dataclass
class PoACRecord:
    """Parsed PoAC record with all fields accessible."""

    prev_poac_hash: bytes       # 32B
    sensor_commitment: bytes    # 32B
    model_manifest_hash: bytes  # 32B
    world_model_hash: bytes     # 32B
    inference_result: int       # uint8
    action_code: int            # uint8
    confidence: int             # uint8
    battery_pct: int            # uint8
    monotonic_ctr: int          # uint32
    timestamp_ms: int           # int64
    latitude: float             # IEEE 754 double
    longitude: float            # IEEE 754 double
    bounty_id: int              # uint32
    signature: bytes            # 64B (r || s)

    # Derived fields (set after parsing)
    record_hash: bytes = b""    # SHA-256 of serialized body
    device_id: bytes = b""      # keccak256(pubkey), set after verification
    raw_body: bytes = b""       # Original 164-byte serialized body
    schema_version: int = 0     # Sensor commitment schema: 0=unknown, 1=v1 env, 2=v2 kinematic

    # PITL extension — NOT part of 228B wire format; populated by DualShock integration
    pitl_l4_distance:        Optional[float] = field(default=None)  # Mahalanobis distance
    pitl_l4_warmed_up:       Optional[bool]  = field(default=None)  # BiometricFusionClassifier.is_warmed_up()
    pitl_l4_features_json:   Optional[str]   = field(default=None)  # JSON of 7 BiometricFeatureFrame floats
    pitl_l5_cv:              Optional[float] = field(default=None)  # Coefficient of variation
    pitl_l5_entropy_bits:    Optional[float] = field(default=None)  # Shannon entropy
    pitl_l5_quant_score:     Optional[float] = field(default=None)  # 60Hz quantization score
    pitl_l5_anomaly_signals: Optional[int]   = field(default=None)  # Signals fired 0-3
    # Phase 25: Agent Intelligence sidecar fields
    pitl_l5_rhythm_humanity: Optional[float] = field(default=None)  # L5 positive humanity score [0,1]
    pitl_l4_drift_velocity:  Optional[float] = field(default=None)  # L4 stable vs candidate EMA drift
    pitl_e4_cognitive_drift: Optional[float] = field(default=None)  # E4 cross-session embedding delta
    pitl_humanity_prob:      Optional[float] = field(default=None)  # Bayesian fusion humanity_probability

    @property
    def record_hash_hex(self) -> str:
        return self.record_hash.hex()

    @property
    def device_id_hex(self) -> str:
        return self.device_id.hex()

    @property
    def action_name(self) -> str:
        return ACTION_NAMES.get(self.action_code, f"0x{self.action_code:02x}")

    @property
    def inference_name(self) -> str:
        return INFERENCE_NAMES.get(self.inference_result, f"0x{self.inference_result:02x}")

    @property
    def lat_fixed(self) -> int:
        """Latitude as fixed-point int64 (value * 1e7) for Solidity."""
        return int(self.latitude * 1e7)

    @property
    def lon_fixed(self) -> int:
        """Longitude as fixed-point int64 (value * 1e7) for Solidity."""
        return int(self.longitude * 1e7)

    @property
    def age_seconds(self) -> float:
        """How old this record is, in seconds."""
        return (time.time() * 1000 - self.timestamp_ms) / 1000.0

    def to_dict(self) -> dict:
        """JSON-serializable dict for API responses."""
        return {
            "record_hash": self.record_hash_hex,
            "device_id": self.device_id_hex,
            "prev_hash": self.prev_poac_hash.hex(),
            "sensor_commitment": self.sensor_commitment.hex(),
            "model_manifest": self.model_manifest_hash.hex(),
            "world_model": self.world_model_hash.hex(),
            "inference": self.inference_name,
            "inference_code": self.inference_result,
            "action": self.action_name,
            "action_code": self.action_code,
            "confidence": self.confidence,
            "battery_pct": self.battery_pct,
            "counter": self.monotonic_ctr,
            "timestamp_ms": self.timestamp_ms,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "bounty_id": self.bounty_id,
        }


def parse_record(data: bytes) -> PoACRecord:
    """
    Parse a raw 228-byte PoAC record from wire format.

    Raises ValueError if the data is the wrong size or malformed.
    """
    if len(data) != POAC_RECORD_SIZE:
        raise ValueError(
            f"Invalid record size: {len(data)} bytes (expected {POAC_RECORD_SIZE})"
        )

    offset = 0

    # 4 x 32-byte hashes
    prev_hash = data[offset : offset + POAC_HASH_SIZE]
    offset += POAC_HASH_SIZE
    sensor_commit = data[offset : offset + POAC_HASH_SIZE]
    offset += POAC_HASH_SIZE
    model_manifest = data[offset : offset + POAC_HASH_SIZE]
    offset += POAC_HASH_SIZE
    world_model = data[offset : offset + POAC_HASH_SIZE]
    offset += POAC_HASH_SIZE

    # Packed fields
    fields = struct.unpack_from(_FIELDS_FMT, data, offset)
    offset += _FIELDS_SIZE

    # Signature
    sig = data[offset : offset + POAC_SIG_SIZE]

    body = data[:POAC_BODY_SIZE]
    record_hash = hashlib.sha256(body).digest()

    return PoACRecord(
        prev_poac_hash=prev_hash,
        sensor_commitment=sensor_commit,
        model_manifest_hash=model_manifest,
        world_model_hash=world_model,
        inference_result=fields[0],
        action_code=fields[1],
        confidence=fields[2],
        battery_pct=fields[3],
        monotonic_ctr=fields[4],
        timestamp_ms=fields[5],
        latitude=fields[6],
        longitude=fields[7],
        bounty_id=fields[8],
        signature=sig,
        record_hash=record_hash,
        raw_body=body,
    )


def verify_signature(record: PoACRecord, pubkey_bytes: bytes) -> bool:
    """
    Verify the ECDSA-P256 signature on a PoAC record.

    Args:
        record: Parsed PoAC record.
        pubkey_bytes: 65-byte uncompressed SEC1 public key (0x04 || x || y).

    Returns:
        True if signature is valid, False otherwise.
    """
    if len(pubkey_bytes) != 65 or pubkey_bytes[0] != 0x04:
        raise ValueError(f"Invalid public key format (len={len(pubkey_bytes)})")

    # Reconstruct the EC public key
    pubkey = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pubkey_bytes)

    # The signature is raw r||s (32 + 32 bytes). Convert to DER for cryptography lib.
    r = int.from_bytes(record.signature[:32], "big")
    s = int.from_bytes(record.signature[32:], "big")
    der_sig = utils.encode_dss_signature(r, s)

    # The signed data is SHA-256 of the 164-byte body.
    # The firmware signs: ECDSA-P256(SHA-256(serialized_body)).
    try:
        pubkey.verify(der_sig, record.raw_body, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def compute_device_id(pubkey_bytes: bytes) -> bytes:
    """
    Compute device ID as keccak256(pubkey), matching DeviceRegistry.sol.

    Args:
        pubkey_bytes: 65-byte uncompressed SEC1 public key.

    Returns:
        32-byte device ID.
    """
    from eth_hash.auto import keccak

    return keccak(pubkey_bytes)


def verify_chain_link(prev_record: PoACRecord, record: PoACRecord) -> bool:
    """
    Verify that record.prev_poac_hash == SHA-256(serialize(prev_record body)).

    Returns True if the chain link is valid.
    """
    expected = hashlib.sha256(prev_record.raw_body).digest()
    return record.prev_poac_hash == expected
