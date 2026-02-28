# VAPI SDK Integration Guide
**One-afternoon integration for game studios, hardware partners, and platform developers**

Version: `1.0.0-phase20` | SDK: `sdk/vapi_sdk.py` | C header: `sdk/vapi.h` | REST: `sdk/openapi.yaml`

---

## Overview

The VAPI SDK is the licensing surface for the Verified Autonomous Physical Intelligence protocol. It gives game studios, controller manufacturers (SCUF, Battle Beaver, HORI), and platform developers programmatic access to:

- **PoAC record parsing** — zero-copy 228-byte wire format
- **PITL cheat detection** — Layers L2–L5 with typed inference codes
- **PHCI device certification** — DualShock Edge, SCUF Reflex Pro, and 3 other profiles
- **Chain integrity** — client-side PoAC chain verification (no RPC call)
- **SDK self-verification** — the novel feature: `VAPISession.self_verify()` proves the integration is correctly wired using VAPI's own PITL stack

---

## Quick Start (Python)

```bash
# From the project root
pip install -r bridge/requirements.txt   # already satisfied if bridge is installed
python sdk/examples/quickstart_python.py
```

```python
import sys; sys.path.insert(0, "sdk")
from vapi_sdk import VAPISession

session = VAPISession()
session.on_cheat_detected(lambda r: print(f"Cheat: {r.inference_name}"))

# Ingest raw 228-byte records from your controller bridge
session.ingest_record(raw_bytes)

# Self-verify: proves all 4 PITL layers are active and wired
att = session.self_verify()
print(att.layers_active)        # {'L2_hid_xinput': True, 'L3_behavioral': True, ...}
print(att.attestation_hash.hex()[:32])  # SHA-256 commitment for on-chain proof
```

---

## SDK Classes

### `VAPIRecord` — 228-byte PoAC Wire Parser

```python
rec = VAPIRecord(raw_bytes)   # raises ValueError if not exactly 228 bytes
```

| Property | Type | Description |
|---|---|---|
| `inference_result` | `int` | Raw inference byte (see Inference Codes) |
| `inference_name` | `str` | Human name (`"NOMINAL"`, `"DRIVER_INJECT"`, …) |
| `confidence` | `int` | 0–255; ≥220 = high confidence |
| `battery_pct` | `int` | 0–100 |
| `monotonic_ctr` | `int` | Device counter (never resets) |
| `timestamp_ms` | `int` | Unix epoch milliseconds |
| `is_clean` | `bool` | True only for NOMINAL (0x20) |
| `is_advisory` | `bool` | True for TEMPORAL_ANOMALY/BIOMETRIC_ANOMALY |
| `record_hash` | `bytes` | SHA-256(raw[:164]) — used for on-chain lookup |
| `chain_hash` | `bytes` | SHA-256(raw[:228]) — used as prev_hash for next record |

```python
rec.verify_chain_link(prev)   # None = genesis check; prev = continuation check
```

**Genesis:** `prev_poac_hash` must be `\x00 * 32`.
**Continuation:** `prev_poac_hash` must equal `prev.chain_hash`.

---

### `SDKAttestation` — Self-Verification Result

Returned by `VAPISession.self_verify()`. Contains a cryptographic commitment you can submit on-chain as proof the integration is correctly wired.

```python
att = session.self_verify()

att.layers_active       # {'L2_hid_xinput': bool, 'L3_behavioral': bool, ...}
att.pitl_scores         # {'L2_hid_xinput': float, ...}  0.0–1.0
att.all_layers_active   # bool — True if all 4 layers imported and functional
att.active_layer_count  # int — 0–4
att.zk_proof_available  # bool — True if ZK trusted setup has been run
att.sdk_version         # str — "1.0.0-phase20"
att.verified_at         # float — Unix epoch (nanosecond precision float)
att.attestation_hash    # bytes[32] — SHA-256 commitment

att.to_dict()           # JSON-serializable dict for REST submission
```

**How attestation_hash is computed:**
```
SHA-256(
  repr(sorted(layers_active.items())) +
  repr(sorted(pitl_scores.items())) +
  SDK_VERSION +
  struct.pack(">Q", time.time_ns())
)
```
Two calls in rapid succession produce different hashes (nanosecond timestamps differ).

---

### `VAPIDevice` — Profile + PHCI Certification

```python
dev = VAPIDevice()
profile = dev.get_profile("sony_dualshock_edge_v1")   # raises KeyError if unknown

profile.profile_id                    # "sony_dualshock_edge_v1"
profile.phci_tier                     # PHCITier.CERTIFIED
profile.pitl_layers                   # (2, 3, 4, 5)
profile.sensor_commitment_size_bytes  # 56 (CERTIFIED) or 48 (STANDARD)

dev.certification()        # PHCICertification(score=100, passed_checks=[...])
dev.is_phci_certified()    # bool — True for CERTIFIED and STANDARD tiers
```

