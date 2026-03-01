# Verified Autonomous Physical Intelligence: Cryptographic Proof of Human Gaming via Hardware-Rooted Controller Input Attestation

**Authors:** [Author A]$^{1}$, [Author B]$^{1}$, [Author C]$^{2}$

$^{1}$[Affiliation 1], $^{2}$[Affiliation 2]

**Contact:** {author.a, author.b}@affiliation1.edu, author.c@affiliation2.edu

---

## Abstract

No existing mechanism allows a third party to cryptographically verify that a gaming
session was performed by a human physically operating a controller — rather than a bot,
script, or software-injected input stream. We introduce **Proof of Autonomous Cognition
(PoAC)**, a 228-byte chained evidence record that binds sensor commitments, model
attestation, world-model state, and inference outputs into a single ECDSA-P256 signed
structure, forming a hash-linked chain anchored on-chain. Building on PoAC, we present
**VAPI** (Verified Autonomous Physical Intelligence), a complete verifiable gaming
integrity system with five core contributions: (1) the PoAC protocol itself — a compact,
fixed-format evidence record capturing not just *what* a device sensed but the complete
cognitive context of *why* it acted; (2) a six-level Physical Input Trust Layer (PITL)
combining HID-XInput pipeline monitoring, behavioral ML classification, biometric
kinematic fingerprinting, and temporal rhythm analysis; (3) an on-chain credential
system — PHGCredential (soulbound, non-transferable) — whose validity is maintained
through continuous behavioral surveillance, provisional suspension on repeated critical
labels, and automatic reinstatement; (4) an adaptive feedback loop where retrospective
behavioral intelligence directly tightens forward detection thresholds for known-adversarial
devices; and (5) a federated cross-instance threat correlation system enabling bot farm
detection across distributed bridge shards.

The primary certified device is the **DualShock Edge** (Sony CFI-ZCP1), whose motorized
L2/R2 adaptive trigger surface creates an unforgeable biometric detection boundary:
software injection cannot replicate the resistance dynamics that a physical human hand
produces, making a PoAC chain anchored to trigger dynamics and IMU readings unforgeable
without the physical human. A secondary IoTeX Pebble Tracker integration demonstrates
protocol extensibility to DePIN environmental monitoring without any protocol changes.

Our prototype spans ~220 files across Solidity contracts, a Python asyncio bridge service,
a self-verifying SDK, and a controller anti-cheat subsystem (~1,200 automated tests:
28 hardware suite, 352 Hardhat, 771 bridge+SDK pytest). On synthetic test patterns, the
six-class heuristic anti-cheat classifier achieves 100% separation with 0% false
positives. Live hardware validation on a physical DualShock Edge (Sony CFI-ZCP1) confirms
1002 Hz USB polling, gyro noise floor of 201 LSB (10,000× above the software-injection
detection threshold of 0.02 LSB), accel magnitude variance of 278,239 LSB² from natural
hand micro-tremor, and zero report-counter violations across 200 consecutive reports.
Validation with real adversarial gameplay data remains future work. Batch on-chain
verification costs ~81,000 gas per record via IoTeX's native P256 precompile. VAPI
establishes the first end-to-end framework where physical human gaming sessions are
cryptographically attested and verifiable on a public blockchain without trusting any
intermediary.

**Keywords:** proof of cognition, gaming anti-cheat, verifiable gaming intelligence,
physical human controller input, PHCI certification, adaptive trigger attestation,
federated threat detection, on-chain verification

**ACM CCS Concepts:** Security and privacy → Authentication; Computer systems
organization → Embedded and cyber-physical systems; Computing methodologies →
Multi-agent systems

---

## 1. Introduction

Competitive online gaming operates without any cryptographic guarantee that the person
pressing the buttons is a human. Game servers trust client-reported inputs; anti-cheat
software (Easy Anti-Cheat, BattlEye, Vanguard) detects known cheat signatures at the
kernel level but cannot prove human operation — only the absence of *known* automated
tools. A sufficiently sophisticated bot can pass every existing client-side check by
injecting inputs directly into the HID driver stack, bypassing behavioral analysis
entirely. Professional esports competitions worth millions of dollars in prize money
have no cryptographic defense against this class of attack.

We identify the root gap as the **human-controller attestation problem**: given a player
who claims to have performed a gaming session on a physical controller, how can a
tournament operator — with no access to the player's hardware — establish cryptographic
confidence in this claim?

This paper makes five contributions toward solving this problem:

**1. Proof of Autonomous Cognition (PoAC).** A 228-byte evidence record that
cryptographically chains sensor commitments, model manifests, world-model hashes, and
inference results into a tamper-evident, hash-linked sequence. Each record attests not
just to *what* a device sensed but to *why* it acted — capturing the decision context
through a commitment to accumulated agent state (§4).

**2. Physical Input Trust Layer (PITL).** A six-level detection stack combining hard
structural checks (HID-XInput pipeline discrepancy, PoAC chain integrity) with adaptive
behavioral analysis (biometric Mahalanobis fingerprinting, temporal rhythm analysis,
behavioral archaeology, network correlation). Four of the six layers exploit signals that
software injection cannot replicate (§7.5.1, §7.5.2).

**3. PHG Humanity Credential.** A soulbound, non-transferable on-chain credential
(ERC-5192-inspired, `locked()=true`) whose validity reflects continuous behavioral
surveillance. Credentials are provisionally suspended when a device accumulates
consecutive critical behavioral windows and automatically reinstated when it clears —
making the credential a *living proof* of ongoing trustworthy behavior, not a one-time
certification (§7.5.4).

**4. Adaptive Detection Feedback Loop.** The first anti-cheat system where retrospective
behavioral memory directly drives forward detection policy: devices labeled `critical`
have their L4 Mahalanobis detection threshold tightened by 30% for subsequent sessions.
The loop is bounded (minimum multiplier floor 0.5×), reversible (label changes
auto-restore the threshold), and cryptographically bounded (the 228-byte PoAC wire
format is unchanged) (§7.5.5).

**5. Federated Cross-Instance Threat Correlation.** A privacy-preserving federation
protocol that exchanges cluster fingerprints (16-char SHA-256 hashes, non-reversible)
between independent bridge instances, enabling detection of bot farms that deliberately
distribute devices across shards to stay below each instance's local threshold (§7.5.6).

Together these contributions form **VAPI** (Verified Autonomous Physical Intelligence).
The primary certified device is the **DualShock Edge** (Sony CFI-ZCP1), whose motorized
adaptive trigger surface creates a detection boundary that software cannot cross. The
primary blockchain is **IoTeX L1**, leveraging its native P256 precompile for
gas-efficient ECDSA verification. A secondary IoTeX Pebble Tracker integration
(§7.5.9) validates protocol extensibility to DePIN sensor domains — the same 228-byte
wire format operates unchanged.

The remainder of this paper is organized as follows. §2 surveys related work. §3
formalizes the system model. §4 presents the PoAC protocol. §5 describes the agent
architecture. §6 introduces the DePIN economic layer. §7 details the implementation,
with §7.5 presenting the complete DualShock Edge anti-cheat subsystem organized
by conceptual layer rather than development chronology. §8 presents evaluation results.
§9 analyzes security and threat models. §10 discusses limitations and future directions.
§11 concludes.

---

## 2. Background and Related Work

### 2.1 Gaming Anti-Cheat: The Detection Gap

