#!/usr/bin/env python3
"""
VAPI PoAC Inspector — CLI tool to decode, verify, and analyze PoAC records.

Reads PoAC records from binary files, serial port, or MQTT streams.
Verifies signatures (ECDSA-P256), validates chain integrity, and provides
human-readable output of the agent's cognitive audit trail.

Requirements:
    pip install cryptography paho-mqtt pyserial

Usage:
    # Decode a binary PoAC file
    python poac_inspector.py decode record.bin

    # Verify a chain of records
    python poac_inspector.py verify-chain records/

    # Monitor serial port for live PoAC output
    python poac_inspector.py monitor --port COM3 --baud 115200

    # Dump device public key for on-chain registration
    python poac_inspector.py pubkey record.bin
"""

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

try:
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("Warning: 'cryptography' not installed — signature verification disabled")
    print("Install with: pip install cryptography")

# ---------------------------------------------------------------------------
# PoAC Record Structure (matches firmware/include/poac.h)
# ---------------------------------------------------------------------------

POAC_HASH_SIZE = 32
POAC_SIG_SIZE = 64

# Serialization format (big-endian):
#   32B prev_hash + 32B sensor_commit + 32B model_manifest +
#   1B inference + 1B action + 1B confidence + 1B battery +
#   4B counter + 8B timestamp + 8B lat + 8B lon + 4B bounty_id +
#   64B signature
# Total: 196 bytes (serialized with signature)
# Signable payload: 132 bytes (without signature)

# Format: prev_hash(32) + sensor_commit(32) + model_manifest(32) + world_model(32)
#       + inference(1) + action(1) + confidence(1) + battery(1)
#       + counter(4) + timestamp(8) + lat(8) + lon(8) + bounty_id(4) = 164 bytes
POAC_SIGNABLE_FORMAT = ">32s32s32s32sBBBBIqdd I"
POAC_SIGNABLE_SIZE = struct.calcsize(POAC_SIGNABLE_FORMAT)  # 164 bytes
POAC_FULL_SIZE = POAC_SIGNABLE_SIZE + POAC_SIG_SIZE  # 228 bytes

# Action code names
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

