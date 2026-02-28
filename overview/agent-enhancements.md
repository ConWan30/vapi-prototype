# VAPI Phase 13 — Agent Capability Enhancements

## Overview

Phase 13 introduces four enhancements that make the VAPI agent irreplaceable by
enriching the three underutilized fields already present in the immutable 228-byte
PoAC body: `sensor_commitment`, `model_manifest_hash`, and `world_model_hash`.
These enhancements do not exist in any current anti-cheat, coaching, or DePIN system.

**Wire format invariant:** The 228-byte PoAC body is IMMUTABLE. All enhancements
operate within existing fields by changing what is *hashed into* them — not the
format itself. All 525 pre-Phase-13 tests continue to pass.

---

## Enhancement 1 — Multi-Modal Biometric Fusion (Reflexive Layer)

**File:** `controller/tinyml_biometric_fusion.py`
**PoAC field enriched:** `sensor_commitment` (32B) and `inference_result` (1B)
**Layer:** Reflexive (30-second cycle, frame-level)

### What it is

A biometric anomaly detector that extracts 7 kinematic/haptic signals unique to the
DualShock Edge adaptive trigger — signals that software cannot inject or reproduce:

| Signal | Description |
|--------|-------------|
| `trigger_resistance_change_rate` | L2/R2 mechanical mode transitions per 100 frames |
| `trigger_onset_velocity_l2/r2` | Frames from trigger=0 to peak, normalized |
| `micro_tremor_accel_variance` | Accelerometer variance during gyro-still periods |
| `grip_asymmetry` | L2/(R2+eps) ratio during simultaneous dual press |
| `stick_autocorr_lag1` | Stick velocity autocorrelation at lag-1 (fine motor signature) |
| `stick_autocorr_lag5` | Stick velocity autocorrelation at lag-5 (coarser motor signature) |

A rolling fingerprint (EMA of mean + variance over the last N sessions) is maintained
per device. Mahalanobis distance from the fingerprint triggers inference code
`0x30: BIOMETRIC_ANOMALY` when `d > 3.0` AND `confidence > 180/255`.

**Sensor commitment expansion:** The schema v2 sensor_commitment input grows from
48 bytes to 56 bytes by appending `biometric_distance (float32) + trigger_mode_hash (uint32)`.
The output remains a 32-byte SHA-256 — no wire format change.

**Model manifest hash:** Updated to pin the biometric model version:
`SHA-256(b"biometric_fusion_v1.0_adaptive_trigger")`.

### Why it is novel

No anti-cheat, coaching, or DePIN system currently uses adaptive trigger resistance
dynamics as a biometric signal. The haptic motor state is accessible only to the
physical controller — it cannot be replicated by a driver-level injection or macro tool.

### Inference code design

`0x30: BIOMETRIC_ANOMALY` is intentionally **outside** the `[0x28, 0x2A]` cheat range
used by TeamProofAggregator. It is a *soft anomaly signal* — the player's kinematic
profile changed (session fatigue, different player, hardware swap). It does not
trigger a rating penalty in SkillOracle and does not block team proofs.

---

## Enhancement 2 — Privacy-Preserving Personalized Optimizer (Deliberative Layer)

**File:** `controller/knapsack_personalized.py`
**PoAC field enriched:** `world_model_hash` (32B, via E2+E4 synergy)
**Layer:** Deliberative (5-minute cycle)

### What it is

A personalized bounty optimizer that extends the greedy knapsack logic from
`firmware/src/economic.c` with:

- **5-dimensional preference model** — per-device weights for `[reward_magnitude,
  sensor_match, zone_proximity, duration_fit, tier_weight]`, learned via SGD
  from observed bounty outcomes.
- **Differential privacy** — Laplace noise with `ε=1.5` (sensitivity=1.0 in
  normalized feature space) is added to utility scores before ranking. A daily
  budget of 50.0ε prevents unbounded inference from bounty selection patterns.
- **Preemption** — if a new candidate has utility > 1.5× the worst currently
  selected bounty, it displaces it (matches firmware preemption threshold).

### Why it is novel

No DePIN economic protocol implements per-device differential privacy in its
optimizer. The DP guarantee means an adversary observing which bounties a device
accepts cannot infer the device's preference weights beyond the noise floor — the
player's economic profile is private even if their selection history is public.

### E2+E4 Synergy

The preference model's 5 float64 weights (40 bytes) are contributed to the
`world_model_hash` field:

```python
world_model_hash = SHA-256(ewc_model.serialize_weights() || preference_model.serialize_weights())
```

This single 32-byte hash fingerprints both *how* the player plays (EWC cognitive
state) and *what* they value economically (preference weights). No existing
Web3 protocol has a player-owned cryptographic fingerprint of this richness.

---

## Enhancement 4 — Self-Evolving World Model (Cross-Layer)

**File:** `controller/world_model_continual.py`
**PoAC field enriched:** `world_model_hash` (32B)
**Layer:** Cross-layer (updated after each session, consulted by all layers)

### What it is

A small 3-layer MLP (30→64→32→8) trained via **Elastic Weight Consolidation (EWC)**
— the canonical algorithm for preventing catastrophic forgetting in neural networks
(Kirkpatrick et al., 2017). Implemented in pure NumPy (no PyTorch dependency).

The network encodes the player's current cognitive state as an 8-dimensional
embedding. It trains on 30-dimensional session feature vectors (mean of all
FeatureFrame.to_vector() observations in the session).

EWC adds a penalty for changing weights that were important for past sessions
(measured by diagonal Fisher information). This prevents the model from forgetting
early skills while learning new ones — the model *grows*, not *drifts*.

**The SHA-256 of the weight vector IS the `world_model_hash`:**