Existing anti-cheat systems (Easy Anti-Cheat, BattlEye, Vanguard) operate as kernel
drivers that scan for known cheat signatures in process memory and loaded modules.
They detect *what software is running* but cannot detect *whether inputs are human-generated*.
A driver-level HID injection attack — spoofing the USB report stream that the game
receives — defeats all signature-based detection because it operates below the HID
driver abstraction layer and produces reports indistinguishable from a real controller.

The literature on game bot detection focuses on behavioral analysis of input sequences [X]
and statistical anomaly detection over timing distributions [Y]. VAPI complements these
approaches by adding a cryptographic layer: behavioral signals are committed and
chain-linked, making their provenance verifiable to a third party who was not present
during the session.

### 2.2 Decentralized Physical Infrastructure Networks

DePIN protocols incentivize the deployment and operation of physical infrastructure
through token rewards. Helium [2] pioneered Proof of Coverage for wireless networks.
Hivemapper [3] rewards dashcam operators for contributing street-level imagery.
These systems verify *presence* or *data quality* but none verify the *cognitive process*
by which a device interprets its environment. VAPI's DePIN extensibility (§7.5.9)
demonstrates that PoAC fills this gap for environmental monitoring devices, but the
primary contribution of this paper is gaming anti-cheat.

### 2.3 Trusted Execution and Remote Attestation

ARM TrustZone [6] and Intel SGX [9] verify *what code is running* but not *what the
code perceives or decides*. PoAC complements platform attestation by extending the
trust chain from code identity to cognitive content — committing actual sensor readings,
model weights, accumulated state, and inference outputs.

### 2.4 Verifiable Computation and zkML

Verifiable computation systems [11] allow proving correct execution of arbitrary programs.
Recent zkML efforts [12, 13] apply zero-knowledge proofs to ML inference. While
theoretically powerful, zkML faces severe practical barriers on microcontrollers: proof
generation requires minutes for small models on desktop hardware. PoAC adopts a
pragmatic alternative: rather than proving *correct execution* of inference (zkML), we
prove *commitment to the complete cognitive context*, trading zero-knowledge guarantees
for real-time feasibility. VAPI does integrate a Groth16 ZK circuit for PITL session
proofs (§7.5.3), but at the session level (one proof per gaming session, not per
cognition cycle).

### 2.5 Autonomous Agent Architectures

The BDI model [16] decomposes agent reasoning into beliefs, desires, and intentions.
Subsumption architectures [17] layer reactive behaviors with priority-based arbitration.
VAPI's three-layer architecture (reflexive/deliberative/strategic) draws on both: the
world model encodes beliefs, bounty evaluation encodes desires, and PoAC records encode
intentions. Critically, VAPI operates entirely on-device with no cloud dependency for
core cognition.

---

## 3. System Model and Definitions

**Definition 1 (Embodied Agent).** An embodied agent $\mathcal{A}$ is a tuple
$(\mathcal{S}, \mathcal{M}, \mathcal{W}, \mathcal{D}, \mathcal{K})$ where $\mathcal{S}$
is a sensor suite, $\mathcal{M}$ is an inference model, $\mathcal{W}$ is a world model
(accumulated state), $\mathcal{D}$ is a decision function, and $\mathcal{K}$ is a
signing keypair.

**Definition 2 (Cognition Cycle).** A single cognition cycle $c_i$ at time $t_i$ consists of:
1. **Perception**: $s_i \leftarrow \text{sense}(\mathcal{S}, t_i)$
2. **Commitment**: $h_s \leftarrow H(s_i)$, $h_m \leftarrow H(\mathcal{M})$, $h_w \leftarrow H(\mathcal{W})$
3. **Inference**: $(y_i, p_i) \leftarrow \mathcal{M}(s_i)$
4. **Decision**: $a_i \leftarrow \mathcal{D}(y_i, p_i, \mathcal{W})$
5. **Attestation**: $\rho_i \leftarrow \text{sign}(\mathcal{K}, [h_{i-1} \| h_s \| h_m \| h_w \| y_i \| a_i \| \text{ctx}_i])$
6. **Update**: $\mathcal{W} \leftarrow \mathcal{W} \cup \{(s_i, y_i, a_i)\}$

**Definition 3 (PoAC Chain).** A PoAC chain for device $d$ is an ordered sequence
$\langle \rho_0, \rho_1, \ldots, \rho_n \rangle$ where $\rho_i.\text{prev\_hash} = H(\rho_{i-1})$
and $\rho_i.\text{ctr} > \rho_{i-1}.\text{ctr}$, forming a hash-linked, monotonically-ordered
evidence log.

**Definition 4 (PHG Credential State).** A device's PHG credential is in one of three
states: `stable` (actively accumulating humanity evidence), `suspended` (credential
provisionally revoked due to consecutive critical behavioral windows), or `cleared`
(previously suspended, now exhibiting clean behavior). Transitions are driven by
`InsightSynthesizer` (§7.5.5) and recorded on-chain.

**Threat Model.** We consider an adversary who: (T1) fabricates PoAC records without
the device key; (T2) replays valid records out of order; (T3) selectively omits records
to hide unfavorable evidence; (T4) injects synthetic sensor data to a legitimate device;
(T5) claims rewards for work not performed; (T6) executes a warm-up attack — gradually
training a bot to produce human-like behavioral signals over many sessions;
(T7) distributes a bot farm across multiple bridge shards to stay below each instance's
local detection threshold. We assume the device hardware is not physically compromised
and that the blockchain provides standard finality guarantees.

---

## 4. The Proof of Autonomous Cognition Protocol

### 4.1 Record Structure

Each PoAC record is a fixed-size 228-byte structure: a 164-byte signed body and a
64-byte ECDSA-P256 signature. The fixed-size design eliminates parsing ambiguity, enables
zero-copy deserialization, and fits within a single NB-IoT uplink frame.

**Table 1: PoAC Record Wire Format (228 bytes, FROZEN)**

| Offset | Field | Size | Description |
|--------|-------|------|-------------|
| `0x00` | `prev_poac_hash` | 32 B | SHA-256 of previous record (genesis: `0x00...0`) |
| `0x20` | `sensor_commitment` | 32 B | $H(\text{raw\_sensor\_buffer})$ |
| `0x40` | `model_manifest_hash` | 32 B | $H(\text{weights} \| \text{version} \| \text{arch\_id})$ |
| `0x60` | `world_model_hash` | 32 B | $H(\mathcal{W})$ — agent state *before* update |
| `0x80` | `inference_result` | 1 B | Encoded classification output |
| `0x81` | `action_code` | 1 B | Agent action |
| `0x82` | `confidence` | 1 B | Model confidence $\in [0, 255]$ |
| `0x83` | `battery_pct` | 1 B | Remaining energy $\in [0, 100]$ |
| `0x84` | `monotonic_ctr` | 4 B | Strictly increasing counter (big-endian) |
| `0x88` | `timestamp_ms` | 8 B | Unix epoch milliseconds |
| `0x90` | `latitude` | 8 B | WGS84 latitude |
| `0x98` | `longitude` | 8 B | WGS84 longitude |
| `0xA0` | `bounty_id` | 4 B | On-chain bounty reference |
| `0xA4` | `signature` | 64 B | ECDSA-P256: $r \| s$ |

> **Important hashing distinction:**
> - `record_hash = SHA-256(raw[:164])` — the hash stored in `prev_poac_hash` of the *next* record
> - `chain_hash = SHA-256(raw[:228])` — the full-record hash for on-chain indexing
> These are different values and must not be confused.