**Registered profile IDs:**
| Profile ID | Device | Tier | Layers |
|---|---|---|---|
| `sony_dualshock_edge_v1` | DualShock Edge (CFI-ZCP1) | CERTIFIED | L2–L5 |
| `sony_generic_dualsense_v1` | DualSense (standard) | STANDARD | L2–L3 |
| `scuf_reflex_pro_v1` | SCUF Reflex Pro | STANDARD | L2–L3 |
| `battle_beaver_dualshock_edge_v1` | Battle Beaver Edge mod | CERTIFIED | L2–L5 |
| `hori_fighting_commander_ps5_v1` | HORI Fighting Commander | NONE | L2 |

**Note:** Battle Beaver uses the same USB VID/PID as DualShock Edge. Auto-detection defaults to DualShock Edge — set `DEVICE_PROFILE_ID=battle_beaver_dualshock_edge_v1` explicitly.

---

### `VAPIVerifier` — Local + On-Chain Verification

```python
v = VAPIVerifier()

v.verify_record(raw_bytes)             # bool — syntactic 228B + size check
v.verify_chain([r1_raw, r2_raw, ...])  # bool — full chain integrity, no RPC
```

---

### `VAPISession` — Primary Integration Interface

```python
session = VAPISession("sony_dualshock_edge_v1")  # profile_id optional

# Callbacks
session.on_cheat_detected(lambda r: ...)
session.on_record_submitted(lambda r, tx_hash: ...)

# Ingest
rec = session.ingest_record(raw_bytes)

# Chain integrity (client-side, no RPC)
session.chain_integrity()  # bool

# Summary
session.summary()  # {'total_records': N, 'clean_records': N, 'cheat_detections': N, ...}

# Self-verify (the novel feature)
att = session.self_verify()

# Async context manager
async with VAPISession("sony_dualshock_edge_v1") as session:
    session.ingest_record(raw_bytes)
    att = session.self_verify()
```

---

## Inference Codes

| Code | Name | PITL Layer | Type | Action |
|---|---|---|---|---|
| `0x20` | `NOMINAL` | — | Clean | Normal play — submit to SkillOracle |
| `0x28` | `DRIVER_INJECT` | L2 | **Hard Cheat** | Immediate flag; blocks BountyMarket claim |
| `0x29` | `WALLHACK_PREAIM` | L3 | **Hard Cheat** | Immediate flag; blocks BountyMarket claim |
| `0x2A` | `AIMBOT_BEHAVIORAL` | L3 | **Hard Cheat** | Immediate flag; blocks BountyMarket claim |
| `0x2B` | `TEMPORAL_ANOMALY` | L5 | Advisory | Flag for review; does not block chain |
| `0x30` | `BIOMETRIC_ANOMALY` | L4 | Advisory | Flag for review; does not block chain |

Hard cheat codes `[0x28, 0x2A]` are the `CHEAT_CODES` range. Advisory codes (`0x2B`, `0x30`) are outside this range — they do not block `BountyMarket.submitEvidence` or `TeamProofAggregator`.

---

## PHCI Certification Requirements

**CERTIFIED tier** (score 100): DualShock Edge, Battle Beaver Edge
- has_adaptive_triggers, has_gyroscope, has_accelerometer
- pitl_layers includes L4 and L5
- sensor_commitment_size_bytes == 56
- schema_version == 2

**STANDARD tier** (score ~62): DualSense, SCUF Reflex Pro
- has_gyroscope, has_accelerometer
- pitl_layers includes L2 and L3
- sensor_commitment_size_bytes == 48

**NONE tier** (score ~25): HORI Fighting Commander
- No gyro, no accel, no analog sticks
- Only L2 layer

---

## SDK Self-Verification (Novel Feature)

`VAPISession.self_verify()` is the defining feature of the VAPI SDK. It uses VAPI's own PITL stack to attest that each layer is correctly imported and functional — no other gaming SDK does this.

**What it does:**
1. **L2 probe** — `import HidXInputOracle` from the bridge. Layer active if import succeeds.
2. **L3 probe** — `import BackendCheatClassifier`. Score 1.0 if classifier is functional.
3. **L4 probe** — `import BiometricFusionClassifier`. Score 1.0 if classifier functional.
4. **L5 probe** — Imports `TemporalRhythmOracle`, then injects 25 synthetic input frames at 100ms constant inter-press intervals (the exact signature of a software bot). Score 1.0 if `TEMPORAL_ANOMALY` fires correctly, 0.5 if the oracle loads but misses.

**No hardware required.** Works in CI, headless Docker, GitHub Actions.

**The attestation_hash** is a SHA-256 commitment over all layer states, scores, SDK version, and a nanosecond timestamp. Submit it on-chain alongside PoAC records as cryptographic proof of correct SDK wiring.

```python
att = session.self_verify()
assert att.all_layers_active, "PITL not fully wired — check bridge installation"
assert att.pitl_scores["L5_temporal"] >= 0.5, "L5 temporal oracle failed synthetic bot test"

# Submit attestation hash on-chain (via chain.py or REST)
# This is the on-chain proof that your integration is live and correctly wired
```