```python
world_model_hash = SHA-256(W1 || b1 || W2 || b2 || W3 || b3)
```

The PoAC chain is therefore also a *cognitive evolution proof* — every record's
`world_model_hash` reflects the player's cumulative learned state at that moment.

### Why it is novel

No existing Web3 gaming or coaching protocol has player-owned, verifiable cognitive
state. The world_model_hash is:
- **Unforgeable** — requires the actual training history to reproduce
- **Portable** — JSON save/load with hex-encoded weights
- **Backward compatible** — `from_legacy_world_model()` migrates old EMA-based
  WorldModel format without data loss

### Quantifying progress with WORLD_MODEL_EVOLUTION

`compute_world_model_improvement_bps(baseline_hash, current_hash)` returns the
normalized Hamming weight of the XOR of two world_model_hashes in basis points
(0–10000). This is the `improvementBps` value stored in ProgressAttestation when
`metricType = WORLD_MODEL_EVOLUTION (4)`:

- **0 BPS**: identical weights (no cognitive change)
- **5000 BPS**: 50% bit divergence (substantial evolution)
- **10000 BPS**: all bits differ (complete transformation)

---

## Enhancement 3 — ZK Swarm Consensus (Strategic Layer — Design + Mock)

**Files:**
- `contracts/contracts/TeamProofAggregatorZK.sol` (Solidity ZK gate)
- `contracts/contracts/TeamProofAggregatorZKTestable.sol` (test harness)
- `bridge/swarm_zk_aggregator.py` (Python mock + interface)

**PoAC field enriched:** None directly — operates on aggregated PoAC records
**Layer:** Strategic (1-hour cycle, team-level aggregation)

### What it is

A ZK-ready team attestation system that gates `submitTeamProof` on a cryptographic
zero-knowledge proof. Phase 13 delivers:

1. **Solidity interface** — `TeamProofAggregatorZK.submitTeamProofZK()` with:
   - Virtual `_verifyZKProof()` (Phase 13: accepts 256-byte mock; Phase 14: Groth16)
   - Nullifier registry preventing ZK proof replay
   - Full cheat detection inherited from `TeamProofAggregator`

2. **Python mock** — `SwarmZKAggregator` generates/verifies structured 256-byte proofs
   and computes Merkle roots matching the on-chain algorithm

3. **Circuit specification** (Phase 14 target — Circom 2.0, Groth16 + BN254):
   - Public: `merkleRoot`, `nullifierHash`, `memberCount`
   - Private: `deviceIds[]`, `recordHashes[]`, `inferenceResults[]`, `identityCommitments[]`
   - Constraints: no cheat codes, valid Merkle root, Poseidon identity commitments
   - Trusted setup: Hermez perpetual powers-of-tau

### Why it is novel

This is the first Solidity contract that gates team proof submission on a ZK proof
interface with nullifier-based double-spend prevention. No esports integrity system
has this architecture. Phase 14 drops in the real Groth16 verifier (`_verifyZKProof`
override) without changing the contract interface, the Python bridge, or any tests.

---

## Synergy Matrix

|  | E1: Biometric | E4: World Model | E2: Personalized Opt | E3: ZK Swarm |
|--|--|--|--|--|
| **E1: Biometric** | — | E1 7-feature vectors become part of the 30-dim session input to the EWC model | E2 optimizer excludes bounties where sensors show active biometric anomaly (0x30) | E3 ZK circuit enforces inference NOT in 0x28–0x30 range for all members |
| **E4: World Model** | E4 hash encodes session history including E1 biometric features | — | E4 + E2 preference weights combined: `world_model_hash = SHA-256(ewc_weights + pref_weights)` | E3 aggregate proves all members' cognitive states (E4 hashes) evolved consistently |
| **E2: Personalized Opt** | E2 deprioritizes bounties where E1 flags ongoing anomaly | E2 preference weights contribute to `world_model_hash` (E4 synergy) | — | E3 team records are the ones E2 selected under DP constraints |
| **E3: ZK Swarm** | E3 circuit validates E1 inference codes (no 0x28–0x30 in proof) | E3 aggregate proves team cognitive consistency (all E4 hashes consistent) | E3 team proofs aggregate E2-optimized records | — |

---

## PoAC Field Utilization Summary

| Field | Before Phase 13 | After Phase 13 |
|-------|----------------|----------------|
| `sensor_commitment` (32B) | SHA-256 of 48-byte schema v2 payload | SHA-256 of 56-byte payload: +biometric_distance (f32) + trigger_mode_hash (u32) |
| `model_manifest_hash` (32B) | Static `SHA-256(b"heuristic_fallback_v0")` | `SHA-256(b"biometric_fusion_v1.0_adaptive_trigger")` — version-pinned |
| `world_model_hash` (32B) | SHA-256 of 4 EMA baseline values (~32B) | SHA-256 of EWC-MLP weight vector (~6KB) + optional preference weights (40B) |
| `inference_result` (1B) | Codes 0x20–0x2A defined | `0x30: BIOMETRIC_ANOMALY` added (outside cheat range) |

---

## Phase 14 Roadmap (from Phase 13 foundations)

| Item | Phase 13 mock | Phase 14 reality |
|------|--------------|-----------------|
| ZK circuit | Circuit spec in docstring | `contracts/circuits/TeamProof.circom` |
| ZK prover | 256-byte mock bytes | `bridge/zk_prover.py` (snarkjs / py_ecc) |
| Solidity verifier | `proof.length == 256` | `_verifyZKProof` override with BN254 pairing |
| Biometric model | Heuristic thresholds | Real training on labeled session data |
| EWC world model | Simulation-trained | Player-specific training from real sessions |