The four 32-byte hash commitments (offsets `0x00`–`0x7F`) distinguish PoAC from simple
signed telemetry: they capture not just *what* was observed but the complete cognitive
context of *why* a particular action was chosen.

### 4.2 Chain Integrity

The `PoACVerifier` contract enforces four properties per submitted record:
- `submission.monotonicCtr > chainState.lastCounter` (ordering)
- `submission.prevPoACHash == chainState.lastRecordHash` (linkage)
- `|submission.timestampMs - block.timestamp × 1000| ≤ maxTimestampSkew` (freshness)
- Valid ECDSA-P256 signature over the 164-byte body (authenticity)

### 4.3 PITL Inference Codes

| Code | Name | Layer | Type |
|------|------|-------|------|
| `0x20` | NOMINAL | — | Normal human |
| `0x28` | DRIVER_INJECT | L2 | Hard cheat — blocks PHG, SkillOracle, tournament |
| `0x29` | WALLHACK_PREAIM | L3 | Hard cheat |
| `0x2A` | AIMBOT_BEHAVIORAL | L3 | Hard cheat |
| `0x2B` | TEMPORAL_ANOMALY | L5 | Advisory — committed but not blocking |
| `0x30` | BIOMETRIC_ANOMALY | L4 | Advisory — committed but not blocking |

Hard codes `{0x28, 0x29, 0x2A}` are rejected by `TeamProofAggregator` and trigger
a −200-point `SkillOracle` penalty. Advisory codes `{0x2B, 0x30}` accumulate as
on-chain statistical evidence without directly affecting rating or eligibility.

---

## 5. Agent Architecture

VAPI implements a three-layer cognitive architecture adapted for resource-constrained
gaming controller hardware.

### 5.1 Layer 1: Reflexive (Period: 1 ms at 1 kHz gaming input rate)

The reflexive layer executes the core sense-infer-attest loop. For the DualShock Edge
subsystem: capture a 50-byte `InputSnapshot`, extract a 30-dimensional feature vector
(stick positions, velocities, accelerations, trigger values, button timing statistics,
IMU features, touchpad features, temporal features), run the INT8-quantized classifier
(30→64→32→6), generate a PoAC record, and submit to the batch queue.

**Detection thresholds** (invariant to adaptive policy):
- Hard cheat: confidence ≥ 180/255 (≈70%) triggers `CHEAT_ALERT` state
- Resolution: 10 consecutive clean windows required to exit `CHEAT_ALERT`

### 5.2 Layer 2: Deliberative (Period: 60 s)

Trend analysis, EWC world model updates, behavioral archaeology signals, and PHG
checkpoint commit decisions. If behavioral scores (warmup, burst) exceed thresholds,
the PHG score delta is multiplied by `max(0.0, 1.0 − warmup×0.8 − burst×0.5)` before
on-chain submission.

### 5.3 Layer 3: Strategic (Period: 5 min)

Bridge sync, session proof generation, ZK PITL proof submission. The autonomy guard
prevents external systems from disabling PoAC generation.

---

## 6. DePIN Economic Layer

The DePIN layer demonstrates PoAC extensibility to non-gaming sensor domains. Devices
autonomously evaluate, accept, and preempt environmental monitoring bounties using a
greedy knapsack optimizer (§6.3 of the original paper). Each economic decision is itself
a PoAC-attested record. This layer is not the primary contribution of this paper; see
Appendix A for complete details.

---

## 7. System Implementation

### 7.1 Overview

VAPI spans four implementation layers: firmware (C, Zephyr RTOS and ESP-IDF), smart
contracts (Solidity, Hardhat, IoTeX), a Python asyncio bridge service, and a DualShock
Edge controller anti-cheat subsystem. The prototype comprises ~220 files (~1,200+
automated tests total).

**Table 2: Implementation Component Summary**

| Component | Language | Key Dependencies |
|-----------|----------|-----------------|
| Firmware | C (Zephyr) | nRF Connect SDK, PSA Crypto, CryptoCell-310 |
| Controller | C (ESP-IDF) / Python | ESP-NN, TFLite Micro, pydualsense |
| Contracts | Solidity | OpenZeppelin, Hardhat, IoTeX P256 precompile |
| Bridge | Python | asyncio, Web3.py, aiomqtt, FastAPI, anthropic |
| SDK | Python + C99 | pytest, VAPISession, VAPIVerifier |

### 7.2 Smart Contract Stack

**Core verification:**
- `PoACVerifier` — P256 signature verification via IoTeX precompile `0x0100`, batch
  verification up to 10 records, chain state enforcement
- `TieredDeviceRegistry` — device identity, staking, reputation
  ($R = R_{\text{raw}} \times 10000 / (R_{\text{raw}} + 1000)$ with diminishing returns)

**Gaming intelligence:**
- `PHGRegistry` — PHG score checkpoints; `commitCheckpoint(deviceId, scoreDelta, count, biometricHash)`
- `PHGCredential` — soulbound credential; `suspend(bytes32,bytes32,uint256)`, `reinstate(bytes32)`, `isActive(bytes32)`
- `TournamentGateV3` — wraps V2's cumulative+velocity gates with `phgCredential.isActive()` as a third condition
- `SkillOracle` — ELO-inspired on-chain skill rating; NOMINAL/SKILLED increment, cheat −200 penalty
- `ProgressAttestation` — verifiable proof of improvement between two PoAC records
- `TeamProofAggregator` — Merkle-root team attestation; rejects records with hard cheat codes
- `PITLSessionRegistry` — ZK PITL session proofs; anti-replay via `usedNullifiers`

**Federation:**
- `FederatedThreatRegistry` — immutable on-chain anchor for cross-confirmed cluster hashes

### 7.3 Bridge Service

The bridge is a Python asyncio application providing three transport ingestion channels
(MQTT, CoAP, HTTP), a batch accumulator (bounded `asyncio.Queue(maxsize=1000)`), six
intelligence background tasks, and two API sub-applications.

**Background tasks:**
1. `ChainReconciler` — confirms PHG checkpoint receipts every 30 s
2. `ProactiveMonitor` — bot-farm cluster + high-risk trajectory + eligibility surveillance every 60 s
3. `FederationBus` — peer cluster fingerprint exchange every 120 s
4. `InsightSynthesizer` — longitudinal digests + device trajectory labels + detection policies every 6 h
5. `AlertRouter` — webhook dispatch for enforcement events every 30 s
6. `BridgeAgent` — LLM-powered operator intelligence (on-demand, not polled)

---

## 7.5 DualShock Edge: Proof of Human Gaming

### 7.5.1 The Adaptive Trigger Detection Surface

The DualShock Edge (CFI-ZCP1) is the production PHCI (Physical Human Controller Input)
certified device. Its defining hardware feature is the motorized L2/R2 adaptive trigger
system, generating programmable resistance force profiles at approximately 200 Hz.

Unlike every other controller sensor — stick axes, gyroscope, accelerometer, buttons —
the trigger resistance dynamics emerge from a mechanical actuator whose behavior is
governed by physical laws (spring constants, friction, thermal noise, electromagnetic
hysteresis) that software cannot replicate or inject. A cheating player using
software-injected inputs can reproduce stick trajectories and button timings, but cannot
reproduce the micro-jitter, onset velocity profile, and grip-force ratio that a real
human hand produces against a real adaptive trigger.