---

## C Header Integration (Sony/SCUF Firmware)

Include `sdk/vapi.h` in your embedded project.

```c
#include "vapi.h"

// Implement SHA-256 using your platform's library:
// mbedTLS: mbedtls_sha256(data, len, out, 0)
// OpenSSL: SHA256(data, len, out)
static void my_sha256(const uint8_t *data, size_t len, uint8_t out[32]) {
    mbedtls_sha256(data, len, out, 0);
}

// Parse a 228-byte record
vapi_record_t rec;
int rc = vapi_record_parse(raw_bytes, 228, my_sha256, &rec);
assert(rc == VAPI_OK);

printf("inference: %s (0x%02X)\n",
       vapi_inference_name(rec.inference_result),
       rec.inference_result);
printf("is_cheat: %d\n", vapi_record_is_cheat(&rec));

// Chain link verification
bool ok = vapi_chain_verify_link(&current_rec, &previous_rec);

// Session
vapi_session_t *session = vapi_session_create("sony_dualshock_edge_v1", my_sha256);
vapi_session_on_cheat_detected(session, my_cheat_callback, NULL);
vapi_session_ingest(session, raw_bytes, 228);
vapi_session_destroy(session);
```

---

## REST API Integration (Unity, Unreal, Web)

Full OpenAPI 3.0 spec: `sdk/openapi.yaml`

**Base URL:** `https://api.vapi.gg/v1`

Key endpoints:

```
POST /sessions                              Create session, get session_id
POST /records/batch                         Submit 1–64 PoAC records
POST /sessions/{id}/self-verify             Run SDK self-verification
GET  /devices/{profile_id}/certification    PHCI certification for a device
GET  /leaderboard/{device_id}               SkillOracle rating
POST /bounties/{id}/evidence                Submit bounty evidence
```

**Unity C# example:** `sdk/examples/unity_integration.cs` — attach `VAPIManager` to a persistent `GameObject`, call `IngestRecord(bytes)` each physics frame.

**Web/JS example:** `sdk/examples/web_integration.js` — ES2022 module, SubtleCrypto for SHA-256, zero npm dependencies for core parsing.

**Webhook server:** `sdk/examples/webhook_server.py` — FastAPI; receives `CheatDetected` events; HMAC-SHA256 signature verification; escalation policy (flag → kick → ban).

---

## Wire Format Reference

```
228-byte PoAC record (big-endian, IMMUTABLE — never extend or alter):
─────────────────────────────────────────────────────────────────────
[  0.. 31]  prev_poac_hash      SHA-256 of previous record (genesis = 0x00*32)
[ 32.. 63]  sensor_commitment   SHA-256 of kinematic/haptic sensor frame (48B or 56B)
[ 64.. 95]  model_manifest_hash SHA-256 of TinyML model binary
[ 96..127]  world_model_hash    SHA-256 of EWC world model weights
[128]       inference_result    uint8 (see Inference Codes above)
[129]       action_code         uint8
[130]       confidence          uint8 (0–255; ≥220 = high confidence)
[131]       battery_pct         uint8 (0–100)
[132..135]  monotonic_ctr       uint32 big-endian (device counter, never resets)
[136..143]  timestamp_ms        uint64 big-endian (Unix epoch ms)
[144..151]  latitude            IEEE 754 double big-endian
[152..159]  longitude           IEEE 754 double big-endian
[160..163]  bounty_id           uint32 big-endian (0 = no active bounty)
[164..227]  signature           ECDSA-P256 raw r‖s over SHA-256(body[0..163])
─────────────────────────────────────────────────────────────────────
record_hash = SHA-256(raw[0..163])   ← used for on-chain lookups
chain_hash  = SHA-256(raw[0..227])   ← used as prev_poac_hash in next record
```

**These two hashes are different.** Never use `chain_hash` as the record identifier.

---

## Deployment Checklist

- [ ] `session.self_verify()` returns `all_layers_active = True`
- [ ] `att.pitl_scores["L5_temporal"] >= 0.5` — temporal bot detection functional
- [ ] Device profile resolves to correct PHCI tier for your hardware
- [ ] Cheat callback wired to game-server kick/flag endpoint
- [ ] Records submitting with `schema_version=2` (DualShock kinematic)
- [ ] Bridge monitoring at `/monitor/health` returns `status: ok`
- [ ] Webhook HMAC secret set and verified in staging before production
- [ ] Attestation hash persisted for on-chain proof of SDK wiring date

---

## Versioning

The SDK version `1.0.0-phase20` follows `MAJOR.MINOR.PATCH-phase` convention.
`SDK_VERSION` constant in `sdk/vapi_sdk.py` is included in every `SDKAttestation`.
Protocol upgrades (new PoAC fields) increment MAJOR and require re-certification.