# Inference result names
INFER_NAMES = {
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
    """Decoded PoAC record."""
    prev_poac_hash: bytes
    sensor_commitment: bytes
    model_manifest_hash: bytes
    world_model_hash: bytes
    inference_result: int
    action_code: int
    confidence: int
    battery_pct: int
    monotonic_ctr: int
    timestamp_ms: int
    latitude: float
    longitude: float
    bounty_id: int
    signature: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "PoACRecord":
        """Decode a PoAC record from raw bytes."""
        if len(data) < POAC_FULL_SIZE:
            raise ValueError(
                f"Data too short: {len(data)} bytes (need {POAC_FULL_SIZE})"
            )

        fields = struct.unpack(POAC_SIGNABLE_FORMAT, data[:POAC_SIGNABLE_SIZE])
        signature = data[POAC_SIGNABLE_SIZE:POAC_SIGNABLE_SIZE + POAC_SIG_SIZE]

        return cls(
            prev_poac_hash=fields[0],
            sensor_commitment=fields[1],
            model_manifest_hash=fields[2],
            world_model_hash=fields[3],
            inference_result=fields[4],
            action_code=fields[5],
            confidence=fields[6],
            battery_pct=fields[7],
            monotonic_ctr=fields[8],
            timestamp_ms=fields[9],
            latitude=fields[10],
            longitude=fields[11],
            bounty_id=fields[12],
            signature=signature,
        )

    def signable_bytes(self) -> bytes:
        """Get the bytes that are signed (everything except the signature)."""
        return struct.pack(
            POAC_SIGNABLE_FORMAT,
            self.prev_poac_hash,
            self.sensor_commitment,
            self.model_manifest_hash,
            self.world_model_hash,
            self.inference_result,
            self.action_code,
            self.confidence,
            self.battery_pct,
            self.monotonic_ctr,
            self.timestamp_ms,
            self.latitude,
            self.longitude,
            self.bounty_id,
        )

    def record_hash(self) -> bytes:
        """SHA-256 hash of the signable portion (used for chain linking)."""
        return hashlib.sha256(self.signable_bytes()).digest()

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "prev_poac_hash": self.prev_poac_hash.hex(),
            "sensor_commitment": self.sensor_commitment.hex(),
            "model_manifest_hash": self.model_manifest_hash.hex(),
            "world_model_hash": self.world_model_hash.hex(),
            "inference_result": INFER_NAMES.get(
                self.inference_result, f"0x{self.inference_result:02x}"
            ),
            "action_code": ACTION_NAMES.get(
                self.action_code, f"0x{self.action_code:02x}"
            ),
            "confidence": f"{self.confidence / 255.0:.2%}",
            "battery_pct": f"{self.battery_pct}%",
            "monotonic_ctr": self.monotonic_ctr,
            "timestamp_ms": self.timestamp_ms,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "bounty_id": self.bounty_id if self.bounty_id else None,
            "signature": self.signature.hex(),
            "record_hash": self.record_hash().hex(),
        }

    def pretty_print(self):
        """Print human-readable record."""
        d = self.to_dict()
        print(f"{'=' * 70}")
        print(f"  PoAC Record #{self.monotonic_ctr}")
        print(f"{'=' * 70}")
        print(f"  Chain Link:      {d['prev_poac_hash'][:16]}...")
        print(f"  Sensor Commit:   {d['sensor_commitment'][:16]}...")
        print(f"  Model Manifest:  {d['model_manifest_hash'][:16]}...")
        print(f"  World Model:     {d['world_model_hash'][:16]}...")
        print(f"  Inference:       {d['inference_result']}")
        print(f"  Action:          {d['action_code']}")
        print(f"  Confidence:      {d['confidence']}")
        print(f"  Battery:         {d['battery_pct']}")
        print(f"  Counter:         {self.monotonic_ctr}")
        print(f"  Timestamp:       {self.timestamp_ms}")
        print(f"  Location:        ({self.latitude:.6f}, {self.longitude:.6f})")
        if self.bounty_id:
            print(f"  Bounty:          #{self.bounty_id}")
        print(f"  Record Hash:     {d['record_hash'][:16]}...")
        print(f"  Signature:       {d['signature'][:16]}...")
        print()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_signature(record: PoACRecord, pubkey_bytes: bytes) -> bool:
    """Verify ECDSA-P256 signature of a PoAC record."""
    if not HAS_CRYPTO:
        print("  [SKIP] Signature verification requires 'cryptography' package")
        return True

    try:
        # Parse uncompressed SEC1 public key (0x04 || x || y)
        pubkey = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), pubkey_bytes
        )

        # Signature is r || s (32 bytes each)
        r = int.from_bytes(record.signature[:32], "big")
        s = int.from_bytes(record.signature[32:], "big")
        der_sig = utils.encode_dss_signature(r, s)

        # Verify over the signable bytes
        pubkey.verify(
            der_sig,
            record.signable_bytes(),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except Exception as e:
        print(f"  [FAIL] Signature verification failed: {e}")
        return False


def verify_chain(records: list[PoACRecord]) -> bool:
    """Verify chain integrity across a list of PoAC records."""
    if len(records) < 2:
        print("Need at least 2 records to verify chain")
        return True

    all_valid = True
    for i in range(1, len(records)):
        expected_hash = records[i - 1].record_hash()
        actual_hash = records[i].prev_poac_hash

        if expected_hash != actual_hash:
            print(f"  [BREAK] Chain broken at record #{records[i].monotonic_ctr}")
            print(f"    Expected prev_hash: {expected_hash.hex()[:16]}...")
            print(f"    Actual prev_hash:   {actual_hash.hex()[:16]}...")
            all_valid = False
        else:
            print(f"  [OK] Record #{records[i].monotonic_ctr} -> "
                  f"#{records[i - 1].monotonic_ctr} chain valid")

        # Monotonic counter check
        if records[i].monotonic_ctr <= records[i - 1].monotonic_ctr:
            print(f"  [WARN] Counter not strictly increasing at "
                  f"#{records[i].monotonic_ctr}")
            all_valid = False

    return all_valid


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_decode(args):
    """Decode and display a PoAC record from a binary file."""
    data = Path(args.file).read_bytes()

    # File may contain multiple records
    offset = 0
    count = 0
    while offset + POAC_FULL_SIZE <= len(data):
        record = PoACRecord.from_bytes(data[offset:])
        record.pretty_print()
        offset += POAC_FULL_SIZE
        count += 1

    print(f"Decoded {count} record(s) from {args.file}")

    if args.json:
        records = []
        offset = 0
        while offset + POAC_FULL_SIZE <= len(data):
            r = PoACRecord.from_bytes(data[offset:])
            records.append(r.to_dict())
            offset += POAC_FULL_SIZE
        print(json.dumps(records, indent=2))


def cmd_verify_chain(args):
    """Verify chain integrity across PoAC record files in a directory."""
    path = Path(args.directory)
    records = []

    for f in sorted(path.glob("*.bin")):
        data = f.read_bytes()
        offset = 0
        while offset + POAC_FULL_SIZE <= len(data):
            records.append(PoACRecord.from_bytes(data[offset:]))
            offset += POAC_FULL_SIZE

    # Sort by monotonic counter
    records.sort(key=lambda r: r.monotonic_ctr)

    print(f"Loaded {len(records)} records")
    print(f"Counter range: {records[0].monotonic_ctr} - {records[-1].monotonic_ctr}")
    print()

    valid = verify_chain(records)
    print()
    print(f"Chain integrity: {'VALID' if valid else 'BROKEN'}")


def cmd_monitor(args):
    """Monitor serial port for live PoAC records."""
    try:
        import serial
    except ImportError:
        print("Install pyserial: pip install pyserial")
        sys.exit(1)

    print(f"Monitoring {args.port} at {args.baud} baud...")
    print("Waiting for PoAC records (binary frames)...\n")

    ser = serial.Serial(args.port, args.baud, timeout=1)
    buf = bytearray()

    try:
        while True:
            chunk = ser.read(256)
            if chunk:
                buf.extend(chunk)

            # Try to extract complete records
            while len(buf) >= POAC_FULL_SIZE:
                try:
                    record = PoACRecord.from_bytes(bytes(buf[:POAC_FULL_SIZE]))
                    record.pretty_print()
                    buf = buf[POAC_FULL_SIZE:]
                except Exception:
                    # Not a valid record — skip one byte and retry
                    buf.pop(0)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")
    finally:
        ser.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="VAPI PoAC Inspector — Decode, verify, and analyze "
                    "Proof of Autonomous Cognition records",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # decode
    p_decode = subparsers.add_parser(
        "decode", help="Decode PoAC record(s) from a binary file"
    )
    p_decode.add_argument("file", help="Binary file containing PoAC record(s)")
    p_decode.add_argument("--json", action="store_true", help="Output as JSON")

    # verify-chain
    p_verify = subparsers.add_parser(
        "verify-chain", help="Verify chain integrity across PoAC records"
    )
    p_verify.add_argument(
        "directory", help="Directory containing .bin files with PoAC records"
    )

    # monitor
    p_monitor = subparsers.add_parser(
        "monitor", help="Monitor serial port for live PoAC records"
    )
    p_monitor.add_argument("--port", required=True, help="Serial port (e.g., COM3)")
    p_monitor.add_argument("--baud", type=int, default=115200, help="Baud rate")

    args = parser.parse_args()

    commands = {
        "decode": cmd_decode,
        "verify-chain": cmd_verify_chain,
        "monitor": cmd_monitor,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