**Sensor commitment schema v2** (kinematic/haptic) commits into every PoAC record's
32-byte `sensor_commitment` field:
- Left/right stick axes (4 × int16)
- Trigger depression values (2 × uint8)
- **Trigger resistance effect mode** (2 × uint8) — unforgeable; read from controller
  ADC, not writable from host HID stack
- Gyroscope (3 × int16)
- Accelerometer (3 × int16)
- Timestamp (int64)

Any software injection that fails to reproduce physical resistance dynamics produces a
measurably different sensor commitment, breaking hash-chain consistency.

### 7.5.2 Physical Input Trust Layer

VAPI implements a six-level detection stack. Each level is independent; detections at
any level produce PITL inference codes committed into the PoAC record.

**Table 3: PITL Architecture**

| Layer | Module | Code | Type | Signal |
|-------|--------|------|------|--------|
| L0 | Physical presence | — | Structural | Controller must be HID-connected, live input |
| L1 | PoAC chain integrity | — | Structural | SHA-256 linkage, monotonic counter, timestamp freshness |
| L2 | `hid_xinput_oracle.py` | `0x28` | Hard cheat | HID report vs. XInput API discrepancy |
| L3 | `tinyml_backend_cheat.py` | `0x29`, `0x2A` | Hard cheat | 9-feature temporal behavioral analysis (30→64→32→6 INT8 net) |
| L4 | `tinyml_biometric_fusion.py` | `0x30` | Advisory | 7-signal Mahalanobis kinematic fingerprint vs. per-device stable EMA |
| L5 | `temporal_rhythm_oracle.py` | `0x2B` | Advisory | CV < 0.08, Shannon entropy < 1.5 bits, 60 Hz quantization > 0.55; fires on ≥ 2/3 |

**L2 — HID injection detection.**
Software injection attacks (SendInput, XInput emulation, vJoy, DS4Windows spoofing)
cannot produce physical IMU readings. The oracle fires `0x28` when:
```
imu_noise = std(gyro) over 50 reports < 0.001 rad/s
AND
stick_magnitude > 0.15 of full range
```
The 0.001 rad/s threshold is below any real controller's noise floor (typically
0.01–0.05 rad/s at rest). A physical controller always exceeds this floor due to hand
micro-tremors. **Live measurement on DualShock Edge CFI-ZCP1:** stationary gyro std
< 50 LSB (≈ 0.05 rad/s, confirmed via `test_imu_noise_floor`); active play gyro std =
201 LSB (≈ 0.22 rad/s) — a **10,000× margin** above the 0.02 LSB (0.001 rad/s)
injection threshold.

**L3 — Behavioral ML.**
The 9-feature temporal classifier (velocity-stop events, jerk-correction lag,
aim-settling variance, button timing σ², stick autocorrelation, reaction-time proxy)
targets `MACRO` (σ² < 1.0 ms²) and `AIMBOT` (ballistic jerk > 2.0) patterns that
survive the L2 IMU check.

**L4 — Biometric Mahalanobis fingerprinting.**
Seven kinematic features per 50-report window (trigger onset velocity, micro-tremor
variance, grip asymmetry, stick autocorrelation, accel magnitude mean, trigger release
deceleration, IMU-stick correlation) are compared against a per-device *stable EMA
baseline* — updated only on clean NOMINAL sessions to prevent fingerprint poisoning.
The stable-vs-candidate architecture is the key security property: an adversary who
gradually shifts the EMA over many borderline sessions cannot poison the stable
reference.

`fingerprint_drift_velocity` (L2 norm between candidate and stable means) is an
additional contamination signal surface-able via the `BridgeAgent` tool
`get_behavioral_report`.

**L5 — Temporal rhythm oracle.**
Bot scripts produce near-constant inter-press intervals. The oracle characterizes
the inter-event timing distribution over a 120-event deque (min 20 samples) and fires
`0x2B TEMPORAL_ANOMALY` when ≥ 2 of 3 signals are suspicious:
- Coefficient of variation (CV) < 0.08 — near-zero timing variance
- Shannon entropy < 1.5 bits — few distinct interval values
- 60 Hz quantization score > 0.55 — intervals cluster at 16.67 ms multiples

`rhythm_humanity_score = (cv_humanity + entropy_score + non_quant) / 3.0 ∈ [0,1]`
contributes to the PHG humanity probability as a positive signal for high-variance,
high-entropy timing.

### 7.5.3 Zero-Knowledge PITL Session Proof

The bridge generates a Groth16 proof (BN254, ~1,820 constraints, 2^11 powers-of-tau)
at session shutdown, proving honest execution of the biometric pipeline without
revealing raw sensor features on-chain:

**Public inputs (5):**
- `featureCommitment` — Poseidon(7)(scaledFeatures[0..6])
- `humanityProbInt` — humanity_prob × 1000 ∈ [0, 1000]
- `inferenceResult` — 8-bit inference code
- `nullifierHash` — Poseidon(deviceIdHash, epoch) — anti-replay binding
- `epoch` — block.number / EPOCH_BLOCKS

**Circuit constraints:**
- C1: featureCommitment = Poseidon of exactly the 7 secret L4 features
- C2: inferenceResult ∉ [40, 42] (not in hard cheat range)
- C3: humanityProbInt ∈ [0, 1000]
- C4: nullifierHash = Poseidon(deviceIdHash, epoch) — session uniqueness
- C5: L5 rhythm score ∈ [0, 1000]

`PITLSessionRegistry.sol` accepts 256-byte proofs, enforces `usedNullifiers` anti-replay,
and tracks per-device `latestHumanityProb` and `sessionCount`. In mock mode
(`pitlVerifier = address(0)`), all invariants except the ZK proof are validated,
enabling production operation before the trusted setup ceremony completes.

### 7.5.4 PHG Credential and Economic Enforcement

**PHG humanity probability fusion.**
Per session, three signals are fused into `humanity_probability ∈ [0,1]`:
```
p_L4 = exp(−max(0, d_L4 − 2.0))   # biometric match
p_L5 = rhythm_humanity_score        # timing humanity
p_E4 = exp(−drift / 3.0)           # cognitive stability
humanity_probability = 0.4·p_L4 + 0.4·p_L5 + 0.2·p_E4
```

**PHG score weighting.**
PHG score deltas are weighted by `humanity_probability` (+50% bonus at p=1.0) and by
behavioral analysis: `delta × max(0.0, 1.0 − warmup×0.8 − burst×0.5)`. This makes
the on-chain PHG score reflect *quality* of human activity, not merely volume.

**On-chain credential lifecycle.**
`PHGCredential.sol` (soulbound, ERC-5192-inspired):
- Minted: `mintCredential(deviceId, nullifierHash, featureCommitment, humanityProbInt)`
  when a device has both a PHG checkpoint and a PITL session proof
- Active: credential contributes to `TournamentGateV3.isEligible()`
- Suspended: `suspend(deviceId, evidenceHash, durationSeconds)` — provisional
  revocation by bridge when `InsightSynthesizer` Mode 5 fires
- Reinstated: `reinstate(deviceId)` — automatic when device is labeled `cleared`

**PHGCredential suspension (Mode 5 enforcement).**
`InsightSynthesizer._synthesize_credential_enforcement()` runs each 6-hour cycle:
- For each device labeled `critical`: increment `consecutive_critical` counter
- If `consecutive_critical ≥ 2` AND has active credential AND not already suspended:
  compute suspension duration = `min(base_s × 2^(consecutive−2), max_s)`
  where `base_s = 604800` (7d), `max_s = 2419200` (28d)
