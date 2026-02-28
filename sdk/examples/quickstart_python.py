"""
VAPI SDK — Python Quickstart
One-afternoon integration for a Python game server or anti-cheat backend.

Demonstrates:
1. Parse and validate a 228-byte PoAC record
2. Ingest a session with cheat-detection callbacks
3. Verify chain integrity
4. Run SDK self-verification (produces SDKAttestation)
5. Submit a batch to the VAPI bridge via REST

Run from the project root:
    python sdk/examples/quickstart_python.py
"""

import asyncio
import hashlib
import struct
import sys
from pathlib import Path

# Make vapi_sdk importable from the sdk/ directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vapi_sdk import (
    SDK_VERSION,
    VAPIDevice,
    VAPIRecord,
    VAPISession,
    VAPIVerifier,
)


# ---------------------------------------------------------------------------
# 1. Synthesize a fake 228-byte PoAC record (normally from your controller)
# ---------------------------------------------------------------------------

def make_demo_record(inference: int = 0x20, prev_hash: bytes = b"\x00" * 32) -> bytes:
    """Build a syntactically valid 228-byte PoAC record for demo purposes."""
    hashes = prev_hash + b"\xAB" * 32 + b"\xCD" * 32 + b"\xEF" * 32  # 128B hashes
    packed  = struct.pack(">BBBBI", inference, 0x01, 220, 95, 1)       # infer+action+conf+bat+ctr
    packed += struct.pack(">Q", 1_700_000_000_000)                      # timestamp_ms
    packed += struct.pack(">ddI", 40.7128, -74.0060, 0)                # lat, lon, bounty_id
    body = hashes + packed                                              # 164B body
    sig  = b"\x00" * 64                                                # placeholder sig
    return body + sig                                                   # 228B total


# ---------------------------------------------------------------------------
# 2. Parse a record
# ---------------------------------------------------------------------------

def demo_parse():
    print("\n=== 1. Parse a PoAC record ===")
    raw = make_demo_record(inference=0x20)
    rec = VAPIRecord(raw)

    print(f"  inference    : {rec.inference_name} (0x{rec.inference_result:02X})")
    print(f"  confidence   : {rec.confidence}")
    print(f"  battery_pct  : {rec.battery_pct}%")
    print(f"  is_clean     : {rec.is_clean}")
    print(f"  record_hash  : {rec.record_hash.hex()[:16]}...")
    print(f"  chain_hash   : {rec.chain_hash.hex()[:16]}...")


# ---------------------------------------------------------------------------
# 3. Session with cheat-detection callback
# ---------------------------------------------------------------------------

def demo_session():
    print("\n=== 2. Session with cheat-detection callback ===")

    session = VAPISession()
    detections = []

    session.on_cheat_detected(lambda r: detections.append(r.inference_name))

    # Ingest 3 records: clean, cheat, advisory
    r1_raw = make_demo_record(inference=0x20)                            # NOMINAL
    r2_raw = make_demo_record(inference=0x28)                            # DRIVER_INJECT
    r3_raw = make_demo_record(inference=0x2B)                            # TEMPORAL_ANOMALY

    session.ingest_record(r1_raw)
    session.ingest_record(r2_raw)
    session.ingest_record(r3_raw)

    summary = session.summary()
    print(f"  total_records    : {summary['total_records']}")
    print(f"  clean_records    : {summary['clean_records']}")
    print(f"  cheat_detections : {summary['cheat_detections']}")
    print(f"  advisory_records : {summary['advisory_records']}")
    print(f"  cheat callback fired for: {detections}")


# ---------------------------------------------------------------------------
# 4. Chain integrity verification
# ---------------------------------------------------------------------------

def demo_chain_integrity():
    print("\n=== 3. Chain integrity ===")

    # Build a properly linked 3-record chain
    r1_raw = make_demo_record(inference=0x20, prev_hash=b"\x00" * 32)
    r2_raw = make_demo_record(inference=0x20, prev_hash=hashlib.sha256(r1_raw).digest())
    r3_raw = make_demo_record(inference=0x20, prev_hash=hashlib.sha256(r2_raw).digest())

    verifier = VAPIVerifier()
    intact = verifier.verify_chain([r1_raw, r2_raw, r3_raw])
    print(f"  3-record chain intact: {intact}")

    # Tamper the second record's prev_hash
    r2_broken = make_demo_record(inference=0x20, prev_hash=b"\xFF" * 32)
    broken = verifier.verify_chain([r1_raw, r2_broken, r3_raw])
    print(f"  tampered chain intact: {broken}")


# ---------------------------------------------------------------------------
# 5. Device profile + PHCI certification
# ---------------------------------------------------------------------------

def demo_device():
    print("\n=== 4. Device profile + PHCI certification ===")
    dev = VAPIDevice()
    profile = dev.get_profile("sony_dualshock_edge_v1")
    print(f"  profile       : {profile.display_name}")
    print(f"  phci_tier     : {profile.phci_tier.name}")
    print(f"  pitl_layers   : {profile.pitl_layers}")
    cert = dev.certification()
    print(f"  cert score    : {cert.score}/100")
    print(f"  is_certified  : {dev.is_phci_certified()}")


# ---------------------------------------------------------------------------
# 6. SDK self-verification
# ---------------------------------------------------------------------------

def demo_self_verify():
    print("\n=== 5. SDK self-verification ===")
    session = VAPISession()
    attestation = session.self_verify()

    print(f"  sdk_version      : {attestation.sdk_version}")
    print(f"  layers_active    : {attestation.layers_active}")
    print(f"  pitl_scores      : {attestation.pitl_scores}")
    print(f"  all_layers_active: {attestation.all_layers_active}")
    print(f"  active_layers    : {attestation.active_layer_count}/4")
    print(f"  zk_available     : {attestation.zk_proof_available}")
    print(f"  attestation_hash : {attestation.attestation_hash.hex()[:32]}...")

    # The attestation_hash is your on-chain proof of correct SDK wiring.
    # Submit it alongside your PoAC records to prove the integration is live.


# ---------------------------------------------------------------------------
# 7. Async context manager (clean open/close for game session lifecycle)
# ---------------------------------------------------------------------------

async def demo_async():
    print("\n=== 6. Async context manager ===")
    async with VAPISession("sony_dualshock_edge_v1") as session:
        session.ingest_record(make_demo_record())
        summary = session.summary()
    print(f"  session closed cleanly | total_records={summary['total_records']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"VAPI SDK {SDK_VERSION} — Python Quickstart")
    print("=" * 50)

    demo_parse()
    demo_session()
    demo_chain_integrity()
    demo_device()
    demo_self_verify()
    asyncio.run(demo_async())

    print("\nAll demos complete.")