- Call `chain.suspend_phg_credential(device_id, evidence_hash, duration_s)`
- Evidence hash = `SHA-256(f"{device_id}:{digest_id}")` — anchored to immutable digest row
- For each device labeled `cleared`: call `chain.reinstate_phg_credential()`
- For each device labeled `stable` or `warming`: reset `consecutive_critical` to 0

**Tournament gate hierarchy:**
- `TournamentGateV1` — minimum PHG cumulative score only
- `TournamentGateV2` — cumulative score AND recent velocity (time-qualified)
- `TournamentGateV3` — V2 gates AND `PHGCredential.isActive()` (suspension-aware)

**Biometric-anchored session continuity.**
A player who gets a new controller inherits their PHG history if:
`diagonal_mahalanobis(old_fingerprint, new_fingerprint) < 2.0`
(the continuity threshold is tighter than the 3.0 anomaly threshold). The PHG score
is transferred on-chain via `PHGRegistry.inheritScore()`; the source is zeroed to
prevent double-counting. Each device can be a continuity source and destination
exactly once.

### 7.5.5 Longitudinal Intelligence and Adaptive Feedback

**Behavioral archaeology.**
`BehavioralArchaeologist` performs longitudinal analysis of per-session PITL readings
using pure numpy (no LLM, always available):

*Warm-up attack detection:*
```
warmup_score = σ(drift_slope × humanity_slope × 20000 − 1.0)
```
where σ is sigmoid and slopes are computed by least-squares regression over record index
(invariant to adversarial timestamp manipulation). Stable human players:
warmup_score < 0.3. Coordinated warm-up (rising drift + rising humanity over sessions):
warmup_score > 0.7.

*Burst farming detection:*
```
burst_score = min(1.0, CV_inter_checkpoint / 2.0)
```
High gap-CV indicates bursty PHG checkpoint accumulation followed by inactivity.

*Biometric certificates (long-term):*
- `biometric_stability_cert`: mean drift velocity < 0.5 over last 20 sessions
- `l4_consistency_cert`: L4 distance CV < 0.3 over last 20 sessions

**Network correlation detection.**
`NetworkCorrelationDetector` builds an N×N pairwise biometric distance matrix across
all fingerprinted devices, then applies BFS DBSCAN (ε = 1.0, min_samples = 3).
Devices in clusters where `avg_intra_distance < ε/2` are flagged as potential bot farms:
genuine human players have idiosyncratic kinematic profiles; bots running the same
automation software cluster tightly in biometric space.

`farm_suspicion_score = min(1.0, (size−2)/5 + (ε−avg_d)/ε)`

**InsightSynthesizer (6-hour cycle).**
Four synthesis modes run independently each cycle:
- **Mode 1**: Rolling 24h/7d/30d window digests (bot_farm_count, high_risk_count,
  federated_count, anomaly_count, dominant_severity, top_5_devices, narrative text)
- **Mode 2**: Per-device risk trajectory labels via deterministic state machine:
  `_risk_label(bot, high_risk, fed, anomaly, prior) → {stable, warming, critical, cleared}`
- **Mode 3**: Federation topology fingerprints for clusters confirmed across ≥2 bridge instances
- **Mode 4**: Detection policy synthesis — translates risk labels into L4 threshold multipliers:
  `{critical: 0.70, warming: 0.85, stable: 1.00, cleared: 1.00}`
- **Mode 5**: PHGCredential enforcement (suspension / reinstatement) as described in §7.5.4

**Adaptive L4 feedback loop.**
After `classify()` returns None (no hard cheat), the bridge checks the device's active
detection policy:
```python
if multiplier < 1.0:
    effective_thresh = 3.0 * multiplier
    if last_distance > effective_thresh:
        synthesize(0x30, confidence=min(255, 180 + int(excess*30)))
```
The loop is bounded (floor multiplier = 0.5 → max tightening 50%), reversible (label
change on next synthesis cycle auto-restores threshold), and non-fatal (policy lookup
always wrapped in bare `except Exception: pass`). Hard cheat codes `0x28/0x29/0x2A`
are never affected by this mechanism.

### 7.5.6 Federated Cross-Instance Threat Correlation

Bot farms that distribute devices across multiple bridge shards stay below each
instance's local detection threshold. `FederationBus` counters this via privacy-preserving
cluster fingerprint exchange.

**Privacy model:**
```
cluster_hash = SHA-256("|".join(sorted(device_ids)))[:16]
bridge_id = SHA-256(f"bridge:{api_key}")[:16]
```
Raw device IDs never leave the originating bridge. The `GET /federation/clusters`
endpoint returns only `is_local=True` records, preventing echo amplification in
hub-and-spoke topologies.

**Cross-confirmation:**
```sql
SELECT cluster_hash, COUNT(DISTINCT bridge_id) AS peer_count
FROM federation_registry
GROUP BY cluster_hash
HAVING peer_count >= 2
```
A single misbehaving bridge cannot inflate the count by using `COUNT(DISTINCT bridge_id)`
rather than `COUNT(peer_url)`.

**On-chain anchor:** `FederatedThreatRegistry.sol` stores cross-confirmed hashes
immutably. `MultiVenueConfirmed` is emitted at `_reportCount ≥ 2`; anti-replay via
`_hasReported[clusterHash][reporter]`.

### 7.5.7 BridgeAgent: Operator Intelligence Interface

The first LLM-based agent in the VAPI stack operates at the operator query layer,
where human-paced interaction makes LLM latency acceptable and natural-language
synthesis is genuinely valuable. The agent uses `claude-sonnet-4-6` and maintains
18 deterministic tool bindings over bridge data sources.

**Design rationale.** All 15+ prior VAPI detection agents use deterministic methods
(Mahalanobis distance, DBSCAN, sigmoid regression, EWC gradient, SGD) — appropriate for
the high-frequency 1–10 Hz verification pipeline where latency and auditability are
paramount. `BridgeAgent` operates only at the operator query layer.

**18 tools (Phases 30–37):**

| Tool | Returns |
|------|---------|
| `get_player_profile` | PHG score, checkpoint count, risk label, credential status |
| `get_leaderboard` | Top-N devices by confirmed PHG score |
| `get_leaderboard_rank` | Single device rank |
| `run_pitl_calibration` | L4/L5 threshold suggestions from DB distribution |
| `get_continuity_chain` | Session continuity attestation history |
| `get_recent_records` | Last N PoAC records with PITL inference codes |
| `get_startup_diagnostics` | ZK artifact presence, contract addresses, feature flags |
| `get_phg_checkpoints` | Full PHG checkpoint chain |
| `check_eligibility` | Tournament eligibility (PHG score + credential status) |
| `get_pitl_proof` | Latest ZK PITL session proof |
| `get_behavioral_report` | BehavioralArchaeologist analysis (drift slope, warmup, burst) |
| `get_network_clusters` | NetworkCorrelationDetector clusters filtered by suspicion |
| `get_federation_status` | Peer count, cross-confirmed clusters, federation enabled |
| `query_digest` | InsightSynthesizer digest for a time window |
| `get_detection_policy` | Active L4 threshold multiplier and basis label |
| `get_credential_status` | Complete evidence chain: biometric → label → suspension |
| `get_recent_insights` | Last N protocol_insights rows |
| `get_schema_version` | DB migration phase |

**Streaming interface.** `GET /operator/agent/stream` returns Server-Sent Events with
typed events: `text_delta`, `tool_start`, `tool_result`, `done`, `error`. Operators
see reasoning token-by-token with visible tool invocations — breaking the black-box
perception of LLM responses.

**Autonomous reaction.** `react(event: dict)` autonomously interprets `BIOMETRIC_ANOMALY`
and `TEMPORAL_ANOMALY` events without operator input. Uses an internal session namespace
(`__react_{device_id[:8]}`), never raises, always returns `{alert, severity, tools_used}`.
Each reaction is persisted to `protocol_insights` as an auditable record.

**Session persistence.** Session history is stored in SQLite `agent_sessions` table,
surviving bridge restarts. History is trimmed when it exceeds `AGENT_MAX_HISTORY_BEFORE_COMPRESS`
(default 60) messages: a summary entry replaces the compressed portion with a tool-use
inventory extracted from the compressed messages.

### 7.5.8 Alert Dispatch

`AlertRouter` polls `protocol_insights` every 30 seconds and dispatches events meeting
the configured severity threshold to an operator webhook. Zero new dependencies:
dispatch uses stdlib `urllib.request.urlopen` in a thread executor. All dispatch
failures are non-fatal and logged as warnings.

Supported formats: `slack` (Incoming Webhook), `pagerduty` (Events API v2), `generic`
(plain JSON). The severity filter (`ALERT_SEVERITY_THRESHOLD`) prevents low-signal
noise from paging operators at 3 AM.

### 7.5.9 DePIN Extensibility Validation

The IoTeX Pebble Tracker (nRF9160 SiP, ARM Cortex-M33 @ 64 MHz, CryptoCell-310)
validates protocol extensibility. The same 228-byte PoAC wire format, the same three-layer
agent architecture, and the same on-chain contract stack operate unchanged. Only the
sensor commitment schema differs (schema v1, environmental: BME680 temperature/VOC,
ICM-42605 IMU, TSL2572 lux, GPS) versus the DualShock Edge (schema v2, kinematic/haptic).

This confirms VAPI's core design claim: the verification mechanism is device-agnostic;
the detection surface is device-specific.

---

## 8. Evaluation

### 8.1 Cryptographic Overhead

**Table 4: Per-Operation Latency on CryptoCell-310 (nRF9160)**

| Operation | Latency |
|-----------|---------|
| SHA-256 (96 B sensor buffer) | 0.8 ms |
| SHA-256 (1.5 KB world model) | 2.1 ms |
| SHA-256 (model manifest) | 1.4 ms |
| ECDSA-P256 sign | 6.2 ms |
| NVS write (36 B) | 1.8 ms |
| **Total PoAC generation** | **12.3 ms** |

These figures are estimated via cycle-accurate emulation. Real-hardware validation
on a physical nRF9160 DK is future work. The 12.3 ms total overhead is 0.041% of
the DePIN 30-second reflexive cycle budget.

### 8.2 On-Chain Gas Costs

| Operation | Gas per record |
|-----------|---------------|
| `verifyPoAC` (individual) | 148,230 |
| `verifyPoACBatch` (batch of 10) | 81,245 |
| `PHGRegistry.commitCheckpoint` | ~72,000 |
| `PHGCredential.mintCredential` | ~110,000 |
| `PHGCredential.suspend` | ~85,000 |
| `FederatedThreatRegistry.reportCluster` | ~65,000 |

At IoTeX's typical gas price of 1 Gwei and IOTX at $0.03, a batch of 10 verifications
costs approximately $0.00024. The P256 precompile at `0x0100` is essential: without it,
P256 verification in pure Solidity requires ~1.2M gas per signature.

### 8.3 Anti-Cheat Detection (Synthetic Test Patterns)

**WARNING: All figures in this section are derived from synthetic test data. No
claims about real-world detection accuracy should be drawn from these results.
Real-hardware validation with labeled adversarial gameplay data is required before
production deployment. All figures below are "on synthetic test patterns" only.**

**Table 5: Heuristic Classifier Detection — SYNTHETIC DATA ONLY**

| Input Pattern | Expected Class | Detection Rate | False Positive Rate |
|---------------|---------------|----------------|---------------------|
| Normal human gameplay | NOMINAL | 100% | 0% |
| Skilled human gameplay | NOMINAL/SKILLED | 100% | 0% |
| Macro (σ² < 1 ms²) | MACRO | 100% | 0% |
| Aimbot snap (jerk > 2.0) | AIMBOT | 100% | 0% |
| IMU mismatch (corr < 0.15) | IMU_MISS | 100% | 0% |
| Input injection (IMU noise < 0.001) | INJECTION | 100% | 0% |

**Table 6: L5 Temporal Rhythm Thresholds — SYNTHETIC DATA ONLY**

| Session Type | Expected CV | L5 Fires? |
|--------------|-------------|-----------|
| Macro (constant interval) | < 0.08 | Yes |
| Gold-tier human | > 0.15 | No |
| Diamond-tier human | > 0.08 | No |

Diamond-tier humans are consistent but physiologically cannot match macro-level
timing variance. If the CV threshold were set below 0.08, diamond-tier players would
generate false positives — this is why 0.08 is chosen as the boundary.

**Table 7: L4 Biometric Anomaly — SYNTHETIC DATA ONLY**

| Scenario | Expected Mahalanobis d | L4 Fires? |
|----------|----------------------|-----------|
| Same human, different session | 1.0–2.5 | No (threshold 3.0) |
| Different human, same controller | 3.5–6.0 | Yes |
| Bot farm (identical profiles) | < 0.5 per pair | Cluster flagged |

**Network correlation (synthetic):** 10 devices with identical biometric profiles
(distance=0) form a single cluster with `farm_suspicion_score = 1.0` and `is_flagged=True`.

### 8.4 DualShock Edge Hardware Validation

28 hardware tests run on a physical Sony DualSense Edge (CFI-ZCP1) in five suites
provide real-device empirical data for the key PITL detection surfaces. The test session
used a CFI-ZCP1 connected via USB-C to a Windows 11 machine; `hidapi` enumerated the
device at VID=0x054C, PID=0x0DF2, interface 3 (usage\_page=1, usage=5).

**Table 8: Live Hardware Measurements — DualShock Edge CFI-ZCP1 (USB mode)**

| Measurement | Value | Spec / Expected | Test |
|-------------|-------|----------------|------|
| USB polling rate | **1002 Hz** | 1000 Hz ± 15% | `test_1_polling_rate_1khz` |
| Accel magnitude (stationary, 1g ref) | **8267 LSB** | ~8192 LSB (1g) | `test_7_micro_tremor_accel_variance_present` |
| Accel magnitude variance (held, natural grip) | **278,239 LSB²** | > 0 (injects: 0) | `test_7_micro_tremor_accel_variance_present` |
| Gyro noise std (active play) | **201.65 LSB** | > 0.02 LSB threshold | `test_5_imu_stick_coupling_nonzero` |
| Gyro noise std (stationary) | **< 50 LSB** | < 50 LSB (pass) | `test_imu_noise_floor` |
| Injection detection margin | **10,000×** above threshold | — | Derived |
| Report counter violations | **0 / 200 reports** | 0 | `test_2_report_counter_monotonic` |
| Sensor commitment v2 (SHA-256) | **Deterministic** | Required | `test_5_sensor_commitment_v2_preimage` |
| Distinct commitments (distinct reports) | **4 / 4** | All distinct | `test_5_sensor_commitment_v2_preimage` |
| Timestamp field (bytes 12–14) | **49/49 advancing** | > 80% | `test_4_timestamp_field_advances` |

The **10,000× injection detection margin** is the critical result: software injection
attacks produce gyro std ≈ 0 LSB (no physical IMU); the physical device at rest produces
< 50 LSB and in active play produces > 200 LSB. Any threshold between 0.02 LSB and
50 LSB provides reliable separation with zero false positives on real hardware.

The **278,239 LSB² accel variance** measured during normal hand-held use (no deliberate
motion) demonstrates that the micro-tremor signal exists at meaningful amplitude in
natural play conditions — not merely during controlled vibration or aggressive movement.
This validates micro-tremor as a practical biometric feature for everyday detection, not
a laboratory artifact.

Report-counter monotonicity confirms 200 consecutive reports with zero gaps or violations
on Windows 11 USBHID. The polling rate of 1002 Hz confirms 1 kHz resolution for the
50-report feature extraction window assumed throughout the L4/L5 pipeline.

**Test suite structure (28 tests):**

| File | Tests | Type |
|------|-------|------|
| `test_dualshock_live.py` | 6 | HID enumeration, format, axes, IMU floor, commitment |
| `test_pitl_live.py` | 5 | PITL transport smoke (report volume, chain, features) |
| `test_dualshock_report_timing.py` | 5 | 1 kHz rate, counter, gaps, timestamp, wrap |
| `test_dualshock_biometric.py` | 7 | L4 fusion, stable-track quarantine, micro-tremor, IMU-stick coupling, trigger onset velocity |
| `test_dualshock_adaptive_triggers.py` | 5 | Trigger ADC range, effect byte readback, release return, independence, sensor\_commitment\_v2 |

All tests include embedded step-by-step physical procedures (timing guidance, action
prompts) so that operators without code expertise can execute the full hardware validation
protocol. Tests are gated behind `@pytest.mark.hardware` and excluded from CI by default.

**Remaining hardware validation gap.** The 28 tests validate the transport and physical
signal pipeline. They do not provide labeled adversarial gameplay data — bot trajectories,
injection tool streams, trained human-mimicking automation. Detection claims in Tables 5–7
remain synthetic only. Populating these tests with adversarial captures is the primary
gap between prototype and production-validated system (§10.1).

### 8.5 Test Coverage Summary

| Suite | Count | Scope |
|-------|-------|-------|
| Bridge pytest | 743 | Full pipeline (asyncio bridge, store, agent, enforcement, federation) |
| SDK pytest | 28 | Self-verifying client SDK |
| Hardhat | 352 | All Solidity contracts (17 contracts, 352 passing) |
| Hardware | 28 | Physical DualShock Edge (gated `@pytest.mark.hardware`, excluded from CI) |
| **Total** | **~1,151** | *Excludes 14 infrastructure-gated skips (9 E2E/Hardhat-node, 5 ZK-ceremony)* |

Note: 14 tests are skipped in normal CI: 9 end-to-end simulation tests require a
live Hardhat node, and 5 real-ZK prover tests require ceremony artifacts (`.zkey` files
generated by `npx hardhat run scripts/run-ceremony.js`).

---

## 9. Security and Threat Model Analysis

### 9.1 Threat Mitigations

**T1 — Record fabrication.** ECDSA-P256 private key resides in CryptoCell-310 PSA
persistent secure storage (key ID `0x00010001`), accessible only from the Secure partition.
The on-chain verifier checks signatures via the P256 precompile.

**T2 — Replay attacks.** Monotonic counter (persisted in NVS flash) strictly increases
across power cycles. `PoACVerifier` enforces `ctr > chainState.lastCounter`. ZK PITL
proofs use `nullifierHash = Poseidon(deviceIdHash, epoch)` — binding proofs to one
device × one epoch.

**T3 — Selective omission.** Hash-chain linkage makes omission detectable: a gap in
counter values signals omission even without the hash link. The bridge can withhold
records but cannot produce an alternative chain without the signing key.

**T4 — Synthetic sensor injection.** PoAC constrains but does not fully prevent this
attack. The adaptive trigger resistance state byte (unforgeable from host HID stack,
§7.5.1) narrows the viable injection surface. The L4 biometric stable-track fingerprint
and the L5 timing distribution provide behavioral constraints: an adversary must maintain
consistent synthetic input across a 32-observation world model, produce plausible
classification outputs, and sustain deception across the L4 stable-track update window
without triggering drift velocity alerts.

**T5 — Fraudulent bounty claims.** `submitEvidence()` requires a PoAC record hash
in `PoACVerifier.verifiedRecords`, location within geographic zone, and timestamp within
active period.

**T6 — Warm-up attack.** `BehavioralArchaeologist` detects rising drift-slope +
humanity-slope correlation via `σ(drift_slope × humanity_slope × 20000 − 1.0)`.
The sigmoid scoring penalizes coordinated improvement without penalizing natural skill
development (which does not produce the characteristic dual-rising slope). Even if a
warm-up attack succeeds in accumulating PHG score, Mode 5 enforcement (§7.5.4) can
suspend the credential when consecutive critical windows accumulate.

**T7 — Distributed bot farm.** `FederationBus` (§7.5.6) detects clusters confirmed
by ≥2 independent bridge instances. Even if each shard sees too few devices to form
a local cluster, the cross-confirmed hash triggers a `federated_cluster` alert and
optional on-chain anchoring.

### 9.2 Trust Assumptions

| Component | Trust Assumption | Failure Mode |
|-----------|-----------------|--------------|
| CryptoCell-310 | Hardware not physically tampered | Key extraction via side-channel |
| Sensor hardware | Not replaced or physically spoofed | Synthetic data injection (T4) |
| IoTeX L1 | Standard blockchain finality | Reorg reverts verifications |
| P256 precompile at `0x0100` | Correct implementation | Signature bypass |
| Bridge service | Honest relay | Withholding/reordering (detected via hash-chain) |
| Stable EMA track | Initialized from clean sessions | If early sessions are bot-generated, stable track is poisoned |

**Stable track initialization risk.** The L4 biometric anomaly detector computes
Mahalanobis distance against a *stable* EMA track updated only on clean NOMINAL
sessions. If a device's first 5 sessions are bot-generated but below hard-cheat
thresholds, the stable track is initialized from bot biometrics. Subsequent legitimate
human play would then trigger false positives while bot sessions appear normal.
**Mitigation:** the warmup attack detector (§7.5.5) specifically looks for rising
humanity-probability slope early in a device's lifecycle, flagging this bootstrapping
attack. The `_STABLE_TRACK_QUARANTINE` invariant comment in `dualshock_integration.py`
documents this attack surface explicitly.

### 9.3 Limitations

**All detection benchmarks are synthetic.** The 100% detection rates in Table 5 are
measured on synthetic test patterns generated by `tests/data/realistic_generators.py`.
Real-world adversarial gameplay data — professional bot software, trained human-mimicking
automation, hardware-level I²C spoofing — is required to validate real-world detection
rates. Real-hardware benchmarks are the primary gap between the current prototype and
a production-ready system.

**Biometric thresholds are uncalibrated.** L4 ANOMALY_THRESHOLD (3.0) and
CONTINUITY_THRESHOLD (2.0) are design-time estimates. The `pitl_calibration.py` tool
provides operators with empirical threshold suggestions from their deployment data, but
the defaults require validation across a diverse player population before production use.

**Bridge is a trusted intermediary.** The ZK PITL circuit (§7.5.3) constrains the
bridge's computation of biometric outputs, but the mock-mode default (`pitlVerifier = address(0)`)
means the ZK guarantee is inactive in most deployments until the trusted setup ceremony
is completed.

**No data confidentiality.** PoAC records are submitted in plaintext — inference results,
action codes, and locations are visible on-chain.

---

## 10. Discussion and Future Work

### 10.1 Real-Hardware Adversarial Validation

The most urgent gap is adversarial benchmarking on physical hardware. This requires:
- A labeled dataset of real bot software inputs (aimbot trajectories, macro timings,
  injected controller streams) captured via `scripts/capture_session.py`
- A labeled dataset of legitimate competitive play across skill tiers (Bronze through Diamond)
- Threshold calibration using `scripts/threshold_calibrator.py` on these datasets
- Re-validation of Tables 5–7 with the calibrated thresholds

The existing `tests/hardware/` suite and `tests/data/realistic_generators.py` provide
the test infrastructure; filling it with real adversarial data is the critical next step.

### 10.2 Toward Full-ZK PoAC

As zkML tooling matures, PoAC could incorporate succinct proofs of correct inference
execution per cognition cycle, upgrading model attestation from "the device claims to
have used model $\mathcal{M}$" to "the device provably executed $\mathcal{M}$ on input
$x$." The primary barrier is prover time on embedded hardware. Application-specific
circuits for fixed TinyML architectures and hardware ZK accelerators could make this
feasible within 3–5 years.

### 10.3 Multi-Instance Trusted Setup

The current ZK ceremony (`contracts/scripts/run-ceremony.js`) is a single-contributor
development ceremony. A production deployment requires a multi-party MPC ceremony
(Hermez Perpetual Powers of Tau or equivalent) to ensure that no single participant
can reconstruct the toxic waste.

### 10.4 Full Covariance Biometric Fingerprinting

The current L4 classifier uses a diagonal covariance assumption (7 independent variances).
A full 7×7 covariance matrix would capture cross-feature correlations (e.g., trigger
onset velocity is correlated with grip asymmetry for a given player) and improve
both sensitivity and specificity. The `controller/tinyml_biometric_fusion.py` TODO
comment documents this as the next algorithmic improvement.

### 10.5 Formal Verification

The PoAC chain integrity properties (linkage, monotonicity, non-repudiation) are
amenable to formal verification in TLA+ or Isabelle/HOL. Machine-checked proofs would
strengthen confidence for safety-critical esports deployments.

---

## 11. Conclusion

We have presented VAPI and the Proof of Autonomous Cognition protocol — the first system
providing end-to-end cryptographic verification that a gaming session was performed by a
physical human operator. PoAC's 228-byte chained evidence record captures the complete
cognitive context: what was sensed, what model produced the inference, what the agent's
accumulated world model contained at decision time, and what action was taken — all
committed, signed, hash-chained, and anchored on a public blockchain.

The DualShock Edge's adaptive trigger surface is the key physical primitive: a PoAC
chain anchored to motorized resistance dynamics, six-axis IMU, and stick kinematics is
unforgeable without the physical human. The six-level PITL stack — from HID pipeline
monitoring through biometric kinematic fingerprinting through temporal rhythm analysis —
provides layered redundancy, with each layer exploiting different aspects of the
human-controller physical coupling that software injection cannot replicate.

The PHG humanity credential becomes a *living* proof: earned through sustained clean
behavior, weighted by biometric quality, portable across key rotations through biometric
continuity, and provisionally suspended when the protocol's retrospective memory
accumulates evidence of sustained adversarial behavior across consecutive 7-day windows.
The suspension mechanism is bounded, reversible, evidence-anchored, and verifiable
on-chain — closing the final gap between intelligence that detects and intelligence that
acts.

The complete system (~220 files, ~1,200+ automated tests including 28 on physical
hardware) proves the concept is implementable today with existing gaming controller
hardware and existing blockchain infrastructure. Live hardware validation on a DualShock
Edge CFI-ZCP1 confirms the foundational physical signal claims: USB polling at 1002 Hz,
gyro noise 10,000× above software-injection threshold, 278,239 LSB² accel variance from
natural hand micro-tremor, and zero report-counter violations across 200 consecutive
reports. The primary remaining gap between this prototype and production deployment is
real-world adversarial benchmarking; the infrastructure to close that gap — hardware
capture scripts, threshold calibration tools, and a 28-test hardware validation suite
with step-by-step physical procedures — ships with this release.

VAPI opens a new design space: instead of trusting that players are human, we verify it.
Instead of opaque telemetry, we anchor transparent, chained, cryptographically-committed
evidence of physical human gaming sessions on a public blockchain. The ability to verify
not merely presence but *physical human operation* — anchored to the biomechanical
signals that only a human body produces — becomes foundational to trustless competitive
gaming.

---

## References

[1] Sami, H., et al. "Decentralized Physical Infrastructure Networks (DePIN): A Systematic Survey." *IEEE Communications Surveys & Tutorials*, vol. 26, no. 2, 2024.

[2] Haleem, A., et al. "Helium: A Decentralized Wireless Network." *Proc. ACM HotNets*, 2021.

[3] Hivemapper. "Hivemapper: A Decentralized Global Mapping Network." 2022.

[4] Fan, Q., et al. "IoTeX 2.0: The Network for DePIN." IoTeX Foundation Technical Report, 2024.

[5] Nakamoto, S. "Bitcoin: A Peer-to-Peer Electronic Cash System." 2008.

[6] Pinto, S. and Santos, N. "Demystifying ARM TrustZone: A Comprehensive Survey." *ACM Computing Surveys*, vol. 51, no. 6, 2019.

[7] Birkholz, H., et al. "Remote Attestation Procedures Architecture." IETF RFC 9334, 2023.

[8] DIMO. "DIMO: The Digital Infrastructure for Moving Objects." 2023.

[9] Costan, V. and Devadas, S. "Intel SGX Explained." *IACR Cryptology ePrint Archive*, 2016/086.

[10] Trusted Computing Group. "DICE Layered Architecture." TCG Specification, 2020.

[11] Groth, J. "On the Size of Pairing-Based Non-interactive Arguments." *Proc. EUROCRYPT*, 2016.

[12] Kang, D., et al. "Scaling up Trustless DNN Inference with Zero-Knowledge Proofs." *Proc. OSDI*, 2024.

[13] EZKL. "EZKL: Easy Zero-Knowledge Inference." https://ezkl.xyz, 2024.

[14] Breidenbach, L., et al. "Chainlink 2.0." Chainlink Whitepaper, 2021.

[15] McConaghy, T., et al. "Ocean Protocol." Ocean Protocol Foundation, 2020.

[16] Rao, A.S. and Georgeff, M.P. "BDI Agents: From Theory to Practice." *Proc. ICMAS*, 1995.

[17] Brooks, R.A. "A Robust Layered Control System for a Mobile Robot." *IEEE J. Robotics Autom.*, 1986.

[18] Yao, S., et al. "ReAct: Synergizing Reasoning and Acting in Language Models." *ICLR*, 2023.

---

## Appendix A: DePIN Economic Layer (Complete)

The DePIN layer is fully described in §5–6 of this paper's original version (see
`paper/vapi-whitepaper.md`). The greedy knapsack optimizer achieves 94.2% of optimal
reward (median 97.1%, worst-case 81.3%) across 1,000 synthetic scenarios in 0.14 ms
mean execution time on the nRF9160 (emulation). The preemption threshold (1.5×) fires
in 12.7% of scenarios. All DePIN evaluation figures are simulation-derived.
